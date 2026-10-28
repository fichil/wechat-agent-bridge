"""Opt-in UIAutomationCore source + private PID scope + stack combination probe.

Only two newly spawned children load the fingerprinted DLL and call
UiaRaiseAutomationEvent(NULL, 20015). No provider object, window, UIA tree,
external PID, real client, elevation, global trace or permission change is used.
Each reciprocal phase keeps the same PID filter for Start/Enable/Query/Stop.
This is source instrumentation evidence, never live-diagnostic admission.
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
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_private_etw_scope as private
from scripts import probe_uia_event_source as uia

COUNT, INVALID = uia.COUNT, 0x80070057


class Scope(private.Scope):
    def properties(self):
        original, _ = super().properties()
        storage = c.create_string_buffer(144 + 4096)
        c.memmove(storage, original, 144)
        value = private.PropertiesV2.from_buffer(storage)
        value.wnode.size, value.name_offset, value.file_offset = len(storage), 144, 144 + 2048
        encoded = (self.path + '\0').encode('utf-16-le')
        c.memmove(c.addressof(storage) + value.file_offset, encoded, len(encoded))
        return storage, value

    def enable(self):
        value = super().enable()
        value.properties = 4
        return value


def returned_wide(storage, offset):
    if offset < c.sizeof(private.PropertiesV2) or offset % 2 or offset >= len(storage):
        raise ValueError('query_string_offset_invalid')
    raw = bytes(storage)[offset:]
    end = next((i for i in range(0, min(len(raw), 2048) - 1, 2) if raw[i:i + 2] == b'\0\0'), None)
    if end is None:
        raise ValueError('query_string_unterminated')
    return raw[:end].decode('utf-16-le', errors='strict')


def stop_owned_scope(api, scope, trace=None):
    """Query-produced name/path, not the shared provider GUID, prove ownership."""
    storage, p = scope.properties()
    # Input provider GUID and PID filter are required; strings must be output.
    c.memset(c.addressof(storage) + 144, 0, len(storage) - 144)
    status = api.ControlTraceW(0, scope.name, storage, 0)
    result = {'query_before': status}
    if status == 4201:
        result['absent'] = True
        return result
    if status:
        result['error'] = 'owned_query_failed'
        return result
    proof = {'guid_matches': bytes(p.wnode.guid) == uia.PROVIDER, 'name_matches': False,
             'file_matches': False, 'name_empty': None, 'file_empty': None}
    result['query_evidence'] = proof
    try:
        actual_name = returned_wide(storage, p.name_offset)
        actual_file = returned_wide(storage, p.file_offset)
        allowed_files = {os.path.normcase(os.path.abspath(x))
                         for x in (scope.path, scope.path + '_' + str(scope.pid))}
        proof.update(name_matches=actual_name == scope.name,
                     file_matches=os.path.normcase(os.path.abspath(actual_file)) in allowed_files,
                     name_empty=not bool(actual_name), file_empty=not bool(actual_file))
        if not all(proof[key] for key in ('guid_matches', 'name_matches', 'file_matches')):
            raise ValueError('owned_query_identity_mismatch')
    except (ValueError, UnicodeError) as exc:
        reason = str(exc)
        proof['validation_error'] = reason if reason in (
            'query_string_offset_invalid', 'query_string_unterminated', 'owned_query_identity_mismatch') else 'decode_error'
        result['error'] = 'owned_query_identity_unproven'
        return result
    result['actual_name_and_file_verified'] = True
    storage, p = scope.properties()
    result['stop_status'] = api.ControlTraceW(trace or 0, scope.name, storage, 1)
    result['events_lost'] = p.events_lost + p.log_lost + p.realtime_lost
    check, _ = scope.properties()
    result['query_after'] = api.ControlTraceW(0, scope.name, check, 0)
    result['absent'] = result['stop_status'] == 0 and result['query_after'] == 4201
    return result


def creation_time(kernel, handle):
    values = [uia.U64() for _ in range(4)]
    fn = uia.call(kernel, 'GetProcessTimes', c.c_int, [uia.PTR] + [c.POINTER(uia.U64)] * 4)
    if not fn(handle, *(c.byref(value) for value in values)):
        raise RuntimeError('own_process_creation_time_unavailable')
    return values[0].value


class OwnedProcesses:
    def __init__(self, identities):
        self.kernel, self.handles = c.WinDLL('kernel32', use_last_error=True), []
        self.open = uia.call(self.kernel, 'OpenProcess', uia.PTR, [uia.U32, c.c_int, uia.U32])
        self.wait = uia.call(self.kernel, 'WaitForSingleObject', uia.U32, [uia.PTR, uia.U32])
        self.close_handle = uia.call(self.kernel, 'CloseHandle', c.c_int, [uia.PTR])
        try:
            for item in identities:
                handle = self.open(0x101000, False, item['pid'])  # Query-limited + synchronize only.
                if not handle:
                    raise RuntimeError('own_process_handle_unavailable')
                self.handles.append(handle)
                if creation_time(self.kernel, handle) != item['created']:
                    raise RuntimeError('own_process_identity_changed')
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if len(self.handles) != 2 or any(self.wait(handle, 0) != 258 for handle in self.handles):
            raise RuntimeError('own_source_exited')

    def close(self):
        results = [bool(self.close_handle(handle)) for handle in self.handles]
        self.handles.clear()
        return results


class Evidence:
    """Reject other headers before delegating target stack decoding."""
    def __init__(self, selected, opposite, modules):
        self.selected, self.opposite = selected, opposite
        self.inner = uia.Evidence(*selected, modules, require_stack=True)
        self.counts = {'positive': 0, 'negative': 0, 'unknown': 0, 'malformed': 0}
        self.metadata, self.aborted = 0, False

    def consume(self, pointer):
        try:
            record = pointer.contents
            provider = bytes(record.header.provider)
            if provider == private.SESSION_METADATA:
                self.metadata += 1
                return
            if self.aborted:
                return
            identity = (record.header.pid, record.header.tid)
            if provider != uia.PROVIDER or identity not in (self.selected, self.opposite):
                self.counts['unknown'] += 1
                self.aborted = True
                return
            if identity == self.opposite:
                self.counts['negative'] += 1
                self.aborted = True
                return
            if record.header.flags & 0x20:
                raise ValueError('unexpected_32bit_header')
            self.inner.consume(pointer)
            self.counts['positive'] = self.inner.accepted
            self.counts['malformed'] = self.inner.rejected
            self.aborted = bool(self.inner.rejected)
        except BaseException:
            self.counts['malformed'] += 1
            self.aborted = True


def session_name(token, index):
    if uuid.UUID(token).hex != token or index not in (0, 1):
        raise ValueError('invalid_own_session_token')
    return 'WechatBridge-UiaPrivateScope-' + token + '-' + str(index)


def new_scope(token, index, identities, directory):
    return Scope(session_name(token, index), uia.PROVIDER, identities[index]['pid'],
                 Path(directory) / ('phase' + str(index) + '.etl'))


def initialize_source():
    if uia.uia_hash() != uia.DLL_HASH:
        raise RuntimeError('unreviewed_uia_dll')
    schemas = uia.validate_metadata(uia.Metadata())
    path = Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'
    dll = c.WinDLL(str(path), winmode=0x800)
    invoke = uia.call(dll, 'UiaRaiseAutomationEvent', c.c_int32, [uia.PTR, c.c_int])
    if invoke(None, 20015) & 0xffffffff != INVALID:
        raise RuntimeError('null_provider_warmup_failed')
    tid = uia.call(c.WinDLL('kernel32'), 'GetCurrentThreadId', uia.U32, [])()
    kernel = c.WinDLL('kernel32', use_last_error=True)
    process = uia.call(kernel, 'GetCurrentProcess', uia.PTR, [])()
    return dll, invoke, {'pid': os.getpid(), 'tid': tid, 'modules': uia.module_ranges(),
                         'metadata_hashes': schemas, 'dll_sha256': uia.DLL_HASH,
                         'created': creation_time(kernel, process)}


def validate_identities(values, own):
    if (not isinstance(values, list) or len(values) != 2 or values[0] != own or
            values[0]['pid'] == values[1]['pid']):
        raise RuntimeError('own_identity_bind_failed')
    for value in values:
        if (any(type(value.get(k)) is not int or not 0 < value[k] < 2**32 for k in ('pid', 'tid')) or
                value.get('dll_sha256') != uia.DLL_HASH or
                type(value.get('created')) is not int or not value['created'] > 0 or
                value.get('metadata_hashes') != own['metadata_hashes'] or
                not 1 <= len(value.get('modules', [])) <= 256):
            raise RuntimeError('own_identity_schema_failed')
        for base, size, name in value['modules']:
            if (type(base) is not int or type(size) is not int or not 0 < base < 2**64 or
                    not 0 < size < 2**32 or base + size >= 2**64 or
                    not isinstance(name, str) or not name or '/' in name or '\\' in name):
                raise RuntimeError('own_module_range_failed')
    return values


def invoke_four(invoke):
    results = [invoke(None, 20015) & 0xffffffff for _ in range(COUNT)]
    if results != [INVALID] * COUNT:
        raise RuntimeError('unexpected_null_provider_result')
    return results


def source_child(pipe, directory, token, owner_role):
    active, started, trace, phase, identities, owned = None, False, uia.U64(), None, None, None
    result, emitted, phases = {'cleanup': []}, set(), set()
    try:
        private.check_abi()
        dll, invoke, own = initialize_source()
        api = private.controller()
        pipe.send({'ready': own})
        deadline, phase_deadline = time.monotonic() + 30, None
        while time.monotonic() < deadline:
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                raise RuntimeError('private_phase_timeout')
            if not pipe.poll(.05):
                continue
            command = pipe.recv()
            if command == 'done':
                break
            if not isinstance(command, dict):
                raise RuntimeError('invalid_source_command')
            if 'bind' in command and owner_role and identities is None:
                identities = validate_identities(command['bind'], own)
                owned = OwnedProcesses(identities)
                pipe.send({'bound': True})
            elif 'phase' in command and owner_role and identities is not None and active is None:
                index = command['phase']
                if index not in (0, 1) or index in phases:
                    raise RuntimeError('invalid_or_duplicate_phase')
                phases.add(index)
                phase_deadline = time.monotonic() + 5
                phase = {'index': index, 'trace_started': False, 'source_results': []}
                active = new_scope(token, index, identities, directory)
                owned.check()
                storage, _ = active.properties()
                phase['start_status'] = api.StartTraceW(c.byref(trace), active.name, storage)
                if phase['start_status']:
                    # Shared UIA GUID is not ownership: never stop a failed start.
                    raise RuntimeError(f'private_start_failed_{phase["start_status"]}')
                started = phase['trace_started'] = True
                pipe.send({'created': index, 'name': active.name})
                owned.check()
                params = active.enable()
                phase['enable_status'] = api.EnableTraceEx2(trace, uia.GUID.from_buffer_copy(uia.PROVIDER),
                                                           1, 4, 0x10000, 0, 1000, c.byref(params))
                if phase['enable_status']:
                    raise RuntimeError(f'private_enable_failed_{phase["enable_status"]}')
                pipe.send({'phase_ready': index})
            elif 'emit' in command:
                index = command['emit']
                if index not in (0, 1) or index in emitted or (owner_role and
                        (not started or phase is None or phase['index'] != index)):
                    raise RuntimeError('unprepared_or_duplicate_emission')
                emitted.add(index)
                if owner_role:
                    owned.check()
                results = invoke_four(invoke)
                if owner_role:
                    phase['source_results'] = results
                pipe.send({'emitted': index, 'results': results})
            elif command == {'finish_phase': True} and owner_role and started:
                phase['cleanup'] = stop_owned_scope(api, active, trace)
                phase['duration_seconds'] = 5 - (phase_deadline - time.monotonic())
                if not phase['cleanup'].get('absent'):
                    raise RuntimeError('private_stop_unconfirmed')
                started = False
                index = phase['index']
                selected, opposite = identities[index], identities[1 - index]
                capture = Evidence((selected['pid'], selected['tid']), (opposite['pid'], opposite['tid']),
                                   selected['modules'])
                phase.update(private.consume_files(api, Path(directory), 'phase' + str(index), capture))
                phase['stacks'], phase['rejection_reasons'] = capture.inner.stacks, capture.inner.reasons
                pipe.send({'phase_finished': phase})
                active, phase, phase_deadline = None, None, None
            else:
                raise RuntimeError('unexpected_source_command')
        else:
            raise RuntimeError('source_lifetime_timeout')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ':' + str(exc)
        if phase is not None:
            result['incomplete_phase'] = phase
    finally:
        if started:
            result['cleanup'].append(private.safe(lambda: stop_owned_scope(api, active, trace)))
        if owned is not None:
            result['process_handles_closed'] = private.safe(owned.close)
        result['dll_unchanged'] = private.safe(lambda: uia.uia_hash() == uia.DLL_HASH)
        try:
            pipe.send({'finished': result})
        except (OSError, EOFError):
            pass
        pipe.close()


def phase_passed(phase):
    return bool(phase.get('trace_started') and phase.get('start_status') == 0 and
        phase.get('enable_status') == 0 and phase.get('source_results') == [INVALID] * COUNT and
        phase.get('opposite_results') == [INVALID] * COUNT and phase.get('duration_seconds', 99) <= 5 and
        phase.get('file_count') == 1 and phase.get('process_status') == [0] and phase.get('close_status') == [0] and
        phase.get('received') == {'positive': 2 * COUNT, 'negative': 0, 'unknown': 0, 'malformed': 0} and
        len(phase.get('stacks', [])) == 2 * COUNT and phase.get('cleanup', {}).get('absent') and
        phase['cleanup'].get('actual_name_and_file_verified') is True and
        phase['cleanup'].get('stop_status') == 0 and phase['cleanup'].get('events_lost') == 0)


def fingerprints():
    names = ('probe_uia_private_scope.py', 'probe_private_etw_scope.py',
             'probe_uia_event_source.py', 'test_focus_etw.py', 'inspect_focus_events.py')
    return {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in names}


def finish_children(entries, active_index, created, token, identities, temporary):
    """Stop owner first; shared GUID alone never authorizes a fallback stop."""
    children, unknown = [], False
    for index, (process, pipe) in enumerate(entries):
        item = {'index': index, 'terminated': False}
        if process.pid is not None:
            def finish():
                pipe.send('done')
                reply = private.receive(pipe, 2)
                item['result'] = reply.get('finished', reply)
            item['finish'] = private.safe(finish)
            item['join'] = private.safe(lambda: process.join(1))
            if process.is_alive():
                item['termination'] = private.safe(process.terminate)
                item['final_join'] = private.safe(lambda: process.join(2))
                item['terminated'] = True
            item['dead'], item['exitcode'] = not process.is_alive(), process.exitcode
        else:
            item['dead'], item['exitcode'] = True, None
        if index == 0 and active_index is not None:
            if item['dead']:
                owned = new_scope(token, active_index, identities, temporary)
                # Even a lost create acknowledgement requires actual output name/path.
                item['fallback_cleanup'] = private.safe(lambda: stop_owned_scope(private.controller(), owned))
                unknown = not item['fallback_cleanup'].get('absent', False)
            else:
                unknown = True
        item['pipe_close'] = private.safe(pipe.close)
        children.append(item)
    return children, unknown


def run():
    private.check_abi()
    source, token = fingerprints(), uuid.uuid4().hex
    report = {'research_only': True, 'live_diagnostic_ready': False, 'focus_cause_verified': False,
              'windows_operated': False, 'passed': False, 'trace_started': False, 'phases': [],
              'fingerprints': source, 'os': platform.version(), 'dll_sha256': uia.DLL_HASH}
    directory = ROOT / '.local/live-acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    entries, identities, active_index, created, temporary = [], [], None, False, None
    try:
        temporary = Path(tempfile.mkdtemp(prefix='uia-private-scope-own-', dir=directory))
        ctx = mp.get_context('spawn')
        for index in range(2):
            local, remote = ctx.Pipe()
            process = ctx.Process(target=source_child, args=(remote, str(temporary), token, index == 0))
            entries.append((process, local))
            try:
                process.start()
            finally:
                remote.close()
            message = private.receive(local, 5)
            own = message.get('ready', {})
            if own.get('pid') != process.pid or own.get('dll_sha256') != uia.DLL_HASH:
                report['source_failure'] = message.get('finished', {})
                raise RuntimeError('source_handshake_failed')
            identities.append(own)
        entries[0][1].send({'bind': identities})
        if private.receive(entries[0][1], 1) != {'bound': True}:
            raise RuntimeError('source_identity_bind_failed')
        for index in range(2):
            if not all(process.is_alive() for process, _ in entries):
                raise RuntimeError('source_exited_before_phase')
            active_index, created = index, False
            owner = entries[0][1]
            owner.send({'phase': index})
            message = private.receive(owner, 4)
            if message != {'created': index, 'name': session_name(token, index)}:
                report['source_failure'] = message.get('finished', {})
                failed = report['source_failure'].get('incomplete_phase', {})
                if failed.get('start_status') not in (None, 0):
                    active_index = None  # Explicit failed StartTrace owns no session.
                raise RuntimeError('private_creation_not_acknowledged')
            created = report['trace_started'] = True
            if private.receive(owner, 2) != {'phase_ready': index}:
                raise RuntimeError('private_enable_not_ready')
            emission_results = []
            for process, pipe in entries:
                if not all(source_process.is_alive() for source_process, _ in entries):
                    raise RuntimeError('source_exited')
                pipe.send({'emit': index})
                emitted = private.receive(pipe, 1)
                if emitted != {'emitted': index, 'results': [INVALID] * COUNT}:
                    raise RuntimeError('synthetic_uia_emission_failed')
                emission_results.append(emitted['results'])
            owner.send({'finish_phase': True})
            message = private.receive(owner, 4)
            if 'phase_finished' not in message:
                report['source_failure'] = message.get('finished', {})
                raise RuntimeError('private_phase_incomplete')
            phase = message['phase_finished']
            phase['source_results'] = emission_results[index]
            phase['opposite_results'] = emission_results[1 - index]
            report['phases'].append(phase)
            active_index, created = None, False
            if not phase_passed(phase):
                raise RuntimeError('combined_uia_scope_stack_failed')
    except BaseException as exc:
        report['error'] = type(exc).__name__ + ':' + str(exc)
    finally:
        report['children'], report['cleanup_unknown'] = finish_children(
            entries, active_index, created, token, identities, temporary)
        report['all_children_dead'] = bool(entries) and all(not p.is_alive() for p, _ in entries)
        if temporary is not None and report['all_children_dead'] and not report['cleanup_unknown']:
            def remove_temp():
                target = temporary.resolve()
                if target.parent != directory.resolve() or not target.name.startswith('uia-private-scope-own-'):
                    raise RuntimeError('temporary_directory_scope_changed')
                shutil.rmtree(target)
                return not target.exists()
            report['temporary_files_removed'] = private.safe(remove_temp)
        report['source_unchanged'] = private.safe(lambda: fingerprints() == source)
        report['passed'] = bool(not report.get('error') and len(report['phases']) == 2 and
            all(phase_passed(p) for p in report['phases']) and len(report['children']) == 2 and
            all(x.get('dead') and x.get('exitcode') == 0 and not x.get('terminated') and
                x.get('result', {}).get('dll_unchanged') is True and not x.get('result', {}).get('error')
                for x in report['children']) and report['all_children_dead'] and not report['cleanup_unknown'] and
            report['children'][0].get('result', {}).get('process_handles_closed') == [True, True] and
            report.get('temporary_files_removed') is True and report['source_unchanged'] is True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args()
    if not args.run_synthetic:
        print(json.dumps({'trace_started': False, 'live_diagnostic_ready': False}))
        return 0
    destination = ROOT / '.local/live-acceptance/uia-private-scope-stack.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'state': 'running', 'passed': False}), encoding='utf-8')
    try:
        report = run()
    except BaseException as exc:
        report = {'passed': False, 'live_diagnostic_ready': False,
                  'error': type(exc).__name__ + ':' + str(exc)}
    report['state'] = 'finished'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'phases'}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
