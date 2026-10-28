"""Opt-in, own-process-pair private ETW PID-scope experiment.

Only two newly spawned children register a fresh random synthetic provider.
The first child controls two reciprocal PRIVATE_LOGGER file sessions. The same
one-PID filter is passed to StartTrace, EnableTraceEx2 and every ControlTrace.
No UIA, windows, external PID, event/payload filters, stacks, elevation or
permission changes are used. A pass is not live-client diagnostic admission.
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
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_focus_etw import (U32, U64, PTR, GUID, Properties, Descriptor,
    Filter, Enable, Record, Logfile, CALLBACK, SESSION_METADATA, abi_check, call, controller)
from scripts.inspect_focus_events import Metadata, parse_event

COUNT = 4
EVENT = bytes(Descriptor(2, 0, 0, 4, 0, 0, 0x2000))
PID_FILTER = 0x80000004
PRIVATE_MODE = 0x801  # PRIVATE_LOGGER + FILE_SEQUENTIAL, never IN_PROC/REAL_TIME.


class PropertiesV2(c.Structure):
    _fields_ = Properties._fields_ + [('v2_control', U32), ('filter_count', U32),
                                    ('filters', c.POINTER(Filter)), ('options', U64)]


class EventData(c.Structure):
    _fields_ = [('pointer', U64), ('size', U32), ('reserved', U32)]


def check_abi():
    abi_check()
    if (c.sizeof(PropertiesV2) != 144 or PropertiesV2.v2_control.offset != 120 or
            PropertiesV2.filters.offset != 128 or PropertiesV2.options.offset != 136 or
            c.sizeof(EventData) != 16):
        raise RuntimeError('private_scope_abi_mismatch')


def manifest_text(provider):
    ns = 'http://schemas.microsoft.com/win/2004/08/events'
    ET.register_namespace('', ns)
    root = ET.Element('{' + ns + '}instrumentationManifest',
                      {'xmlns:win': 'http://manifests.microsoft.com/win/2004/08/windows/events'})
    events = ET.SubElement(ET.SubElement(root, 'instrumentation'), 'events')
    node = ET.SubElement(events, 'provider', {'name': 'WechatBridge-PrivateScope-' + provider.hex,
        'guid': '{' + str(provider) + '}', 'symbol': 'SyntheticScope',
        'resourceFileName': sys.executable, 'messageFileName': sys.executable})
    ET.SubElement(ET.SubElement(node, 'keywords'), 'keyword', {'name': 'Canary', 'mask': '0x2000'})
    template = ET.SubElement(ET.SubElement(node, 'templates'), 'template', {'tid': 'Pair'})
    for name in ('Sequence', 'Nonce'):
        ET.SubElement(template, 'data', {'name': name, 'inType': 'win:UInt32'})
    ET.SubElement(ET.SubElement(node, 'events'), 'event', {'value': '2', 'version': '0',
                  'level': 'win:Informational', 'keywords': 'Canary', 'template': 'Pair'})
    return ET.tostring(root, encoding='unicode')


class Scope:
    """Keep native filter pointers alive and identical for all session APIs."""
    def __init__(self, name, provider, pid, path):
        if not isinstance(pid, int) or not 0 < pid < 2**32:
            raise ValueError('invalid_synthetic_pid')
        self.name, self.provider, self.pid, self.path = name, provider, pid, str(path)
        self.pid_value = U32(pid)
        self.filters = (Filter * 1)(Filter(c.addressof(self.pid_value), 4, PID_FILTER))

    def properties(self):
        name_bytes = (len(self.name) + 1) * 2
        encoded = (self.path + '\0').encode('utf-16-le')
        if len(encoded) > 2048:
            raise ValueError('temporary_path_too_long')
        storage = c.create_string_buffer(c.sizeof(PropertiesV2) + name_bytes + 2048)
        p = PropertiesV2.from_buffer(storage)
        p.wnode.size, p.wnode.flags, p.wnode.clock = len(storage), 0x820000, 1
        p.wnode.guid[:] = self.provider
        p.buffer_kb, p.minimum, p.maximum, p.maximum_file = 64, 2, 4, 4
        p.mode, p.v2_control, p.filter_count = PRIVATE_MODE, 2, 1
        p.filters = self.filters
        p.name_offset, p.file_offset = 144, 144 + name_bytes
        c.memmove(c.addressof(storage) + p.file_offset, encoded, len(encoded))
        return storage, p

    def enable(self):
        return Enable(2, 0, 0, GUID.from_buffer_copy(self.provider), self.filters, 1)


def safe(action):
    try:
        return action()
    except BaseException as exc:
        return {'error': type(exc).__name__ + ':' + str(exc)}


def stop_scope(api, scope, trace=None):
    storage, p = scope.properties()
    status = api.ControlTraceW(0, scope.name, storage, 0)
    result = {'query_before': status}
    if status == 4201:
        result['absent'] = True
        return result
    if status or bytes(p.wnode.guid) != scope.provider:
        result['error'] = 'scope_query_or_identity_failed'
        return result
    storage, p = scope.properties()
    status = api.ControlTraceW(trace or 0, scope.name, storage, 1)
    result.update(stop_status=status, events_lost=p.events_lost + p.log_lost + p.realtime_lost)
    check, _ = scope.properties()
    result['query_after'] = api.ControlTraceW(0, scope.name, check, 0)
    result['absent'] = status == 0 and result['query_after'] == 4201
    return result


def validate_manifest(meta, provider):
    descriptor = Descriptor.from_buffer_copy(EVENT)
    raw = meta.buffer('TdhGetManifestEventInformation', GUID.from_buffer_copy(provider), c.byref(descriptor))
    fields = [(f['name'], f['in_type'], f['flags'], f['count_or_index']) for f in parse_event(raw)['fields']]
    if raw[:16] != provider or raw[32:48] != EVENT or fields != [('Sequence', 8, 0, 1), ('Nonce', 8, 0, 1)]:
        raise RuntimeError('synthetic_manifest_mismatch')
    return hashlib.sha256(raw).hexdigest()


class Evidence:
    def __init__(self, provider, selected, opposite, nonce):
        self.provider, self.selected, self.opposite, self.nonce = provider, selected, opposite, nonce
        self.counts = {'positive': 0, 'negative': 0, 'unknown': 0, 'malformed': 0}
        self.metadata, self.sequences, self.aborted = 0, set(), False

    def consume(self, pointer):
        try:
            record = pointer.contents
            provider = bytes(record.header.provider)
            if provider == SESSION_METADATA:
                self.metadata += 1
                return
            if self.aborted:
                return
            if provider != self.provider:
                self.counts['unknown'] += 1
                self.aborted = True
                return
            identity = (record.header.pid, record.header.tid)
            # The excluded process is counted solely from its known header.
            if identity == self.opposite:
                self.counts['negative'] += 1
                self.aborted = True
                return
            if identity != self.selected:
                self.counts['unknown'] += 1
                self.aborted = True
                return
            if (bytes(record.header.descriptor) != EVENT or record.data_length != 8 or
                    not record.data or not record.header.flags & 0x40 or record.header.flags & 0x20):
                raise ValueError('malformed_synthetic_header')
            sequence, nonce = struct.unpack('<II', c.string_at(record.data, 8))
            if nonce != self.nonce or sequence not in range(COUNT) or sequence in self.sequences:
                raise ValueError('invalid_or_duplicate_synthetic_payload')
            self.sequences.add(sequence)
            self.counts['positive'] += 1
            # No extended-data or stack access anywhere in this experiment.
        except BaseException:
            self.counts['malformed'] += 1
            self.aborted = True


def private_files(directory, stem, pids):
    names = {stem + '.etl'} | {stem + '.etl_' + str(pid) for pid in pids}
    paths = sorted(directory.glob(stem + '*'))
    if not 1 <= len(paths) <= 2:
        raise RuntimeError('unexpected_private_file_count')
    if any(path.name not in names for path in paths):
        raise RuntimeError('unexpected_private_filename')
    return paths


def consume_files(api, directory, stem, evidence):
    paths = private_files(directory, stem, (evidence.selected[0], evidence.opposite[0]))
    statuses, closes = [], []
    callback = CALLBACK(None, c.POINTER(Record))(evidence.consume)
    for path in paths:
        if path.is_symlink() or path.resolve().parent != directory.resolve() or path.stat().st_size > 4 * 1024**2:
            raise RuntimeError('private_file_scope_or_limit')
        log = Logfile()
        log.file, log.mode, log.callback = str(path), 0x10000000, c.cast(callback, PTR)
        handle = U64(api.OpenTraceW(c.byref(log)))
        if handle.value == 0xffffffffffffffff:
            raise RuntimeError('open_private_file_failed')
        try:
            statuses.append(api.ProcessTrace(c.byref(handle), 1, None, None))
        finally:
            closes.append(api.CloseTrace(handle))
    return {'file_count': len(paths), 'process_status': statuses, 'close_status': closes,
            'received': evidence.counts, 'session_metadata': evidence.metadata}


def emit(api, registered, nonce):
    if type(nonce) is not int or not 0 < nonce < 2**32:
        raise RuntimeError('invalid_nonce')
    event = Descriptor.from_buffer_copy(EVENT)
    for sequence in range(COUNT):
        raw = c.create_string_buffer(struct.pack('<II', sequence, nonce))
        data = EventData(c.addressof(raw), 8, 0)
        status = api.EventWrite(registered, c.byref(event), 1, c.byref(data))
        if status:
            raise RuntimeError(f'synthetic_write_failed_{status}')
    return COUNT


def source_child(pipe, provider, manifest, directory, controller_role):
    registered, active, trace, meta, loaded = U64(), None, U64(), None, False
    result = {'unregister_status': None, 'manifest_unload_status': None, 'cleanup': []}
    phases, emitted, identities = set(), set(), None
    try:
        check_abi()
        api = controller()
        call(api, 'EventRegister', U32, [PTR, PTR, PTR, c.POINTER(U64)])
        call(api, 'EventWrite', U32, [U64, c.POINTER(Descriptor), U32, c.POINTER(EventData)])
        call(api, 'EventUnregister', U32, [U64])
        status = api.EventRegister(GUID.from_buffer_copy(provider), None, None, c.byref(registered))
        if status or not registered.value:
            raise RuntimeError(f'synthetic_register_failed_{status}')
        tid = call(c.WinDLL('kernel32'), 'GetCurrentThreadId', U32, [])()
        if controller_role:
            meta = Metadata()
            call(meta.dll, 'TdhLoadManifest', U32, [c.c_wchar_p])
            call(meta.dll, 'TdhUnloadManifest', U32, [c.c_wchar_p])
            status = meta.dll.TdhLoadManifest(manifest)
            if status:
                raise RuntimeError(f'synthetic_manifest_load_failed_{status}')
            loaded = True
            result['metadata_hash'] = validate_manifest(meta, provider)
        pipe.send({'ready': True, 'pid': os.getpid(), 'tid': tid})
        deadline, phase_deadline, phase = time.monotonic() + 30, None, None
        while time.monotonic() < deadline:
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                raise RuntimeError('private_phase_timeout')
            if not pipe.poll(0.05):
                continue
            command = pipe.recv()
            if command == 'done':
                break
            if not isinstance(command, dict):
                raise RuntimeError('invalid_source_command')
            if 'bind' in command and controller_role and identities is None:
                candidate = command['bind']
                if (len(candidate) != 2 or tuple(candidate[0]) != (os.getpid(), tid) or
                        any(len(x) != 2 or any(type(v) is not int or not 0 < v < 2**32 for v in x) for x in candidate) or
                        candidate[0][0] == candidate[1][0]):
                    raise RuntimeError('synthetic_identity_bind_failed')
                identities = [tuple(x) for x in candidate]
                pipe.send({'bound': True})
            elif 'phase' in command and controller_role and identities is not None and active is None:
                index, nonce = command['phase'], command['nonce']
                if index not in (0, 1) or index in phases or type(nonce) is not int or not 0 < nonce < 2**32:
                    raise RuntimeError('invalid_phase')
                phases.add(index)
                phase_deadline = time.monotonic() + 5
                name = 'WechatBridge-PrivateScope-' + uuid.UUID(bytes_le=provider).hex + '-' + str(index)
                path = Path(directory) / ('phase' + str(index) + '.etl')
                active = Scope(name, provider, identities[index][0], path)
                phase = {'index': index, 'trace_started': False, 'nonce': nonce, 'sent': 0}
                storage, _ = active.properties()
                phase['start_status'] = api.StartTraceW(c.byref(trace), name, storage)
                if phase['start_status']:
                    raise RuntimeError(f'private_start_failed_{phase["start_status"]}')
                phase['trace_started'] = True
                params = active.enable()
                phase['enable_status'] = api.EnableTraceEx2(trace, GUID.from_buffer_copy(provider), 1,
                                                            4, 0x2000, 0, 1000, c.byref(params))
                if phase['enable_status']:
                    raise RuntimeError(f'private_enable_failed_{phase["enable_status"]}')
                pipe.send({'phase_ready': index})
            elif 'emit' in command:
                nonce = command['emit']
                if nonce in emitted or (controller_role and (active is None or nonce != phase['nonce'])):
                    raise RuntimeError('duplicate_or_unprepared_emission')
                emitted.add(nonce)
                sent = emit(api, registered, nonce)
                if controller_role:
                    phase['sent'] = sent
                pipe.send({'emitted': sent, 'nonce': nonce})
            elif command == {'finish_phase': True} and controller_role and active is not None:
                phase['cleanup'] = stop_scope(api, active, trace)
                phase['duration_seconds'] = 5 - (phase_deadline - time.monotonic())
                if not phase['cleanup'].get('absent'):
                    raise RuntimeError('private_stop_unconfirmed')
                index = phase['index']
                capture = Evidence(provider, identities[index], identities[1 - index], phase['nonce'])
                phase.update(consume_files(api, Path(directory), 'phase' + str(index), capture))
                pipe.send({'phase_finished': phase})
                active, phase_deadline, phase = None, None, None
            else:
                raise RuntimeError('unexpected_source_command')
        else:
            raise RuntimeError('source_lifetime_timeout')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ':' + str(exc)
        if controller_role and 'phase' in locals() and phase:
            result['incomplete_phase'] = phase
    finally:
        if active is not None:
            result['cleanup'].append(safe(lambda: stop_scope(api, active, trace if trace.value else None)))
        if registered.value:
            result['unregister_status'] = safe(lambda: api.EventUnregister(registered))
        if loaded:
            result['manifest_unload_status'] = safe(lambda: meta.dll.TdhUnloadManifest(manifest))
        try:
            pipe.send({'finished': result})
        except (EOFError, OSError):
            pass
        pipe.close()


def receive(pipe, timeout):
    if not pipe.poll(timeout):
        raise RuntimeError('synthetic_reply_timeout')
    return pipe.recv()


def fingerprints():
    return {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
            for name in ('probe_private_etw_scope.py', 'test_focus_etw.py', 'inspect_focus_events.py')}


def phase_passed(phase):
    return bool(phase.get('trace_started') and phase.get('start_status') == 0 and
        phase.get('enable_status') == 0 and phase.get('sent') == COUNT and
        phase.get('opposite_sent') == COUNT and phase.get('duration_seconds', 99) <= 5 and
        phase.get('file_count') == 1 and phase.get('process_status') == [0] and
        phase.get('close_status') == [0] and phase.get('received') ==
        {'positive': COUNT, 'negative': 0, 'unknown': 0, 'malformed': 0} and
        phase.get('cleanup', {}).get('absent') and phase['cleanup'].get('stop_status') == 0 and
        phase['cleanup'].get('events_lost') == 0)


def run():
    check_abi()
    provider, source = uuid.uuid4(), fingerprints()
    report = {'research_only': True, 'passed': False, 'live_diagnostic_ready': False,
              'uia_scope_verified': False, 'windows_operated': False, 'trace_started': False,
              'provider_guid': str(provider), 'fingerprints': source, 'phases': [], 'children': [],
              'os': platform.version()}
    directory = ROOT / '.local/live-acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='private-scope-own-', dir=directory))
    manifest = temporary / 'synthetic.man'
    manifest.write_text(manifest_text(provider), encoding='utf-8')
    ctx, entries, identities, active_index = mp.get_context('spawn'), [], [], None
    try:
        for index in range(2):
            local, remote = ctx.Pipe()
            process = ctx.Process(target=source_child,
                args=(remote, provider.bytes_le, str(manifest), str(temporary), index == 0))
            entries.append((process, local))
            try:
                process.start()
            finally:
                remote.close()
            reply = receive(local, 5)
            if not reply.get('ready') or reply.get('pid') != process.pid or not reply.get('tid'):
                report['children'].append(reply)
                raise RuntimeError('source_handshake_failed')
            identities.append((process.pid, reply['tid']))
        entries[0][1].send({'bind': identities})
        if receive(entries[0][1], 1) != {'bound': True}:
            raise RuntimeError('controller_bind_failed')
        nonces = [uuid.uuid4().int & 0xffffffff or 1, uuid.uuid4().int & 0xffffffff or 2]
        if nonces[0] == nonces[1]:
            raise RuntimeError('duplicate_phase_nonce')
        for index, nonce in enumerate(nonces):
            active_index = index
            entries[0][1].send({'phase': index, 'nonce': nonce})
            message = receive(entries[0][1], 4)
            if message != {'phase_ready': index}:
                report['children'].append(message)
                raise RuntimeError('private_phase_not_ready')
            report['trace_started'] = True
            for process, pipe in entries:
                if not process.is_alive():
                    raise RuntimeError('source_exited')
                pipe.send({'emit': nonce})
                if receive(pipe, 1) != {'emitted': COUNT, 'nonce': nonce}:
                    raise RuntimeError('source_emission_failed')
            entries[0][1].send({'finish_phase': True})
            reply = receive(entries[0][1], 4)
            if 'phase_finished' not in reply:
                report['children'].append(reply)
                raise RuntimeError('private_phase_incomplete')
            phase = reply['phase_finished']
            phase['opposite_sent'] = COUNT
            report['phases'].append(phase)
            active_index = None
            if not phase_passed(phase):
                raise RuntimeError('private_scope_evidence_failed')
    except BaseException as exc:
        report['error'] = type(exc).__name__ + ':' + str(exc)
    finally:
        # Stop/kill the controller before fallback, so it cannot start a late session.
        for index, (process, pipe) in enumerate(entries):
            item = {'index': index, 'terminated': False}
            if process.pid is not None:
                def finish():
                    pipe.send('done')
                    reply = receive(pipe, 2)
                    item['result'] = reply.get('finished', reply)
                item['finish'] = safe(finish)
                safe(lambda: process.join(1))
                if process.is_alive():
                    item['termination'] = safe(process.terminate)
                    safe(lambda: process.join(2))
                    item['terminated'] = True
                item['dead'], item['exitcode'] = not process.is_alive(), process.exitcode
            else:
                item['dead'], item['exitcode'] = True, None
            if index == 0 and active_index is not None and item['dead'] and len(identities) == 2:
                name = 'WechatBridge-PrivateScope-' + provider.hex + '-' + str(active_index)
                scope = Scope(name, provider.bytes_le, identities[active_index][0],
                              temporary / ('phase' + str(active_index) + '.etl'))
                item['fallback_cleanup'] = safe(lambda: stop_scope(controller(), scope))
            safe(pipe.close)
            report['children'].append(item)
        all_dead = bool(entries) and all(not p.is_alive() for p, _ in entries)
        report['all_children_dead'] = all_dead
        if all_dead:
            def remove_temp():
                target = temporary.resolve()
                if target.parent != directory.resolve() or not target.name.startswith('private-scope-own-'):
                    raise RuntimeError('temporary_scope_changed')
                shutil.rmtree(target)
                return not target.exists()
            report['temporary_files_removed'] = safe(remove_temp)
        report['source_unchanged'] = safe(lambda: fingerprints() == source)
        report['trace_started'] = report['trace_started'] or any(
            x.get('finished', x.get('result', {})).get('incomplete_phase', {}).get('trace_started', False)
            for x in report['children'])
        clean = [x for x in report['children'] if 'index' in x]
        report['passed'] = bool(not report.get('error') and len(report['phases']) == 2 and
            all(phase_passed(p) for p in report['phases']) and len(clean) == 2 and
            all(x.get('dead') and x.get('exitcode') == 0 and not x.get('terminated') and
                x.get('result', {}).get('unregister_status') == 0 and not x.get('result', {}).get('error')
                for x in clean) and clean[0].get('result', {}).get('manifest_unload_status') == 0 and
            all_dead and report.get('temporary_files_removed') is True and report['source_unchanged'] is True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args()
    if not args.run_synthetic:
        print(json.dumps({'trace_started': False, 'live_diagnostic_ready': False,
                          'requires_explicit_synthetic': True}))
        return 0
    destination = ROOT / '.local/live-acceptance/private-etw-scope.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'state': 'running', 'passed': False}), encoding='utf-8')
    try:
        report = run()
    except BaseException as exc:
        report = {'passed': False, 'error': type(exc).__name__ + ':' + str(exc),
                  'live_diagnostic_ready': False}
    report['state'] = 'finished'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
