"""Synthetic-only native debug-object takeover after actual owner exit.

Separate candidate, NOT a DbgEng recovery fix or live diagnostic permission.
No arbitrary PID/GUI/WeChat entrypoint. Own process objects and thread creation
times stay pinned; no additional ResumeThread is used for recovery.
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
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import probe_debug_object_retention as retention
from scripts import probe_debugger_resume_flag as owned

STAGES = ('attached', 'breakpoint')
CONTINUE = 0x10002
BREAKPOINT = 0x80000003
SINGLE_STEP = 0x80000004


def kernel_api():
    kernel = owned.kernel_api()
    signatures = {
        'DebugActiveProcess': [c.c_uint32],
        'DebugActiveProcessStop': [c.c_uint32],
        'DebugSetProcessKillOnExit': [c.c_int],
        'WaitForDebugEvent': [c.c_void_p, c.c_uint32],
        'ContinueDebugEvent': [c.c_uint32, c.c_uint32, c.c_uint32],
        'SetThreadContext': [c.c_void_p, c.c_void_p],
    }
    for name, args in signatures.items():
        getattr(kernel, name).argtypes = args
        getattr(kernel, name).restype = c.c_int
    return kernel


def checked(value, operation):
    if not value:
        raise RuntimeError(operation + '_' + str(c.get_last_error()))
    return value


def native_recovery_api():
    library = c.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/ntdll.dll'), winmode=0x800)
    library.NtDebugContinue.argtypes = [c.c_void_p, c.c_void_p, c.c_int32]
    library.NtDebugContinue.restype = c.c_int32
    library.NtRemoveProcessDebug.argtypes = [c.c_void_p, c.c_void_p]
    library.NtRemoveProcessDebug.restype = c.c_int32
    return library


class BoundThread:
    def __init__(self, bound, tid, expected_creation, *, fixture_suspend=False):
        self.bound, self.tid, self.created = bound, tid, expected_creation
        self.kernel = bound.kernel
        self.last_register_write = None
        access = 8 | 16 | 0x800 | 0x100000 | (2 if fixture_suspend else 0)
        self.handle = self.kernel.OpenThread(access, False, tid)
        try:
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        self.bound.check()
        if (not self.handle or self.kernel.GetProcessIdOfThread(self.handle) != self.bound.identity['pid'] or
                self.kernel.WaitForSingleObject(self.handle, 0) != 258 or
                owned.thread_creation_time(self.kernel, self.handle) != self.created):
            raise RuntimeError('takeover_thread_identity_changed')

    def context(self):
        self.check()
        storage = c.create_string_buffer(1247)
        pointer = (c.addressof(storage) + 15) & ~15
        c.c_uint32.from_address(pointer + 48).value = 0x100010
        checked(self.kernel.GetThreadContext(self.handle, pointer), 'takeover_context_read')
        return storage, pointer, list(struct.unpack('<6Q', c.string_at(pointer + 72, 48)))

    def set_registers(self, registers):
        encoded = encode_debug_context(registers)
        storage, pointer, _ = self.context()
        c.memmove(pointer + 72, struct.pack('<6Q', *encoded), 48)
        self.check()
        checked(self.kernel.SetThreadContext(self.handle, pointer), 'takeover_context_write')
        observed = self.context()[2]
        self.last_register_write = dict(requested=list(registers), native_requested=encoded, observed=list(observed))
        if observed != encoded:
            raise RuntimeError('takeover_register_readback_mismatch')

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            checked(self.kernel.CloseHandle(handle), 'takeover_thread_close')


def encode_debug_context(registers):
    """Exact, empirically bounded Windows write representation, not a DR mask.

    Only the two observed cause-free DR6 baseline forms are accepted. The final
    running state must still equal the full raw baseline after continuation.
    Unrepresentable original status is refused before attaching the debugger.
    """
    if (not isinstance(registers, list) or len(registers) != 6 or
            any(type(value) is not int or not 0 <= value < 2**64 for value in registers) or
            registers[4] not in (0, 0xffff07f0)):
        raise RuntimeError('takeover_original_debug_status_not_representable')
    encoded = list(registers)
    encoded[4] = 0
    encoded[5] &= ~0x400
    return encoded


def armed_registers(original, address):
    if (not isinstance(original, list) or len(original) != 6 or
            any(type(value) is not int or not 0 <= value < 2**64 for value in original) or
            original[5] & 3 or type(address) is not int or address <= 0):
        raise RuntimeError('takeover_breakpoint_slot_not_free')
    # Slot zero only: preserve other slots and their configuration.
    armed = list(original)
    armed[0] = address
    armed[5] = (armed[5] & ~(0xf << 16)) | 1
    return armed


def validate_hit(message, identity, baseline, intent):
    if (not isinstance(message, dict) or set(message) != {'pending'} or
            not isinstance(message['pending'], dict)):
        raise RuntimeError('takeover_pending_shape_invalid')
    event = message['pending']
    if (set(event) != {'pid', 'tid', 'code', 'address', 'stage'} or
            event['pid'] != identity['pid'] or event['stage'] not in STAGES or
            type(event['tid']) is not int or event['tid'] <= 0):
        raise RuntimeError('takeover_event_identity_invalid')
    if event['stage'] == 'attached':
        if event['code'] != BREAKPOINT or intent is not None:
            raise RuntimeError('takeover_initial_event_invalid')
    elif (event['code'] != SINGLE_STEP or event['tid'] != identity['main_tid'] or
          event['address'] != identity['address'] or intent is None or
          intent.get('original') != baseline[str(identity['main_tid'])]['debug_registers'] or
          intent.get('armed') != armed_registers(intent['original'], identity['address'])):
        raise RuntimeError('takeover_hardware_event_invalid')
    return event


def safe_restore(original, armed, current):
    """Only the recorded slot and its observed hit may differ; no blanket DR zero."""
    if (len(current) != 6 or any(current[i] != armed[i] for i in (0, 1, 2, 3)) or
            (current[5] ^ armed[5]) & ~0x400):
        raise RuntimeError('takeover_register_ownership_changed')
    # Intel SDM Vol. 3B Fig. 17-2: DR7 bit 10 is reserved one. On this
    # fingerprinted Windows build, SetThreadContext readback is 1, but the
    # actual hardware hit reads 0x401. No enabled/configuration bit is ignored;
    # restoring the recorded original still requires exact complete readback.
    # DR6 B0 is our hit. Reject other breakpoint/debug causes, tolerate only
    # architectural reserved-bit presentation differences in this comparison.
    # Intel SDM 18.2.3: a cleared BLD (bit 11) or RTM (bit 16) can denote
    # another debug condition. Do not swallow those as our execution hit.
    if current[4] & 0xe00f != 1 or current[4] & 0x10800 != 0x10800:
        raise RuntimeError('takeover_unowned_debug_exception')
    return list(original)


def poll_record(pipe):
    """Windows may report a closed named pipe from poll, before child exit."""
    try:
        return ('record', pipe.recv()) if pipe.poll(.03) else ('idle', None)
    except (EOFError, BrokenPipeError):
        return 'closed', None


def owner(pipe, identity, baseline, mode, stage, nonce):
    kernel = kernel_api()
    bound = owned.BoundProcess(identity, kernel=kernel)
    thread = BoundThread(bound, identity['main_tid'], baseline[str(identity['main_tid'])]['thread_created'])
    attached, pending, original, armed = False, None, None, None

    def handshake(record, key):
        pipe.send(record)
        if not pipe.poll(3) or pipe.recv() != {key: nonce}:
            raise RuntimeError('takeover_parent_handshake_failed')

    try:
        bound.check()
        checked(kernel.DebugActiveProcess(identity['pid']), 'takeover_attach'); attached = True
        checked(kernel.DebugSetProcessKillOnExit(False), 'takeover_exit_policy')
        handshake({'attached': True}, 'retained')
        until = time.monotonic() + 5
        while time.monotonic() < until:
            event = (c.c_uint64 * 22)()
            if not kernel.WaitForDebugEvent(c.byref(event), 100):
                if c.get_last_error() == 121:
                    continue
                checked(False, 'takeover_wait')
            raw = bytes(event)
            kind, pid, tid = struct.unpack_from('<III', raw)
            if pid != identity['pid']:
                raise RuntimeError('takeover_native_event_target_mismatch')
            pending = (pid, tid, CONTINUE)
            if kind in (3, 6):
                image_handle = struct.unpack_from('<Q', raw, 16)[0]
                if image_handle:
                    checked(kernel.CloseHandle(image_handle), 'takeover_image_handle_close')
            if kind == 1:
                code, address = struct.unpack_from('<I', raw, 16)[0], struct.unpack_from('<Q', raw, 32)[0]
                pending = (pid, tid, 0x80010001)
                if code == BREAKPOINT and armed is None:
                    pending = (pid, tid, CONTINUE)
                    if stage == 'breakpoint':
                        original = thread.context()[2]
                        armed = armed_registers(original, identity['address'])
                        handshake({'arm_intent': {'original': original, 'armed': armed}}, 'arm_recorded')
                        thread.set_registers(armed)
                    else:
                        handshake({'pending': dict(pid=pid, tid=tid, code=code, address=address, stage=stage)}, 'fault_ready')
                        break
                elif code == SINGLE_STEP and tid == identity['main_tid'] and armed is not None:
                    pending = (pid, tid, CONTINUE)
                    pipe.send({'breakpoint_registers': thread.context()[2]})
                    handshake({'pending': dict(pid=pid, tid=tid, code=code, address=address, stage=stage)}, 'fault_ready')
                    break
            checked(kernel.ContinueDebugEvent(*pending), 'takeover_continue'); pending = None
        else:
            raise RuntimeError('takeover_breakpoint_timeout')
        if mode == 'crash':
            os._exit(17)
        if mode == 'watchdog':
            pipe.send({'awaiting_external_termination': True})
            # Parent owns this process. A parent loss is not a recovery success.
            while mp.parent_process().is_alive():
                time.sleep(.05)
            os._exit(45)
        if armed is not None:
            thread.set_registers(safe_restore(original, armed, thread.context()[2]))
            armed = None
        checked(kernel.ContinueDebugEvent(*pending), 'takeover_continue'); pending = None
        checked(kernel.DebugActiveProcessStop(identity['pid']), 'takeover_detach'); attached = False
        pipe.send({'detached': True})
    except BaseException as exc:
        if thread.last_register_write is not None:
            pipe.send({'last_register_write': thread.last_register_write})
        pipe.send({'error': owned.error_code(exc)})
    finally:
        # Synthetic failures fail closed. Parent retains the debug object and
        # disposable target; never guess a missing event or register baseline.
        if attached:
            pipe.send({'cleanup_delegated': True})
        thread.close(); bound.close(); pipe.close()


def recover(bound, thread, retained, event, intent):
    bound.check(); thread.check()
    if not retention.debugger_present(bound):
        raise RuntimeError('takeover_original_debug_object_lost')
    api = native_recovery_api()
    receipt = dict(registers_restored=False, event_continued=False, detached=False)
    if event['stage'] == 'breakpoint':
        thread.set_registers(safe_restore(intent['original'], intent['armed'], thread.context()[2]))
        receipt['registers_restored'] = True
    client_id = (c.c_void_p * 2)(event['pid'], event['tid'])
    status = api.NtDebugContinue(retained, c.byref(client_id), CONTINUE)
    if status != 0:
        raise RuntimeError('takeover_continue_status_' + format(status & 0xffffffff, '08x'))
    receipt['event_continued'] = True
    # This additional process handle grants only the detach operation rights;
    # the retained identity/creation time is checked before every native call.
    handle = bound.kernel.OpenProcess(0x400 | 0x800 | 0x100000, False, bound.identity['pid'])
    try:
        if not handle or owned.creation_time(bound.kernel, handle) != bound.identity['created']:
            raise RuntimeError('takeover_detach_process_identity_changed')
        bound.check()
        status = api.NtRemoveProcessDebug(handle, retained)
        if status != 0:
            raise RuntimeError('takeover_detach_status_' + format(status & 0xffffffff, '08x'))
        receipt['detached'] = True
    finally:
        if handle:
            checked(bound.kernel.CloseHandle(handle), 'takeover_detach_handle_close')
    return receipt


def run_case(context, mode, stage, *, fixture=None):
    if mode not in retention.MODES or stage not in STAGES:
        raise RuntimeError('takeover_case_invalid')
    counter, stop = context.Value('L', 0, lock=False), context.Value('b', False, lock=False)
    source_pipe, child_pipe = context.Pipe()
    read, write = context.Pipe()
    source = retention.source if fixture is None else fixture.source
    target = context.Process(target=source, args=(child_pipe, counter, stop))
    worker = None
    bound = thread = retained = intent = pending = None
    nonce = uuid.uuid4().hex
    result = dict(mode=mode, stage=stage, state='started', records=[], cleanup_errors=[],
                  owner_terminated=False, retained_before_exit=False, owner_exit_confirmed=False)
    try:
        target.start(); child_pipe.close()
        identity = owned.receive(source_pipe, 5, 'takeover_source_timeout')
        bound = owned.BoundProcess(identity, target, kernel_api())
        initial = owned.process_state(bound)
        owned.check_baseline(initial)
        if fixture is not None:
            result['fixture'] = fixture.name
            result['before_fixture'] = initial
            initial = fixture.prepare(bound, identity, initial, counter)
        result['initial_threads'] = initial
        encode_debug_context(initial[str(identity['main_tid'])]['debug_registers'])
        thread = BoundThread(bound, identity['main_tid'], initial[str(identity['main_tid'])]['thread_created'])
        worker = context.Process(target=owner, args=(write, identity, initial, mode, stage, nonce))
        worker.start(); write.close()
        until = time.monotonic() + 12
        while time.monotonic() < until:
            pipe_state, record = poll_record(read)
            if pipe_state == 'closed':
                break  # Confirm the held Process object's exit below.
            if pipe_state == 'record':
                result['records'].append(record)
                if record == {'attached': True}:
                    if retained is not None:
                        raise RuntimeError('takeover_duplicate_attach')
                    retained = retention.retain_object(bound, retention.query_api())
                    result['retained_before_exit'] = True
                    read.send({'retained': nonce})
                elif 'arm_intent' in record:
                    if intent is not None or stage != 'breakpoint' or retained is None:
                        raise RuntimeError('takeover_unexpected_arm')
                    intent = record['arm_intent']
                    if (intent.get('original') != initial[str(identity['main_tid'])]['debug_registers'] or
                            intent.get('armed') != armed_registers(intent['original'], identity['address'])):
                        raise RuntimeError('takeover_arm_baseline_mismatch')
                    journal = ROOT / '.local/live-acceptance' / ('debug-object-intent-' + nonce + '.json')
                    with journal.open('x', encoding='utf-8') as output:
                        json.dump(dict(identity=identity, thread_created=thread.created, intent=intent), output)
                        output.flush(); os.fsync(output.fileno())
                    result['intent_path'] = str(journal)
                    read.send({'arm_recorded': nonce})
                elif 'pending' in record:
                    if pending is not None or retained is None:
                        raise RuntimeError('takeover_unexpected_pending')
                    pending = validate_hit(record, identity, initial, intent)
                    if pending['stage'] != stage:
                        raise RuntimeError('takeover_wrong_checkpoint')
                    read.send({'fault_ready': nonce})
                elif record == {'awaiting_external_termination': True}:
                    if mode != 'watchdog' or pending is None:
                        raise RuntimeError('takeover_unexpected_termination')
                    worker.terminate(); result['owner_terminated'] = True
            if not worker.is_alive():
                break
        worker.join(1)
        if worker.is_alive():
            raise RuntimeError('takeover_owner_exit_timeout')
        result['records'].extend(owned.drain(read))
        result.update(owner_exit_confirmed=True, owner_exitcode=worker.exitcode)
        if not retention.expected_exit(mode, worker.exitcode, result['owner_terminated']) or pending is None:
            raise RuntimeError('takeover_unexpected_exit')
        if any('error' in item or 'cleanup_delegated' in item for item in result['records']):
            raise RuntimeError('takeover_owner_failed_before_fault')
        if mode != 'normal':
            result['recovery'] = recover(bound, thread, retained, pending, intent)
        elif not any(item.get('detached') for item in result['records']):
            raise RuntimeError('takeover_normal_detach_missing')
        checked(bound.kernel.CloseHandle(retained), 'takeover_object_close'); retained = None
        result['object_handle_closed'] = True
        result['debugger_after_close'] = retention.debugger_present(bound)
        result['stable_two_seconds'] = owned.heartbeat(counter, bound)
        result['final_threads'] = owned.process_state(bound)
        if fixture is not None:
            if not result['stable_two_seconds'] or initial != result['final_threads']:
                raise RuntimeError('takeover_fixture_baseline_not_restored')
            result['fixture_cleanup'] = fixture.finish(result['final_threads'])
        result['state'] = 'complete'
    except BaseException as exc:
        result.update(state='failed', error=owned.error_code(exc))
        if thread is not None and thread.last_register_write is not None:
            result['last_parent_register_write'] = thread.last_register_write
    finally:
        if worker is not None and worker.pid and worker.is_alive():
            result['worker_cleanup'] = owned.terminate_owned(worker)
        # A failed recovery is not retried. Kill only our disposable child
        # before dropping a still-active object with an unhandled exception.
        if retained is not None:
            result['failed_target_cleanup'] = owned.terminate_owned(target)
            if not bound.kernel.CloseHandle(retained):
                result['cleanup_errors'].append('takeover_object_close_failed')
            else:
                result['object_handle_closed'] = True
        stop.value = True
        if target.pid:
            target.join(.5)
        children = [item for item in (worker, target) if item is not None]
        result['child_cleanup'] = [owned.terminate_owned(item) for item in children]
        result['all_children_exited'] = all(item['dead'] for item in result['child_cleanup'])
        if fixture is not None:
            result['fixture_source_cleanup'] = owned.drain(source_pipe)
            result['fixture_observation'] = fixture.evidence
        for port in (fixture, thread, bound):
            if port is not None:
                try:
                    port.close()
                except Exception as exc:
                    result['cleanup_errors'].append(owned.error_code(exc))
        for pipe in (source_pipe, child_pipe, read, write):
            pipe.close()
        for process in children:
            if process.pid and not process.is_alive():
                process.close()
        result['handles_closed'] = (result['all_children_exited'] and not result['cleanup_errors'] and
                                   result.get('object_handle_closed') is True)
    result['passed'] = retention.case_qualified(result)
    result['live_eligible'] = False
    return result


def fingerprints():
    result = retention.fingerprints()
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
            hashes.get(name) != digest for name, digest in retention.SYSTEM_SHA.items()):
        raise RuntimeError('takeover_platform_or_fingerprint_mismatch')
    cases = []
    for stage in STAGES:
        for mode in retention.MODES:
            case = run_case(mp.get_context('spawn'), mode, stage)
            cases.append(case)
            print(json.dumps({key: case.get(key) for key in (
                'stage', 'mode', 'passed', 'error', 'recovery', 'stable_two_seconds', 'all_children_exited')}), flush=True)
            if not case['passed']:
                break
        if not cases[-1]['passed']:
            break
    report = dict(kind='owned_native_debug_object_takeover', cases=cases, fingerprints=hashes,
                  source_unchanged=hashes == fingerprints(), os=platform.version(),
                  dbgeng_owner_failure_recovered=False, live_eligible=False)
    report['passed'] = len(cases) == 6 and report['source_unchanged'] and all(item['passed'] for item in cases)
    destination = ROOT / '.local/live-acceptance' / ('debug-object-recovery-' + uuid.uuid4().hex + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(destination), passed=report['passed'], live_eligible=False)))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
