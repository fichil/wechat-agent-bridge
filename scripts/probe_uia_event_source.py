"""Own-process UIAutomationCore ETW source probe; never a live admission report.

Explicit --run-synthetic loads the fingerprinted system DLL in a disposable child,
calls UiaRaiseAutomationEvent(NULL, 20015), and expects E_INVALIDARG. No provider
object, UI event, window, desktop, debugger, external PID or network is used.
The private in-process file session cannot collect another process. Its temporary
ETL is bounded and removed after consumption. This does not test cross-process
scope filtering, successful UI notifications, or the cause of window activation.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import shutil
import struct
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_focus_etw import (U32, U64, PTR, GUID, Properties, Descriptor,
    Logfile, Enable, CALLBACK, Record, SESSION_METADATA, abi_check, call,
    controller, module_ranges)
from scripts.inspect_focus_events import Metadata, parse_event

PROVIDER = uuid.UUID('820a42d8-38c4-465d-b64e-d7d56ea1d612').bytes_le
DLL_HASH = 'be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989'
EVENT = bytes(Descriptor(119, 0, 17, 4, 1, 119, 0x4000000000010000))
STOP_EVENT = bytes(Descriptor(120, 0, 17, 4, 2, 119, 0x4000000000010000))
COUNT = 4
SESSION = 'WechatBridge-UiaEntry-OwnProcess'
PRIVATE_MODE = 0x00020801  # PRIVATE_LOGGER | PRIVATE_IN_PROC | FILE_SEQUENTIAL


def file_properties(path):
    name = (SESSION + '\0').encode('utf-16-le')
    filename = (str(path) + '\0').encode('utf-16-le')
    storage = c.create_string_buffer(c.sizeof(Properties) + len(name) + len(filename))
    p = Properties.from_buffer(storage)
    p.wnode.size, p.wnode.flags, p.wnode.clock = len(storage), 0x20000, 1
    p.wnode.guid[:] = PROVIDER  # Private sessions require the provider GUID.
    p.buffer_kb, p.minimum, p.maximum, p.maximum_file = 64, 2, 4, 4
    p.mode = PRIVATE_MODE
    p.name_offset, p.file_offset = c.sizeof(Properties), c.sizeof(Properties) + len(name)
    c.memmove(c.addressof(storage) + p.name_offset, name, len(name))
    c.memmove(c.addressof(storage) + p.file_offset, filename, len(filename))
    return storage, p


def validate_metadata(meta):
    provider = GUID.from_buffer_copy(PROVIDER)
    hashes = []
    for expected in (EVENT, STOP_EVENT):
        descriptor = Descriptor.from_buffer_copy(expected)
        raw = meta.buffer('TdhGetManifestEventInformation', provider, c.byref(descriptor))
        event = parse_event(raw)
        if (raw[:16] != PROVIDER or raw[32:48] != expected or event['fields'] or
                event['provider'] != 'Microsoft-Windows-UIAutomationCore' or
                event['task'] != 'UIAutomationCoreAPI_UiaRaiseEvent'):
            raise RuntimeError('uia_event_schema_changed')
        hashes.append(hashlib.sha256(raw).hexdigest())
    return hashes


class Evidence:
    def __init__(self, pid, tid, modules, require_stack=True):
        self.pid, self.tid, self.modules = pid, tid, modules
        self.require_stack = require_stack
        self.accepted = self.rejected = self.metadata = 0
        self.stacks, self.reasons = [], {}

    def consume(self, pointer):
        try:
            event = pointer.contents
            if bytes(event.header.provider) == SESSION_METADATA:
                self.metadata += 1
                return
            # Reject unknown ownership/schema before any payload/extended access.
            if (bytes(event.header.provider) != PROVIDER or
                    (event.header.pid, event.header.tid) != (self.pid, self.tid) or
                    bytes(event.header.descriptor) != (EVENT if self.accepted % 2 == 0 else STOP_EVENT) or
                    event.data_length != 0 or not event.header.flags & 0x40 or self.accepted >= 2 * COUNT):
                raise ValueError('unexpected_event')
            if not self.require_stack:
                self.accepted += 1
                return  # Entry-only phase never reads extended data.
            if not 0 < event.extended_count <= 16 or not event.extended:
                raise ValueError('missing_extended_data')
            stacks = []
            for item in event.extended[:event.extended_count]:
                if item.kind != 6:  # EVENT_HEADER_EXT_TYPE_STACK_TRACE64
                    continue
                if not item.data or item.size < 16 or item.size > 8 + 192 * 8 or item.size % 8:
                    raise ValueError('invalid_stack')
                raw = c.string_at(item.data, item.size)
                addresses = struct.unpack('<' + 'Q' * ((len(raw) - 8) // 8), raw[8:])
                frames = []
                for address in addresses:
                    if not address:
                        continue
                    matches = [(name, address - base) for base, size, name in self.modules
                               if base <= address < base + size]
                    if len(matches) != 1:
                        raise ValueError('stack_module_unknown')
                    name, rva = matches[0]
                    frames.append({'module': name, 'rva': rva})
                # Entry must actually appear on the same caller's stack.
                if not any(f['module'].lower() == 'uiautomationcore.dll' and
                           0x104270 <= f['rva'] < 0x104904 for f in frames):
                    raise ValueError('uia_entry_missing_from_stack')
                stacks.append(frames[:64])
            if len(stacks) != 1:
                raise ValueError('missing_or_duplicate_stack')
            self.stacks.append(stacks[0])
            self.accepted += 1
        except BaseException as exc:
            self.rejected += 1
            reason = str(exc) if isinstance(exc, ValueError) else 'callback_error'
            self.reasons[reason] = self.reasons.get(reason, 0) + 1


def child_run(path, stacks=True):
    abi_check()
    dll_path = Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'
    if hashlib.sha256(dll_path.read_bytes()).hexdigest() != DLL_HASH:
        raise RuntimeError('unreviewed_uia_dll')
    metadata_hash = validate_metadata(Metadata())
    dll = c.WinDLL(str(dll_path), winmode=0x800)
    raise_event = call(dll, 'UiaRaiseAutomationEvent', c.c_int32, [PTR, c.c_int])
    # Initialize the provider before StartTrace without a real provider object.
    if raise_event(None, 20015) & 0xffffffff != 0x80070057:
        raise RuntimeError('unexpected_null_provider_result')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    tid = call(kernel, 'GetCurrentThreadId', U32, [])()
    api, trace, consumer = controller(), U64(), U64(0xffffffffffffffff)
    result = {'trace_started': False, 'enable_status': None, 'metadata_hash': metadata_hash,
              'api_results': [], 'cleanup': {}, 'source_kind': 'null_provider_api_entry',
              'stack_requested': stacks, 'filter_mode': 'private_in_process_scope_only'}
    callback = evidence = None
    started = False
    try:
        storage, _ = file_properties(path)
        status = api.StartTraceW(c.byref(trace), SESSION, storage)
        if status:
            raise RuntimeError(f'private_start_failed_{status}')
        started = result['trace_started'] = True
        result['private_handle_bit24'] = bool(trace.value & (1 << 24))
        # This OS rejects EVENT_ID and STACKWALK filters on private handles.
        # Isolation remains PRIVATE_IN_PROC: no other process can be recorded.
        # Only our four NULL calls execute; the consumer requires exact 119/120
        # pairs and rejects any other source event. Never used for a live target.
        params = Enable(2, 4 if stacks else 0, 0, GUID.from_buffer_copy(PROVIDER), None, 0)
        status = api.EnableTraceEx2(trace, GUID.from_buffer_copy(PROVIDER), 1,
                                  4, 0x10000, 0, 1000, c.byref(params))
        result['enable_status'] = status
        if status:
            raise RuntimeError(f'private_enable_failed_{status}')
        for _ in range(COUNT):
            result['api_results'].append(raise_event(None, 20015) & 0xffffffff)
        # Private file consumption happens only after the session has stopped.
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if started:
            storage, p = file_properties(path)
            status = api.ControlTraceW(trace, SESSION, storage, 1)
            result['cleanup']['stop_status'] = status
            result['cleanup']['events_lost'] = p.events_lost + p.log_lost + p.realtime_lost
            check, _ = file_properties(path)
            result['cleanup']['query_after_stop'] = api.ControlTraceW(0, SESSION, check, 0)
    if result.get('error') or result['cleanup'].get('stop_status') != 0:
        return result
    try:
        evidence = Evidence(os.getpid(), tid, module_ranges(), require_stack=stacks)
        callback = CALLBACK(None, c.POINTER(Record))(evidence.consume)
        log = Logfile()
        log.file, log.mode = str(path), 0x10000000  # EVENT_RECORD, no REAL_TIME.
        log.callback = c.cast(callback, PTR)
        consumer.value = api.OpenTraceW(c.byref(log))
        if consumer.value == 0xffffffffffffffff:
            raise RuntimeError(f'open_private_file_failed_{c.get_last_error()}')
        result['process_status'] = api.ProcessTrace(c.byref(consumer), 1, None, None)
        result['accepted'], result['rejected'] = evidence.accepted, evidence.rejected
        result['stacks'], result['session_metadata_count'] = evidence.stacks, evidence.metadata
        result['rejection_reasons'] = evidence.reasons
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if consumer.value != 0xffffffffffffffff:
            result['cleanup']['close_status'] = api.CloseTrace(consumer)
    return result


def uia_hash():
    return hashlib.sha256((Path(os.environ['SystemRoot']) /
                           'System32/UIAutomationCore.dll').read_bytes()).hexdigest()


def own_child_watchdog(done, parent, deadline):
    while not done.wait(.1):
        if not parent.is_alive() or time.monotonic() >= deadline:
            os._exit(43)  # Only this disposable child, no attached process/session outside it.


def child(pipe, path, stacks):
    done = threading.Event()
    monitor = threading.Thread(target=own_child_watchdog,
                               args=(done, mp.parent_process(), time.monotonic() + 20), daemon=True)
    monitor.start()
    try:
        result = child_run(Path(path), stacks)
    except BaseException as exc:
        result = {'error': str(exc)}
    try:
        result['dll_unchanged'] = uia_hash() == DLL_HASH
    except OSError:
        result['dll_unchanged'] = False
    try:
        pipe.send(result)
    finally:
        done.set()
        monitor.join(1)
        pipe.close()


def entry_passed(result):
    return bool(not result.get('error') and result.get('trace_started') and
                result.get('enable_status') == 0 and result.get('dll_unchanged') and
                result.get('api_results') == [0x80070057] * COUNT and
                result.get('process_status') == 0 and result.get('accepted') == 2 * COUNT and
                result.get('rejected') == 0 and
                result.get('cleanup') == {'stop_status': 0, 'events_lost': 0,
                                          'query_after_stop': 4201, 'close_status': 0})


def passed(result):
    return bool(entry_passed(result) and result.get('stack_requested') is True and
                len(result.get('stacks', [])) == 2 * COUNT)


def finalize_owned(worker, temp, directory):
    result = {'child_terminated': False, 'temporary_files_removed': False, 'cleanup_errors': []}
    try:
        if worker is not None and worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
                worker.join(2)
            result['child_terminated'] = not worker.is_alive() and worker.exitcode is not None
    except Exception:
        result['cleanup_errors'].append('child_exit_unconfirmed')
    if temp is not None and (worker is None or worker.pid is None or result['child_terminated']):
        try:
            resolved = temp.resolve()
            if resolved.parent != directory.resolve() or not resolved.name.startswith('uia-own-'):
                raise RuntimeError('temporary_directory_scope_changed')
            shutil.rmtree(resolved)
            result['temporary_files_removed'] = not resolved.exists()
        except Exception:
            result['cleanup_errors'].append('temporary_cleanup_failed')
    return result


def fingerprints():
    return {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
            for name in ('probe_uia_event_source.py', 'test_focus_etw.py', 'inspect_focus_events.py')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--stage', choices=('entry', 'stack'), default='stack')
    args = parser.parse_args()
    report = {'research_only': True, 'live_diagnostic_ready': False,
              'cross_process_scope_verified': False, 'focus_cause_verified': False,
              'windows_operated': False, 'dll_sha256': DLL_HASH, 'passed': False,
              'stage': args.stage}
    if not args.run_synthetic:
        print(json.dumps(report))
        return 0
    directory = ROOT / '.local/live-acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'uia-event-source-private-{args.stage}.json'
    source = fingerprints()
    destination.write_text(json.dumps(dict(report, state='running', fingerprints=source)), encoding='utf-8')
    ctx = mp.get_context('spawn')
    read, write = ctx.Pipe(duplex=False)
    worker, temp = None, None
    try:
        temp = Path(tempfile.mkdtemp(prefix='uia-own-', dir=directory))
        worker = ctx.Process(target=child, args=(write, str(temp / 'source.etl'), args.stage == 'stack'))
        worker.start()
        write.close()
        if not read.poll(15):
            raise TimeoutError('private_child_timeout')
        report['result'] = read.recv()
        worker.join(2)
        if worker.is_alive() or worker.exitcode != 0:
            raise RuntimeError('private_child_exit_unconfirmed')
        report['child_exitcode'] = worker.exitcode
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        report.update(finalize_owned(worker, temp, directory))
        for pipe in (read, write):
            try:
                pipe.close()
            except OSError:
                report['cleanup_errors'].append('pipe_close_failed')
    try:
        unchanged = source == fingerprints()
    except OSError:
        unchanged = False
    report.update(state='finished', os=platform.version(), fingerprints=source, source_unchanged=unchanged)
    clean = bool(not report.get('error') and not report['cleanup_errors'] and report['child_terminated'] and
                 report.get('temporary_files_removed') and report['source_unchanged'])
    report['entry_validation_passed'] = clean and entry_passed(report.get('result', {}))
    report['passed'] = clean and passed(report.get('result', {}))
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('fingerprints', 'result')}, indent=2))
    if report.get('result'):
        print(json.dumps({k: v for k, v in report['result'].items() if k != 'stacks'}, indent=2))
    return 0 if report['passed' if args.stage == 'stack' else 'entry_validation_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
