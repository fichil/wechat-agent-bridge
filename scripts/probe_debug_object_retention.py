"""Owned-child experiment: does a retained debug object survive owner death?

No external PID, windows, client inspection, context writes or extra resumes.
The experiment does NOT claim that retaining a handle restores DbgEng state.
Native class 30 is private/version-bound; see docs/M1_DEBUG_OBJECT_RETENTION.md.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import test_debug_engine as original
from scripts import probe_debugger_resume_flag as owned

MODES = ('normal', 'crash', 'watchdog')
SYSTEM_SHA = {
    'dbgeng.dll': 'bfa188b10eb64a94f1eb59bfb0f8d85eb5dfc803cd0f6b5c554816fe311a236e',
    'ntdll.dll': '651ac9041af8e6b5ebd331d53163244c0a27d36d1433f0f15cac1f28c5f2e82c',
    'kernel32.dll': 'e132eba4166256e2f414b91fe7d5386590bc5ea8626aee5af9c9738383cf2ae1',
}


def query_api():
    dll = c.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/ntdll.dll'), winmode=0x800)
    query = dll.NtQueryInformationProcess
    query.argtypes = [c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32)]
    query.restype = c.c_int32
    return query


def retain_object(bound, query):
    """Only a creation-time-bound child process; output handle belongs to us."""
    bound.check()
    present = c.c_int()
    if (not bound.kernel.CheckRemoteDebuggerPresent(bound.handle, c.byref(present)) or
            present.value != 1):
        raise RuntimeError('retention_debugger_missing')
    handle, returned = c.c_void_p(), c.c_uint32()
    status = query(bound.handle, 30, c.byref(handle), c.sizeof(handle), c.byref(returned))
    try:
        if status != 0 or not handle.value or returned.value != c.sizeof(handle):
            raise RuntimeError('retention_query_failed_' + format(status & 0xffffffff, '08x'))
        bound.check()
        return handle.value
    except BaseException:
        if handle.value and not bound.kernel.CloseHandle(handle):
            raise RuntimeError('retention_failed_query_handle_close_failed')
        raise


def source(connection, counter, stop):
    kernel = owned.kernel_api()
    function = kernel.GetCurrentProcessId
    function.restype = c.c_uint32
    identity = owned.self_identity(kernel)
    identity.update(main_tid=kernel.GetCurrentThreadId(),
                    address=c.cast(function, c.c_void_p).value)
    connection.send(identity)
    connection.close()
    until = time.monotonic() + 40
    while not stop.value and time.monotonic() < until:
        function()
        counter.value += 1
        time.sleep(.01)


def owner(connection, identity, anchor_pid, mode, nonce):
    class Gate:
        def send(self, record):
            connection.send(record)
            if record.get('stage_reached') == 'attached':
                if not connection.poll(3) or connection.recv() != {'retained': nonce}:
                    raise RuntimeError('retention_parent_ack_missing')
    # Original owner really exits/crashes here. This is not an analysis fault.
    original.debugger(Gate(), identity['pid'], identity['address'], mode,
                      anchor_pid, 'attached', 'multi')


def debugger_present(bound):
    bound.check()
    present = c.c_int()
    if not bound.kernel.CheckRemoteDebuggerPresent(bound.handle, c.byref(present)):
        raise RuntimeError('retention_presence_query_failed')
    return bool(present.value)


def expected_exit(mode, exitcode, terminated):
    if mode == 'normal':
        return exitcode == 0 and not terminated
    if mode == 'crash':
        return exitcode == 17 and not terminated
    return mode == 'watchdog' and type(exitcode) is int and exitcode != 0 and terminated


def case_qualified(result):
    """Retention alone never makes a failed target-state restoration a pass."""
    return bool(result.get('state') == 'complete' and result.get('all_children_exited') is True and
        result.get('handles_closed') is True and result.get('retained_before_exit') is True and
        result.get('owner_exit_confirmed') is True and
        expected_exit(result.get('mode'), result.get('owner_exitcode'), result.get('owner_terminated')) and
        result.get('initial_threads') and result.get('initial_threads') == result.get('final_threads') and
        result.get('stable_two_seconds') is True and result.get('debugger_after_close') is False and
        not result.get('cleanup_errors') and
        not any('error' in item or 'cleanup_error' in item for item in result.get('records', [])))


def run_case(context, mode):
    if mode not in MODES:
        raise RuntimeError('retention_mode_invalid')
    counter, stop = context.Value('L', 0, lock=False), context.Value('b', False, lock=False)
    receiver, child = context.Pipe()
    read, write = context.Pipe()
    target = context.Process(target=source, args=(child, counter, stop))
    anchor = context.Process(target=original.bootstrap, args=(stop,))
    worker = None
    bound = None
    retained = None
    result = dict(mode=mode, state='started', records=[], cleanup_errors=[], owner_terminated=False,
                  retained_before_exit=False, owner_exit_confirmed=False, handles_closed=False)
    try:
        anchor.start(); target.start(); child.close()
        identity = owned.receive(receiver, 5, 'retention_target_start_timeout')
        bound = owned.BoundProcess(identity, target)
        result['initial_threads'] = owned.process_state(bound)
        owned.check_baseline(result['initial_threads'])
        query = query_api()
        nonce = uuid.uuid4().hex
        worker = context.Process(target=owner, args=(write, identity, anchor.pid, mode, nonce))
        worker.start(); write.close()
        until = time.monotonic() + 12
        while time.monotonic() < until:
            if read.poll(.03):
                try:
                    record = read.recv()
                except EOFError:
                    break
                result['records'].append(record)
                if record.get('stage_reached') == 'attached':
                    if retained is not None:
                        raise RuntimeError('retention_duplicate_checkpoint')
                    retained = retain_object(bound, query)
                    result['retained_before_exit'] = True
                    read.send({'retained': nonce})
                if record.get('awaiting_external_termination'):
                    if mode != 'watchdog' or retained is None:
                        raise RuntimeError('retention_kill_before_handle')
                    worker.terminate()
                    result['owner_terminated'] = True
            if not worker.is_alive():
                break
        worker.join(1)
        if worker.is_alive():
            raise RuntimeError('retention_owner_exit_timeout')
        result['records'].extend(owned.drain(read))
        result.update(owner_exit_confirmed=True, owner_exitcode=worker.exitcode)
        if not result['retained_before_exit']:
            raise RuntimeError('retention_checkpoint_missing')
        bound.check()
        result['debugger_while_retained'] = debugger_present(bound)
        result['threads_while_retained'] = owned.process_state(bound)
        before = counter.value
        time.sleep(.15)
        result['heartbeat_while_retained'] = counter.value > before
        if not bound.kernel.CloseHandle(retained):
            raise RuntimeError('retention_object_close_failed')
        retained = None
        result['object_handle_closed'] = True
        until = time.monotonic() + .5
        while time.monotonic() < until and debugger_present(bound):
            time.sleep(.02)
        result['debugger_after_close'] = debugger_present(bound)
        result['final_threads'] = owned.process_state(bound)
        result['stable_two_seconds'] = owned.heartbeat(counter, bound)
        result['state'] = 'complete'
    except BaseException as exc:
        result.update(state='failed', error=owned.error_code(exc))
    finally:
        if worker is not None and worker.pid and worker.is_alive():
            result['owner_cleanup'] = owned.terminate_owned(worker)
        if retained is not None and bound is not None:
            if not bound.kernel.CloseHandle(retained):
                result['cleanup_errors'].append('retention_object_close_failed')
            else:
                result['object_handle_closed'] = True
        stop.value = True
        for item in (target, anchor):
            if item.pid:
                item.join(.5)
        children = [item for item in (worker, target, anchor) if item is not None]
        result['child_cleanup'] = [owned.terminate_owned(item) for item in children]
        result['all_children_exited'] = all(item['dead'] for item in result['child_cleanup'])
        if bound is not None:
            try:
                bound.close()
            except Exception as exc:
                result['cleanup_errors'].append(owned.error_code(exc))
        for pipe in (receiver, child, read, write):
            pipe.close()
        for process in children:
            if process.pid and not process.is_alive():
                process.close()
        result['handles_closed'] = (result['all_children_exited'] and not result['cleanup_errors'] and
                                    result.get('object_handle_closed') is True)
    result['passed'] = case_qualified(result)
    result['owner_failure_recovered'] = False
    result['live_eligible'] = False
    return result


def fingerprints():
    files = [Path(__file__), Path(original.__file__), Path(owned.__file__),
             ROOT / 'wechat_agent_bridge/adapters/debug_engine.py']
    files += [Path(os.environ['SystemRoot']) / 'System32' / name for name in SYSTEM_SHA]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', live_eligible=False)))
        return 0
    source_hashes = fingerprints()
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8 or any(
            source_hashes.get(name) != digest for name, digest in SYSTEM_SHA.items()):
        raise RuntimeError('retention_platform_or_fingerprint_mismatch')
    cases = []
    for mode in MODES:
        result = run_case(mp.get_context('spawn'), mode)
        cases.append(result)
        print(json.dumps({key: result.get(key) for key in (
            'mode', 'state', 'passed', 'error', 'debugger_while_retained',
            'debugger_after_close', 'stable_two_seconds', 'all_children_exited')}), flush=True)
        if result['state'] != 'complete' or not result['all_children_exited']:
            break
    report = dict(kind='owned_debug_object_retention', cases=cases, os=platform.version(),
                  fingerprints=source_hashes, source_unchanged=source_hashes == fingerprints(),
                  owner_failure_recovered=False, live_eligible=False)
    report['passed'] = (len(cases) == len(MODES) and report['source_unchanged'] and
                        all(item['passed'] for item in cases))
    destination = ROOT / '.local/live-acceptance' / ('debug-object-retention-' + uuid.uuid4().hex + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(destination), passed=report['passed'], live_eligible=False)))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
