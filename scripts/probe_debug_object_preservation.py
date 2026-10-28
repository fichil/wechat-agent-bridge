"""Owned synthetic targets with a pre-existing pause and nonzero debug state.

Fixture setup/teardown is separate from debugger recovery. No user process,
window, network or production eligibility; no PID/target command-line option.
"""
import argparse
import copy
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import struct
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import probe_debug_object_recovery as recovery
from scripts import probe_debugger_resume_flag as owned

CASES = ('existing_pause', 'existing_registers', 'existing_both')
DR6_SAMPLES = (0, 0xffff0ff0, 0xffff07f0, 0xf, 0x2000, 0x4000, 0x8000, 0x10000, 0x800, 0xffffe80f)


def source(pipe, counter, stop):
    kernel = recovery.kernel_api()
    event = recovery.checked(kernel.CreateEventW(None, True, False, None), 'fixture_event_create')
    ready = threading.Event()
    helper_info = {}
    # This storage stays alive and is not accessed after identity publication.
    # Slot 1 watches an aligned, never-written member. No executable code added.
    marker = (c.c_uint64 * 4)(101, 102, 103, 104)
    def auxiliary():
        helper_info['tid'] = kernel.GetCurrentThreadId()
        ready.set()
        helper_info['wait'] = kernel.WaitForSingleObject(event, 45000)
    helper = threading.Thread(target=auxiliary, daemon=True)
    helper.start()
    try:
        if not ready.wait(2):
            raise RuntimeError('fixture_auxiliary_start_timeout')
        identity = owned.self_identity(kernel)
        function = kernel.GetCurrentProcessId
        function.restype = c.c_uint32
        identity.update(main_tid=kernel.GetCurrentThreadId(), auxiliary_tid=helper_info['tid'],
                        marker_address=c.addressof(marker), address=c.cast(function, c.c_void_p).value)
        pipe.send(identity)
        until = time.monotonic() + 40
        while not stop.value and time.monotonic() < until:
            function(); counter.value += 1
            time.sleep(.01)
    finally:
        signalled = bool(kernel.SetEvent(event))
        helper.join(1)
        closed = bool(kernel.CloseHandle(event))
        pipe.send({'fixture_source_closed': signalled and closed and not helper.is_alive() and
                   helper_info.get('wait') == 0})
        pipe.close()


def seed_registers(marker_address):
    if type(marker_address) is not int or marker_address <= 0 or marker_address % 8:
        raise RuntimeError('fixture_marker_address_invalid')
    # Nonzero disabled slots 0/2/3; active slot 1 is write-only, length eight.
    return [marker_address, marker_address + 8, marker_address + 16, marker_address + 24,
            0, (9 << 20) | 4]


def registers_match_seed(actual, requested):
    return bool(isinstance(actual, list) and len(actual) == 6 and
                actual[:4] == requested[:4] and
                (actual[5] ^ requested[5]) & ~0x400 == 0 and
                actual[4] in (0, 0xffff07f0))


def check_seeded_states(before, current, identity, pause, registers):
    if not before or before.keys() != current.keys():
        raise RuntimeError('fixture_thread_set_changed')
    for tid, initial in before.items():
        expected = dict(initial)
        if pause and tid == str(identity['auxiliary_tid']):
            expected['prior_suspend_count'] += 1
        if registers and tid == str(identity['main_tid']):
            actual = current[tid]['debug_registers']
            if not registers_match_seed(actual, seed_registers(identity['marker_address'])):
                raise RuntimeError('fixture_seed_not_preserved')
            expected['debug_registers'] = actual
        if expected != current[tid]:
            raise RuntimeError('fixture_unintended_state_change')


def balanced_register_write(thread, expected, desired):
    """Fixture-only temporary pause; never decrements an unowned count."""
    thread.check()
    previous = thread.kernel.SuspendThread(thread.handle)
    if previous == 0xffffffff:
        raise RuntimeError('fixture_suspend_failed')
    try:
        if previous != 0:
            raise RuntimeError('fixture_main_already_paused')
        if thread.context()[2] != expected:
            raise RuntimeError('fixture_registers_changed_before_write')
        thread.set_registers(desired)
    finally:
        if thread.kernel.ResumeThread(thread.handle) != previous + 1:
            raise RuntimeError('fixture_temporary_pause_balance_changed')


class Fixture:
    source = staticmethod(source)

    def __init__(self, name):
        if name not in CASES:
            raise RuntimeError('fixture_case_invalid')
        self.name = name
        self.pause = name in ('existing_pause', 'existing_both')
        self.registers = name in ('existing_registers', 'existing_both')
        self.main = self.auxiliary = self.bound = None
        self.pause_owned = False
        self.before = self.baseline = None
        self.evidence = {}

    def prepare(self, bound, identity, initial, counter):
        self.bound, self.identity, self.before = bound, identity, copy.deepcopy(initial)
        if (identity['main_tid'] == identity['auxiliary_tid'] or
                str(identity['main_tid']) not in initial or str(identity['auxiliary_tid']) not in initial):
            raise RuntimeError('fixture_thread_identity_invalid')
        self.main = recovery.BoundThread(bound, identity['main_tid'],
            initial[str(identity['main_tid'])]['thread_created'], fixture_suspend=True)
        self.auxiliary = recovery.BoundThread(bound, identity['auxiliary_tid'],
            initial[str(identity['auxiliary_tid'])]['thread_created'], fixture_suspend=True)
        # Let the helper reach its native wait (no Python lock held), then
        # require a live main heartbeat after suspending the auxiliary.
        time.sleep(.1)
        if self.pause:
            self.auxiliary.check()
            prior = bound.kernel.SuspendThread(self.auxiliary.handle)
            if prior == 0xffffffff:
                raise RuntimeError('fixture_auxiliary_pause_failed')
            self.pause_owned = True
            if prior != 0:
                raise RuntimeError('fixture_auxiliary_already_paused')
        if self.registers:
            try:
                balanced_register_write(self.main, initial[str(identity['main_tid'])]['debug_registers'],
                                        seed_registers(identity['marker_address']))
            finally:
                self.evidence['setup_write'] = self.main.last_register_write
        if not owned.heartbeat(counter, bound, .3):
            raise RuntimeError('fixture_main_not_running')
        current = owned.process_state(bound)
        if self.registers:
            self.evidence.update(requested=seed_registers(identity['marker_address']),
                                 observed_before_attach=current[str(identity['main_tid'])]['debug_registers'])
        check_seeded_states(initial, current, identity, self.pause, self.registers)
        self.baseline = current
        return current

    def finish(self, restored):
        if self.baseline is None or restored != self.baseline:
            raise RuntimeError('fixture_cleanup_requires_exact_recovery')
        # Only after qualification: remove the artificial original state.
        # These operations are fixture teardown, not recovery of the debugger.
        if self.registers:
            balanced_register_write(self.main,
                restored[str(self.identity['main_tid'])]['debug_registers'],
                self.before[str(self.identity['main_tid'])]['debug_registers'])
        if self.pause_owned:
            self.auxiliary.check()
            prior = self.bound.kernel.ResumeThread(self.auxiliary.handle)
            self.pause_owned = False
            if prior != 1:
                raise RuntimeError('fixture_original_pause_count_changed')
        after = owned.process_state(self.bound)
        if after != self.before:
            raise RuntimeError('fixture_teardown_not_exact')
        return dict(original_state_preserved=True, seeded_state_removed=True, after_fixture=after)

    def close(self):
        errors = []
        for thread in (self.main, self.auxiliary):
            if thread is not None:
                try:
                    thread.close()
                except Exception as exc:
                    errors.append(owned.error_code(exc))
        if errors:
            raise RuntimeError('fixture_handles_not_closed')


def qualified(case):
    cleanup = case.get('fixture_cleanup', {})
    children = case.get('child_cleanup', [])
    return bool(case.get('passed') is True and case.get('initial_threads') == case.get('final_threads') and
        cleanup.get('original_state_preserved') is True and cleanup.get('seeded_state_removed') is True and
        cleanup.get('after_fixture') == case.get('before_fixture') and len(children) == 2 and
        children[1].get('exitcode') == 0 and children[1].get('terminated') is False and
        case.get('fixture_source_cleanup') == [{'fixture_source_closed': True}])


def fingerprints():
    result = recovery.fingerprints()
    result[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def calibration_qualified(result):
    rows = result.get('samples', [])
    return bool(result.get('state') == 'complete' and
        [row.get('requested_dr6') for row in rows] == list(DR6_SAMPLES) and
        all(row.get('observed_dr6') == row['requested_dr6'] & 0xe00f and
            row.get('other_registers_unchanged') is True for row in rows) and
        result.get('original_context_restored') is True and result.get('pause_balanced') is True and
        result.get('initial_threads') == result.get('final_threads') and result.get('stable_two_seconds') is True and
        result.get('source_cleanup') == [{'fixture_source_closed': True}] and
        result.get('child_cleanup', {}).get('exitcode') == 0 and
        result.get('child_cleanup', {}).get('terminated') is False and not result.get('cleanup_errors'))


def calibrate_registers(context):
    """Hold one owned thread stopped; restore it before its first resumption."""
    counter, stop = context.Value('L', 0, lock=False), context.Value('b', False, lock=False)
    read, write = context.Pipe()
    target = context.Process(target=source, args=(write, counter, stop))
    bound = thread = original = None
    suspended = False
    result = dict(state='started', samples=[], cleanup_errors=[])
    try:
        target.start(); write.close()
        identity = owned.receive(read, 5, 'calibration_source_timeout')
        bound = owned.BoundProcess(identity, target, recovery.kernel_api())
        result['initial_threads'] = owned.process_state(bound)
        owned.check_baseline(result['initial_threads'])
        thread = recovery.BoundThread(bound, identity['main_tid'],
            result['initial_threads'][str(identity['main_tid'])]['thread_created'], fixture_suspend=True)
        thread.check()
        prior = bound.kernel.SuspendThread(thread.handle)
        if prior == 0xffffffff:
            raise RuntimeError('calibration_suspend_failed')
        suspended = True
        if prior != 0:
            raise RuntimeError('calibration_unexpected_prior_suspend')
        original = thread.context()[2]
        for dr6 in DR6_SAMPLES:
            storage, pointer, _ = thread.context()
            desired = list(original)
            desired[4] = dr6
            c.memmove(pointer + 72, struct.pack('<6Q', *desired), 48)
            thread.check()
            recovery.checked(bound.kernel.SetThreadContext(thread.handle, pointer), 'calibration_context_write')
            observed = thread.context()[2]
            result['samples'].append(dict(requested_dr6=dr6, observed_dr6=observed[4],
                other_registers_unchanged=all(observed[i] == original[i] for i in (0, 1, 2, 3, 5))))
        thread.set_registers(original)
        result['original_context_restored'] = True
        suspended = False
        if bound.kernel.ResumeThread(thread.handle) != prior + 1:
            raise RuntimeError('calibration_pause_balance_changed')
        result['pause_balanced'] = True
        result['stable_two_seconds'] = owned.heartbeat(counter, bound)
        result['final_threads'] = owned.process_state(bound)
        result['state'] = 'complete'
    except BaseException as exc:
        result.update(state='failed', error=owned.error_code(exc))
    finally:
        if suspended:
            # Never run a partially changed target after calibration failure.
            result['failed_target_cleanup'] = owned.terminate_owned(target)
        stop.value = True
        if target.pid:
            target.join(.5)
        result['child_cleanup'] = owned.terminate_owned(target)
        result['source_cleanup'] = owned.drain(read)
        for port in (thread, bound):
            if port is not None:
                try:
                    port.close()
                except Exception as exc:
                    result['cleanup_errors'].append(owned.error_code(exc))
        read.close(); write.close()
        if target.pid and not target.is_alive():
            target.close()
    result['passed'] = calibration_qualified(result)
    return result


def run_cases(context):
    cases = []
    # Establish the modified harness still passes the original six zero cases.
    for stage in recovery.STAGES:
        for mode in recovery.retention.MODES:
            result = recovery.run_case(context, mode, stage)
            result['fixture'] = 'zero_baseline'
            cases.append(result)
            print(json.dumps({key: result.get(key) for key in ('fixture', 'stage', 'mode', 'passed', 'error')}), flush=True)
            if not result['passed']:
                return cases
    for name in CASES:
        for mode in recovery.retention.MODES:
            result = recovery.run_case(context, mode, 'breakpoint', fixture=Fixture(name))
            result['preservation_passed'] = qualified(result)
            cases.append(result)
            print(json.dumps({key: result.get(key) for key in (
                'fixture', 'stage', 'mode', 'passed', 'preservation_passed', 'error')}), flush=True)
            if not result['preservation_passed']:
                return cases
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--calibrate-registers', action='store_true')
    args = parser.parse_args(argv)
    if args.calibrate_registers and not args.run_synthetic:
        parser.error('--calibrate-registers requires --run-synthetic')
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', live_eligible=False)))
        return 0
    hashes = fingerprints()
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8 or any(
            hashes.get(name) != value for name, value in recovery.retention.SYSTEM_SHA.items()):
        raise RuntimeError('preservation_platform_or_fingerprint_mismatch')
    if args.calibrate_registers:
        result = calibrate_registers(mp.get_context('spawn'))
        report = dict(kind='owned_dr6_context_roundtrip', result=result, fingerprints=hashes,
                      source_unchanged=hashes == fingerprints(), os=platform.version(), live_eligible=False)
        report['passed'] = result['passed'] and report['source_unchanged']
        path = ROOT / '.local/live-acceptance' / ('debug-register-roundtrip-' + uuid.uuid4().hex + '.json')
        with path.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
        print(json.dumps(dict(path=str(path), passed=report['passed'], error=result.get('error'), live_eligible=False)))
        return 0 if report['passed'] else 1
    cases = run_cases(mp.get_context('spawn'))
    report = dict(kind='owned_debug_object_original_state', fingerprints=hashes, cases=cases,
                  source_unchanged=hashes == fingerprints(), os=platform.version(),
                  dbgeng_owner_failure_recovered=False, live_eligible=False)
    report['passed'] = (len(cases) == 15 and report['source_unchanged'] and
        all(row['passed'] and (row['fixture'] == 'zero_baseline' or row.get('preservation_passed')) for row in cases))
    path = ROOT / '.local/live-acceptance' / ('debug-object-preservation-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(path), passed=report['passed'], live_eligible=False)))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
