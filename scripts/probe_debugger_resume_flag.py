"""Opt-in, own-child-only DEBUG_ATTACH_INVASIVE_RESUME_PROCESS experiment.

No window, arbitrary PID, breakpoint, context write, WeChat or production gate.
At most two targets: zero baseline, then one deliberately suspended helper.
The second case runs only after complete restoration in the first case.
"""
import argparse
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
from wechat_agent_bridge.adapters.debug_engine import DebugEngine, _call, U32, U64

RESUME_PROCESS = 0x10  # Microsoft's DbgEng.h, not a production attach option.
CASES = ('zero_baseline', 'preserve_one_suspend')


def kernel_api():
    k = c.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'GetCurrentProcess': (c.c_void_p, []),
        'GetCurrentThreadId': (U32, []),
        'OpenProcess': (c.c_void_p, [U32, c.c_int, U32]),
        'OpenThread': (c.c_void_p, [U32, c.c_int, U32]),
        'GetProcessIdOfThread': (U32, [c.c_void_p]),
        'CheckRemoteDebuggerPresent': (c.c_int, [c.c_void_p, c.POINTER(c.c_int)]),
        'GetThreadContext': (c.c_int, [c.c_void_p, c.c_void_p]),
        'CreateToolhelp32Snapshot': (c.c_void_p, [U32, U32]),
        'Thread32First': (c.c_int, [c.c_void_p, c.c_void_p]),
        'Thread32Next': (c.c_int, [c.c_void_p, c.c_void_p]),
        'GetProcessTimes': (c.c_int, [c.c_void_p] + [c.POINTER(U64)] * 4),
        'GetThreadTimes': (c.c_int, [c.c_void_p] + [c.POINTER(U64)] * 4),
        'WaitForSingleObject': (U32, [c.c_void_p, U32]),
        'CloseHandle': (c.c_int, [c.c_void_p]),
        'SuspendThread': (U32, [c.c_void_p]),
        'ResumeThread': (U32, [c.c_void_p]),
        'CreateEventW': (c.c_void_p, [c.c_void_p, c.c_int, c.c_int, c.c_wchar_p]),
        'SetEvent': (c.c_int, [c.c_void_p]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(k, name)
        fn.restype, fn.argtypes = result, args
    return k


def creation_time(k, handle):
    values = [U64() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(c.byref(v) for v in values)):
        raise RuntimeError('process_creation_time_failed')
    return values[0].value


def self_identity(k):
    return {'pid': os.getpid(), 'created': creation_time(k, k.GetCurrentProcess())}


def thread_creation_time(k, handle):
    values = [U64() for _ in range(4)]
    if not k.GetThreadTimes(handle, *(c.byref(v) for v in values)) or not values[0].value:
        raise RuntimeError('thread_creation_time_failed')
    return values[0].value


class BoundProcess:
    """Retain the exact process object; never reopen a reused numeric PID."""
    def __init__(self, identity, process=None, kernel=None):
        self.kernel = kernel or kernel_api()
        self.identity, self.process = dict(identity), process
        self.handle = None
        if (type(identity.get('pid')) is not int or identity['pid'] <= 0 or
                type(identity.get('created')) is not int or identity['created'] <= 0):
            raise RuntimeError('invalid_owned_identity')
        if process is not None and process.pid != identity['pid']:
            raise RuntimeError('owned_pid_mismatch')
        self.handle = self.kernel.OpenProcess(0x400 | 0x100000, False, identity['pid'])
        try:
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if (not self.handle or
                self.kernel.WaitForSingleObject(self.handle, 0) != 258 or
                creation_time(self.kernel, self.handle) != self.identity['created'] or
                (self.process is not None and not self.process.is_alive())):
            raise RuntimeError('owned_process_identity_or_liveness_changed')

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise RuntimeError('owned_process_handle_close_failed')


def source_child(connection, counter, stop, auxiliary):
    k = kernel_api()
    event = None
    helper = None
    helper_info = {}
    ready = threading.Event()
    result = {}
    try:
        if auxiliary:
            event = k.CreateEventW(None, True, False, None)
            if not event:
                raise RuntimeError('own_helper_event_failed')

            def wait_thread():
                helper_info['tid'] = k.GetCurrentThreadId()
                ready.set()
                # A native wait releases Python's GIL. The parent checks the
                # main heartbeat after its one suspension before any debugger.
                helper_info['wait_result'] = k.WaitForSingleObject(event, 90000)

            helper = threading.Thread(target=wait_thread, daemon=True)
            helper.start()
            if not ready.wait(2):
                raise RuntimeError('own_helper_start_timeout')
        identity = self_identity(k)
        identity.update(main_tid=k.GetCurrentThreadId(), auxiliary_tid=helper_info.get('tid'))
        connection.send({'identity': identity})
        until = time.monotonic() + 90
        while not stop.value and time.monotonic() < until:
            counter.value += 1
            time.sleep(.01)
    except BaseException as exc:
        result['error'] = error_code(exc)
    finally:
        if event:
            result['event_signalled'] = bool(k.SetEvent(event))
        if helper:
            helper.join(2)
            result['helper_exited'] = not helper.is_alive()
            result['helper_wait_result'] = helper_info.get('wait_result')
        if event:
            result['event_closed'] = bool(k.CloseHandle(event))
        try:
            connection.send({'source_cleanup': result})
        except (EOFError, OSError):
            pass
        connection.close()


def anchor_child(connection, stop):
    connection.send({'identity': self_identity(kernel_api())})
    connection.close()
    until = time.monotonic() + 90
    while not stop.value and time.monotonic() < until:
        time.sleep(.05)


def engine_child(connection, target_identity, anchor_identity, rescuing):
    engine = None
    bound = []
    result = {}
    try:
        connection.send({'identity': self_identity(kernel_api())})
        if not connection.poll(5) or connection.recv() != 'go':
            raise RuntimeError('owned_worker_start_not_authorized')
        for identity in (target_identity, anchor_identity):
            bound.append(BoundProcess(identity))
        engine = DebugEngine()
        for item in bound:
            item.check()
        engine.prepare(anchor_identity['pid'])
        for item in bound:
            item.check()
        if rescuing:
            _call(engine.client, 12, (U64, U32, U32),
                  (0, target_identity['pid'], RESUME_PROCESS))
            engine.user_target_attached = True
        else:
            engine.attach(target_identity['pid'])
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if engine.wait(100):
                if engine.process_id() == target_identity['pid']:
                    for item in bound:
                        item.check()
                    engine.confirm_exit_policy()
                    connection.send({'checkpoint': 'rescue_attached' if rescuing else 'owner_attached',
                                     'event_kind': engine.events.kind,
                                     'exception': engine.events.exception,
                                     'no_breakpoints': not engine.breakpoints})
                    break
                engine.resume()
        else:
            raise RuntimeError('owned_target_initial_event_timeout')
        if not rescuing:
            # Parent kills only this mp.Process after persisting the checkpoint.
            if connection.poll(12):
                raise RuntimeError('unexpected_owner_command')
            raise RuntimeError('owner_kill_checkpoint_timeout')
        engine.detach()
        result['rescue_detached'] = True
    except BaseException as exc:
        result['error'] = error_code(exc)
    finally:
        if engine:
            try:
                engine.close()
            except BaseException as exc:
                result['engine_cleanup_error'] = error_code(exc)
        result['handles_closed'] = []
        for item in reversed(bound):
            try:
                item.close()
                result['handles_closed'].append(True)
            except BaseException:
                result['handles_closed'].append(False)
        try:
            connection.send({'worker_result': result})
        except (EOFError, OSError):
            pass
        connection.close()


def error_code(exc):
    return str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else type(exc).__name__


def thread_state(bound, tid):
    """Validate the retained thread handle before any suspension, even on reuse."""
    k = bound.kernel
    thread = k.OpenThread(2 | 8 | 0x800 | 0x100000, False, tid)
    suspended = False
    prior = None
    try:
        bound.check()
        if (not thread or k.GetProcessIdOfThread(thread) != bound.identity['pid'] or
                k.WaitForSingleObject(thread, 0) != 258):
            raise RuntimeError('inspection_thread_not_owned')
        created = thread_creation_time(k, thread)
        present = c.c_int()
        if not k.CheckRemoteDebuggerPresent(bound.handle, c.byref(present)):
            raise RuntimeError('owned_debug_state_failed')
        bound.check()
        prior = k.SuspendThread(thread)
        if prior == 0xffffffff:
            raise RuntimeError('inspection_suspend_failed')
        suspended = True
        storage = c.create_string_buffer(1232 + 15)
        address = (c.addressof(storage) + 15) & ~15
        c.c_uint32.from_address(address + 48).value = 0x100010
        if not k.GetThreadContext(thread, address):
            raise RuntimeError('owned_thread_context_failed')
        return {'debugger_present': bool(present.value), 'prior_suspend_count': prior,
                'thread_created': created,
                'debug_registers': list(struct.unpack('<6Q', c.string_at(address + 72, 48)))}
    finally:
        try:
            if suspended and k.ResumeThread(thread) != prior + 1:
                raise RuntimeError('inspection_suspend_balance_changed')
        finally:
            if thread and not k.CloseHandle(thread):
                raise RuntimeError('inspection_thread_handle_close_failed')


def process_state(bound):
    bound.check()
    k = bound.kernel
    snapshot = k.CreateToolhelp32Snapshot(4, 0)
    if snapshot == c.c_void_p(-1).value:
        raise RuntimeError('owned_thread_snapshot_failed')
    tids = []
    try:
        entry = (U32 * 7)()
        entry[0] = 28
        ok = k.Thread32First(snapshot, c.byref(entry))
        if not ok:
            raise RuntimeError('owned_thread_enumeration_failed')
        while ok:
            if entry[3] == bound.identity['pid']:
                tids.append(entry[2])
                if len(tids) > 64:
                    raise RuntimeError('owned_thread_count_exceeded')
            ok = k.Thread32Next(snapshot, c.byref(entry))
        if c.get_last_error() != 18 or not tids:
            raise RuntimeError('owned_thread_enumeration_incomplete')
    finally:
        if not k.CloseHandle(snapshot):
            raise RuntimeError('owned_thread_snapshot_close_failed')
    result = {str(tid): thread_state(bound, tid) for tid in tids}
    bound.check()
    return result


def check_baseline(states, auxiliary_tid=None):
    if not states or (auxiliary_tid is not None and str(auxiliary_tid) not in states):
        raise RuntimeError('baseline_thread_set_invalid')
    for tid, state in states.items():
        expected = 1 if tid == str(auxiliary_tid) else 0
        if (state.get('debugger_present') is not False or
                type(state.get('thread_created')) is not int or state['thread_created'] <= 0 or
                state.get('prior_suspend_count') != expected or
                state.get('debug_registers') != [0] * 6):
            raise RuntimeError('baseline_not_exact')


def check_orphan(initial, states):
    if not states or not set(initial).issubset(states):
        raise RuntimeError('orphan_original_thread_missing')
    for tid, state in states.items():
        baseline = initial.get(tid, {}).get('prior_suspend_count', 0)
        if (state.get('debugger_present') is not False or
                type(state.get('thread_created')) is not int or state['thread_created'] <= 0 or
                (tid in initial and state['thread_created'] != initial[tid]['thread_created']) or
                state.get('debug_registers') != [0] * 6 or
                state.get('prior_suspend_count') != baseline + 1):
            raise RuntimeError('attached_orphan_state_not_reproduced')


def restored(initial, final):
    return bool(initial) and initial == final


def heartbeat(counter, bound, duration=2):
    previous = counter.value
    until = time.monotonic() + duration
    while time.monotonic() < until:
        time.sleep(.1)
        bound.check()
        current = counter.value
        if current <= previous:
            return False
        previous = current
    return True


def receive(connection, timeout, name):
    if not connection.poll(timeout):
        raise TimeoutError(name)
    return connection.recv()


def drain(connection):
    records = []
    while True:
        try:
            if not connection.poll():
                break
            records.append(connection.recv())
        except (EOFError, OSError):
            break
    return records


def terminate_owned(process):
    if process.pid is None:
        return {'started': False, 'dead': True, 'terminated': False, 'exitcode': None}
    killed = process.is_alive()
    if killed:
        process.terminate()
    process.join(2)
    return {'started': True, 'dead': not process.is_alive() and process.exitcode is not None,
            'terminated': killed, 'exitcode': process.exitcode}


def run_case(ctx, name, progress=lambda result: None):
    if name not in CASES:
        raise ValueError('unknown_synthetic_case')
    result = {'case': name, 'passed': False, 'attach_flags': RESUME_PROCESS,
              'stage': 'attached', 'hardware_breakpoints': False, 'records': []}
    processes, connections, bindings = [], [], []
    stop = ctx.Value('b', False, lock=False)
    counter = ctx.Value('L', 0, lock=False)
    paused_handle = None
    own_pause_active = False
    target_bound = None
    k = kernel_api()

    def launch(fn, args, role):
        parent, child = ctx.Pipe()
        connections.extend((parent, child))
        process = ctx.Process(target=fn, args=(child, *args))
        processes.append((role, process))
        try:
            process.start()
        finally:
            child.close()
        record = receive(parent, 5, role + '_handshake_timeout')
        identity = record.get('identity', {})
        bound = BoundProcess(identity, process)
        bindings.append(bound)
        result.setdefault('identities', {})[role] = identity
        progress(result)
        return process, parent, bound

    try:
        target, target_pipe, target_bound = launch(source_child,
                (counter, stop, name == CASES[1]), 'target')
        if not heartbeat(counter, target_bound, .3):
            raise RuntimeError('baseline_heartbeat_failed')
        before_pause = process_state(target_bound)
        check_baseline(before_pause)
        result['before_pause_threads'] = before_pause
        auxiliary_tid = target_bound.identity.get('auxiliary_tid')
        if name == CASES[1]:
            if type(auxiliary_tid) is not int or str(auxiliary_tid) not in before_pause:
                raise RuntimeError('owned_helper_tid_missing')
            paused_handle = k.OpenThread(2 | 0x800 | 0x100000, False, auxiliary_tid)
            if not paused_handle:
                raise RuntimeError('owned_helper_open_failed')
            target_bound.check()
            if (k.GetProcessIdOfThread(paused_handle) != target_bound.identity['pid'] or
                    k.WaitForSingleObject(paused_handle, 0) != 258 or
                    thread_creation_time(k, paused_handle) != before_pause[str(auxiliary_tid)]['thread_created']):
                raise RuntimeError('owned_helper_process_mismatch')
            prior = k.SuspendThread(paused_handle)
            if prior == 0xffffffff:
                raise RuntimeError('owned_helper_suspend_failed')
            own_pause_active = True
            result['own_pause_prior_count'] = prior
            if prior != 0:
                raise RuntimeError('owned_helper_prior_count_not_zero')
            if not heartbeat(counter, target_bound, .3):
                raise RuntimeError('own_helper_suspend_blocked_main_heartbeat')
        initial = process_state(target_bound)
        check_baseline(initial, auxiliary_tid)
        expected_initial = {tid: dict(state) for tid, state in before_pause.items()}
        if auxiliary_tid is not None:
            expected_initial[str(auxiliary_tid)]['prior_suspend_count'] = 1
        if initial != expected_initial:
            raise RuntimeError('baseline_changed_during_own_pause_setup')
        result['initial_threads'] = initial
        _, _, anchor = launch(anchor_child, (stop,), 'initial_bootstrap')
        owner, owner_pipe, owner_bound = launch(engine_child,
                (target_bound.identity, anchor.identity, False), 'owner')
        owner_bound.check()
        owner_pipe.send('go')
        checkpoint = receive(owner_pipe, 9, 'owner_attached_timeout')
        result['records'].append(checkpoint)
        if checkpoint.get('checkpoint') != 'owner_attached' or checkpoint.get('no_breakpoints') is not True:
            raise RuntimeError('owner_attached_checkpoint_missing')
        result['phase'] = 'owner_attached_before_kill'
        progress(result)
        result['owner_exit'] = terminate_owned(owner)
        if (not result['owner_exit']['dead'] or not result['owner_exit']['terminated'] or
                result['owner_exit']['exitcode'] in (None, 0)):
            raise RuntimeError('original_owner_death_unconfirmed')
        after_crash = process_state(target_bound)
        result['after_crash_threads'] = after_crash
        check_orphan(initial, after_crash)
        result['phase'] = 'orphan_confirmed'
        progress(result)
        # The old bootstrap may be paused too; never reuse it for rescue.
        _, _, fresh_anchor = launch(anchor_child, (stop,), 'rescue_bootstrap')
        rescuer, rescue_pipe, rescue_bound = launch(engine_child,
                (target_bound.identity, fresh_anchor.identity, True), 'rescuer')
        rescue_bound.check()
        target_bound.check()
        rescue_pipe.send('go')
        checkpoint = receive(rescue_pipe, 9, 'rescue_attached_timeout')
        result['records'].append(checkpoint)
        if checkpoint.get('checkpoint') != 'rescue_attached' or checkpoint.get('no_breakpoints') is not True:
            raise RuntimeError('rescue_attached_checkpoint_missing')
        result['rescue'] = receive(rescue_pipe, 4, 'rescue_cleanup_timeout').get('worker_result', {})
        rescuer.join(2)
        if rescuer.is_alive() or rescuer.exitcode != 0:
            raise RuntimeError('rescue_exit_not_clean')
        if result['rescue'] != {'rescue_detached': True, 'handles_closed': [True, True]}:
            raise RuntimeError('rescue_detach_or_cleanup_failed')
        result['immediate_after_rescue_threads'] = process_state(target_bound)
        result['stable_two_seconds'] = heartbeat(counter, target_bound)
        if not result['stable_two_seconds']:
            raise RuntimeError('restored_target_heartbeat_failed')
        result['final_threads'] = process_state(target_bound)
        if not restored(initial, result['final_threads']):
            raise RuntimeError('thread_set_suspend_count_or_dr_changed')
        result['passed'] = True
    except BaseException as exc:
        result['error'] = error_code(exc)
    finally:
        cleanup = {'processes': [], 'errors': []}
        # Kill an outstanding debugger before inspecting/releasing our pause.
        for role, process in reversed(processes):
            if role not in ('owner', 'rescuer'):
                continue
            try:
                entry = terminate_owned(process)
                cleanup['processes'].append(dict(role=role, **entry))
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        workers_dead = (not cleanup['errors'] and
                        all(item['dead'] for item in cleanup['processes']))
        if own_pause_active and paused_handle and workers_dead:
            try:
                current = process_state(target_bound)
                # Only release our known one suspension after full restoration.
                # Ambiguous/consumed counts are not corrected by guesses.
                if restored(result.get('initial_threads'), current):
                    value = k.ResumeThread(paused_handle)
                    cleanup['own_pause_release_count'] = value
                    if value != 1:
                        raise RuntimeError('own_pause_release_count_changed')
                    own_pause_active = False
                else:
                    cleanup['own_pause_release'] = 'not_safe_no_resume'
                    result['passed'] = False
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        if paused_handle:
            try:
                if not k.CloseHandle(paused_handle):
                    cleanup['errors'].append('owned_helper_handle_close_failed')
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        stop.value = True
        for role, process in reversed(processes):
            if role in ('owner', 'rescuer'):
                continue
            try:
                if process.pid is not None:
                    process.join(2 if role == 'target' else .5)
                entry = terminate_owned(process)
                cleanup['processes'].append(dict(role=role, **entry))
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        if 'target_pipe' in locals():
            cleanup['source_records'] = drain(target_pipe)
        for bound in reversed(bindings):
            try:
                bound.close()
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        for connection in connections:
            try:
                connection.close()
            except BaseException as exc:
                cleanup['errors'].append(error_code(exc))
        result['cleanup'] = cleanup
        target_entries = [x for x in cleanup['processes'] if x['role'] == 'target']
        if (cleanup['errors'] or not cleanup['processes'] or
                not all(x['dead'] for x in cleanup['processes']) or
                not target_entries or target_entries[0]['terminated'] or target_entries[0]['exitcode'] != 0):
            result['passed'] = False
        if name == CASES[1] and (own_pause_active or cleanup.get('own_pause_release_count') != 1):
            result['passed'] = False
        expected_source = ({'event_signalled': True, 'helper_exited': True,
                            'helper_wait_result': 0, 'event_closed': True}
                           if name == CASES[1] else {})
        if cleanup.get('source_records') != [{'source_cleanup': expected_source}]:
            result['passed'] = False
    progress(result)
    return result


def run_cases(ctx, progress=lambda result: None, runner=run_case):
    results = []
    for name in CASES:
        result = runner(ctx, name, progress)
        results.append(result)
        if not result.get('passed'):
            break
    return results


def fingerprints():
    paths = [Path(__file__), ROOT / 'wechat_agent_bridge/adapters/debug_engine.py',
             Path(os.environ['SystemRoot']) / 'System32/dbgeng.dll']
    return {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name:
            hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def save_report(path, report):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = {'research_only': True, 'live_eligible': False, 'live_diagnostic_ready': False,
              'state': 'prepared', 'cases': [], 'passed': False, 'maximum_cases': 2,
              'stage': 'attached', 'attach_flags': RESUME_PROCESS, 'hardware_breakpoints': False}
    if not args.run_synthetic:
        print(json.dumps(report, indent=2))
        return 0
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError('requires_windows_x64')
    destination = args.output or ROOT / '.local/live-acceptance' / ('debugger-resume-flag-' + uuid.uuid4().hex + '.json')
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT / '.local/live-acceptance'):
        raise RuntimeError('report_outside_owned_evidence_directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace prior experimental evidence.
    with destination.open('x', encoding='utf-8') as stream:
        stream.write('{}')
    report.update(state='running', os=platform.version(), output=str(destination))
    try:
        report['fingerprints'] = fingerprints()
        save_report(destination, report)

        def progress(case):
            report['current_case'] = case
            save_report(destination, report)

        report['cases'] = run_cases(mp.get_context('spawn'), progress)
    except BaseException as exc:
        report['error'] = error_code(exc)
    finally:
        try:
            report['final_fingerprints'] = fingerprints()
            report['source_unchanged'] = report.get('fingerprints') == report['final_fingerprints']
        except BaseException as exc:
            report['fingerprint_error'] = error_code(exc)
            report['source_unchanged'] = False
        report['state'] = 'finished'
        report['passed'] = (report.get('source_unchanged') is True and len(report['cases']) == 2 and
                            all(x.get('passed') is True for x in report['cases']) and 'error' not in report)
        save_report(destination, report)
    print(json.dumps({'report': str(destination), 'passed': report['passed'],
                      'live_eligible': False, 'cases_run': len(report['cases'])}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
