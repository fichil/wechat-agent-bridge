"""Owned-process experiment for the scope of a second NtDebugContinue call.

This tests a kernel property needed before designing uncertain-result recovery.
It is NOT a recovery retry in the live adapter; no external PID is accepted.
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
from scripts import probe_debug_object_delivery as delivery

owned = delivery.owned
recovery = delivery.recovery
CUSTOM_EXCEPTION = 0xe0424242
NOT_HANDLED = 0x80010001
NO_REPORTED_EVENT = 0xc000000d
CASES = ('empty_queue', 'unread_new_exception')


def source(pipe, counter, stop, raise_now, caught):
    kernel = recovery.kernel_api()
    raise_exception = kernel.RaiseException
    raise_exception.argtypes = [c.c_uint32, c.c_uint32, c.c_uint32, c.c_void_p]
    raise_exception.restype = None
    function = kernel.GetCurrentProcessId
    function.restype = c.c_uint32
    identity = owned.self_identity(kernel)
    identity.update(main_tid=kernel.GetCurrentThreadId(), address=c.cast(function, c.c_void_p).value)
    pipe.send(identity)
    pipe.close()
    raised = False
    until = time.monotonic() + 30
    while not stop.value and time.monotonic() < until:
        function()
        if raise_now.value and not raised:
            raised = True
            try:
                raise_exception(CUSTOM_EXCEPTION, 0, 0, None)
            except OSError:
                # If the debugger wrongly handles the unread exception, this
                # handler will not run. The expected count is exactly one.
                caught.value += 1
        counter.value += 1
        time.sleep(.01)


def call_continue(bound, handle, native, event, status):
    bound.check()
    if event['pid'] != bound.identity['pid'] or event['tid'] != bound.identity['main_tid']:
        raise RuntimeError('scope_continue_identity_mismatch')
    client = delivery.ClientId(event['pid'], event['tid'])
    return native.NtDebugContinue(handle, c.byref(client), c.c_int32(status).value) & 0xffffffff


def check_new_exception(event, identity):
    if event != dict(state=6, pid=identity['pid'], tid=identity['main_tid'],
                     code=CUSTOM_EXCEPTION, address=event.get('address'), first_chance=1) or not event['address']:
        raise RuntimeError('scope_new_exception_not_observed')


def scope_qualified(result):
    return bool(result.get('state') == 'complete' and result.get('second_continue_status') == NO_REPORTED_EVENT and
        result.get('second_continue_count') == 1 and result.get('no_wait_between_continues') is True and
        result.get('initial_threads') and result.get('initial_threads') == result.get('final_threads') and
        result.get('stable_two_seconds') is True and result.get('debugger_after') is False and
        result.get('handles_closed') is True and not result.get('cleanup_errors') and
        result.get('child_cleanup', {}).get('exitcode') == 0 and not result['child_cleanup'].get('terminated') and
        ((result.get('case') == 'empty_queue' and result.get('queue_signalled') is False and
          result.get('target_handler_count') == 0) or
         (result.get('case') == 'unread_new_exception' and result.get('queue_signalled') is True and
          result.get('new_exception_remained_pending') is True and result.get('target_handler_count') == 1)))


def run_case(context, name):
    if name not in CASES:
        raise RuntimeError('scope_case_invalid')
    counter = context.Value('L', 0, lock=False)
    stop, raise_now = context.Value('b', False, lock=False), context.Value('b', False, lock=False)
    caught = context.Value('L', 0, lock=False)
    read, write = context.Pipe()
    target = context.Process(target=source, args=(write, counter, stop, raise_now, caught))
    bound = thread = handle = None
    result = dict(case=name, state='started', cleanup_errors=[], live_eligible=False)
    try:
        target.start(); write.close()
        identity = owned.receive(read, 5, 'scope_source_timeout')
        bound = owned.BoundProcess(identity, target, recovery.kernel_api())
        initial = owned.process_state(bound)
        owned.check_baseline(initial)
        result['initial_threads'] = initial
        thread = recovery.BoundThread(bound, identity['main_tid'], initial[str(identity['main_tid'])]['thread_created'])
        native = delivery.api()
        handle = delivery.create_object(native)
        with delivery.mutation_handle(bound) as process:
            delivery.status_checked(native.NtDebugActiveProcess(process, handle), 'scope_attach')
        event = delivery.wait_event(bound, handle, native, 500)
        delivery.validate_event(event, identity, hit=False)
        original = thread.context()[2]
        if original != initial[str(identity['main_tid'])]['debug_registers']:
            raise RuntimeError('scope_original_changed')
        armed = recovery.armed_registers(original, identity['address'])
        thread.set_registers(armed)
        delivery.continue_event(bound, handle, native, event)
        until = time.monotonic() + 4
        for _ in range(128):
            if time.monotonic() > until:
                raise RuntimeError('scope_hit_timeout')
            event = delivery.wait_event(bound, handle, native)
            if event is None:
                continue
            if event['state'] in (6, 7, 8):
                delivery.validate_event(event, identity, hit=True)
                break
            if event['state'] not in (2, 9, 10):
                raise RuntimeError('scope_unexpected_initial_event')
            delivery.continue_event(bound, handle, native, event)
        else:
            raise RuntimeError('scope_event_limit')
        thread.set_registers(recovery.safe_restore(original, armed, thread.context()[2]))
        raise_now.value = name == 'unread_new_exception'
        result['first_continue_status'] = call_continue(bound, handle, native, event, recovery.CONTINUE)
        delivery.status_checked(result['first_continue_status'], 'scope_first_continue')
        # Waiting on the object's synchronization signal does not dequeue or
        # mark a debug event as delivered. No NtWaitForDebugEvent occurs here.
        ready = bound.kernel.WaitForSingleObject(handle, 2000 if raise_now.value else 0)
        if ready not in (0, 258) or (ready == 0) != bool(raise_now.value):
            raise RuntimeError('scope_queue_state_not_established')
        result['queue_signalled'] = ready == 0
        result['no_wait_between_continues'] = True
        result['second_continue_count'] = 1
        result['second_continue_status'] = call_continue(bound, handle, native, event, recovery.CONTINUE)
        if result['second_continue_status'] != NO_REPORTED_EVENT:
            raise RuntimeError('scope_second_continue_not_rejected')
        if raise_now.value:
            next_event = delivery.wait_event(bound, handle, native, 500)
            if not isinstance(next_event, dict):
                raise RuntimeError('scope_new_exception_missing')
            check_new_exception(next_event, identity)
            result['new_exception'] = next_event
            result['new_exception_remained_pending'] = True
            # This is a newly received, explicitly identified event; permit
            # the target's own handler to run, do not swallow its exception.
            delivery.status_checked(call_continue(bound, handle, native, next_event, NOT_HANDLED),
                                    'scope_new_exception_continue')
        delivery.detach(bound, handle, native)
        result['debugger_after'] = recovery.retention.debugger_present(bound)
        recovery.checked(bound.kernel.CloseHandle(handle), 'scope_object_close'); handle = None
        result['object_closed'] = True
        result['stable_two_seconds'] = owned.heartbeat(counter, bound)
        result['target_handler_count'] = caught.value
        result['final_threads'] = owned.process_state(bound)
        result['state'] = 'complete'
    except BaseException as exc:
        result.update(state='failed', error=owned.error_code(exc))
    finally:
        if handle is not None:
            result['failed_target_cleanup'] = owned.terminate_owned(target)
            if bound.kernel.CloseHandle(handle):
                result['object_closed'] = True
            else:
                result['cleanup_errors'].append('scope_object_close_failed')
        stop.value = True
        if target.pid:
            target.join(.5)
        result['child_cleanup'] = owned.terminate_owned(target)
        for port in (thread, bound):
            if port is not None:
                try:
                    port.close()
                except BaseException as exc:
                    result['cleanup_errors'].append(owned.error_code(exc))
        read.close(); write.close()
        if target.pid and not target.is_alive():
            target.close()
        result['handles_closed'] = (result['child_cleanup']['dead'] and result.get('object_closed') is True and
                                   not result['cleanup_errors'])
    result['passed'] = scope_qualified(result)
    return result


def fingerprints():
    result = delivery.fingerprints()
    result[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', live_eligible=False)))
        return 0
    hashes = fingerprints()
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8 or any(
            hashes.get(name) != digest for name, digest in recovery.retention.SYSTEM_SHA.items()):
        raise RuntimeError('scope_platform_or_fingerprint_mismatch')
    cases = []
    for name in CASES:
        result = run_case(mp.get_context('spawn'), name)
        cases.append(result)
        print(json.dumps({key: result.get(key) for key in ('case', 'passed', 'error',
            'second_continue_status', 'queue_signalled', 'new_exception_remained_pending', 'target_handler_count')}), flush=True)
        if not result['passed']:
            break
    report = dict(kind='owned_native_continue_scope', cases=cases, fingerprints=hashes,
                  source_unchanged=hashes == fingerprints(), os=platform.version(), live_eligible=False)
    report['passed'] = len(cases) == 2 and report['source_unchanged'] and all(item['passed'] for item in cases)
    path = ROOT / '.local/live-acceptance' / ('debug-continue-scope-' + uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(path), passed=report['passed'], live_eligible=False)))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
