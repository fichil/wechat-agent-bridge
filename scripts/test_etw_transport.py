"""Opt-in ETW transport canary using only a fresh synthetic provider GUID.

No windows, real-client PIDs, persistent manifest installation, stacks or other
providers are used. Default invocation does not start a trace. The ordinary
cross-process realtime session needs an already authorized Windows token; this
script never requests elevation or changes permissions. A pass proves only this
synthetic user-mode transport, never Win32k filtering or live diagnostic safety.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_focus_etw import (U32, U64, PTR, GUID, Descriptor, Record, Logfile, Filter,
                            Enable, CALLBACK, SESSION_METADATA, abi_check, call,
                            controller, properties, stop_owned)
from inspect_focus_events import Metadata, PayloadPredicate, parse_event

COUNT = 20
POSITIVE, NEGATIVE = 0x12345678, 0x34567812  # Synthetic values, not process identities.
EXPECTED_SENT = {'positive': COUNT, 'negative': COUNT, 'wrong_id': COUNT}
EXPECTED_RECEIVED = {'positive': COUNT, 'negative': 0, 'wrong_id': 0,
                     'unexpected': 0, 'malformed': 0}
_LIVE_CONSUMERS = []


class EventData(c.Structure):
    _fields_ = [('pointer', U64), ('size', U32), ('reserved', U32)]


def manifest_text(provider):
    namespace = 'http://schemas.microsoft.com/win/2004/08/events'
    ET.register_namespace('', namespace)
    root = ET.Element('{' + namespace + '}instrumentationManifest',
                      {'xmlns:win': 'http://manifests.microsoft.com/win/2004/08/windows/events'})
    events = ET.SubElement(ET.SubElement(root, 'instrumentation'), 'events')
    node = ET.SubElement(events, 'provider', {
        'name': 'WechatBridge-Synthetic-' + provider.hex, 'guid': '{' + str(provider) + '}',
        'symbol': 'SyntheticProvider', 'resourceFileName': sys.executable,
        'messageFileName': sys.executable})
    ET.SubElement(ET.SubElement(node, 'keywords'), 'keyword', {'name': 'Canary', 'mask': '0x2000'})
    template = ET.SubElement(ET.SubElement(node, 'templates'), 'template', {'tid': 'Pair'})
    for name in ('OldThreadId', 'NewThreadId'):
        ET.SubElement(template, 'data', {'name': name, 'inType': 'win:UInt32'})
    events = ET.SubElement(node, 'events')
    for event_id in (2, 3):
        ET.SubElement(events, 'event', {'value': str(event_id), 'version': '0',
                      'level': 'win:Informational', 'keywords': 'Canary', 'template': 'Pair'})
    return ET.tostring(root, encoding='unicode')


def descriptor(event_id):
    return Descriptor(event_id, 0, 0, 4, 0, 0, 0x2000)


def emission_plan():
    # Wrong-ID events carry the matching payload to test event-ID exclusion.
    return [(kind, event_id, struct.pack('<II', index, value))
            for index in range(COUNT)
            for kind, event_id, value in (('positive', 2, POSITIVE),
                                         ('negative', 2, NEGATIVE), ('wrong_id', 3, POSITIVE))]


def source_fingerprints():
    directory = Path(__file__).resolve().parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('test_etw_transport.py', 'test_focus_etw.py', 'inspect_focus_events.py')}


class Evidence:
    def __init__(self, provider, source_pid):
        self.provider, self.source_pid = provider, source_pid
        self.counts = dict.fromkeys(EXPECTED_RECEIVED, 0)
        self.indices, self.metadata_count = set(), 0
        self.abort = threading.Event()

    def consume(self, pointer):
        try:
            record = pointer.contents
            provider = bytes(record.header.provider)
            if provider == SESSION_METADATA:
                self.metadata_count += 1
                return
            if provider != self.provider or record.header.pid != self.source_pid:
                self.counts['unexpected'] += 1; self.abort.set(); return
            event_id = record.header.descriptor.id
            if event_id == 3:
                self.counts['wrong_id'] += 1; self.abort.set(); return
            if event_id != 2 or record.header.descriptor.version != 0 or record.data_length != 8:
                self.counts['malformed'] += 1; self.abort.set(); return
            index, value = struct.unpack('<II', c.string_at(record.data, 8))
            if value == NEGATIVE:
                self.counts['negative'] += 1; self.abort.set(); return
            if value != POSITIVE or not 0 <= index < COUNT or index in self.indices:
                self.counts['malformed'] += 1; self.abort.set(); return
            self.indices.add(index)
            self.counts['positive'] += 1
            # Never inspect extended data or stacks, including on rejected events.
        except BaseException:
            self.counts['malformed'] += 1
            self.abort.set()


def build_filter(meta, provider):
    aggregate, single = Filter(), PTR()
    predicate = PayloadPredicate('NewThreadId', 0, str(POSITIVE))
    event = descriptor(2)
    status = meta.dll.TdhCreatePayloadFilter(provider, c.byref(event), False, 1,
                                           c.byref(predicate), c.byref(single))
    try:
        if status or not single.value: raise RuntimeError(f'create_payload_filter_failed_{status}')
        call(meta.dll, 'TdhAggregatePayloadFilters', U32,
             [U32, c.POINTER(PTR), PTR, c.POINTER(Filter)])
        call(meta.dll, 'TdhCleanupPayloadEventFilterDescriptor', U32, [c.POINTER(Filter)])
        array = (PTR * 1)(single.value)
        status = meta.dll.TdhAggregatePayloadFilters(1, array, None, c.byref(aggregate))
        if status or not aggregate.pointer: raise RuntimeError(f'aggregate_payload_filter_failed_{status}')
        encoded = c.create_string_buffer(struct.pack('<BBHH', 1, 0, 1, 2))
        filters = (Filter * 2)(aggregate, Filter(c.addressof(encoded), 6, 0x80000200))
        return aggregate, encoded, filters
    except BaseException:
        if aggregate.pointer: meta.dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(aggregate))
        raise
    finally:
        if single.value: meta.dll.TdhDeletePayloadFilter(c.byref(single))


def safe_cleanup(action):
    try: return action()
    except BaseException as exc: return {'error': str(exc)}


def validate_manifest(meta, provider):
    schemas = []
    for event_id in (2, 3):
        event = descriptor(event_id)
        raw = meta.buffer('TdhGetManifestEventInformation', provider, c.byref(event))
        if raw[:16] != bytes(provider) or raw[32:48] != bytes(event):
            raise RuntimeError('synthetic_metadata_identity_mismatch')
        info = parse_event(raw)
        fields = [(field['name'], field['in_type'], field['flags'], field['count_or_index'])
                  for field in info['fields']]
        if fields != [('OldThreadId', 8, 0, 1), ('NewThreadId', 8, 0, 1)]:
            raise RuntimeError('synthetic_metadata_schema_mismatch')
        schemas.append({'event_id': event_id, 'field_names': [field[0] for field in fields]})
    return schemas


class Preparation:
    """Shared cache-only metadata/filter preparation for preflight and tracing."""
    def __init__(self, meta, manifest_path, provider):
        self.meta, self.path, self.provider = meta, manifest_path, provider
        self.loaded, self.bundle, self.schemas = False, None, []

    def open(self):
        call(self.meta.dll, 'TdhLoadManifest', U32, [c.c_wchar_p])
        call(self.meta.dll, 'TdhUnloadManifest', U32, [c.c_wchar_p])
        status = self.meta.dll.TdhLoadManifest(self.path)
        if status: raise RuntimeError(f'load_manifest_failed_{status}')
        self.loaded = True
        self.schemas = validate_manifest(self.meta, self.provider)
        self.bundle = build_filter(self.meta, self.provider)
        return self.bundle

    def close(self):
        result = {}
        if self.bundle:
            result['filter_cleanup_status'] = safe_cleanup(lambda:
                self.meta.dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(self.bundle[0])))
            self.bundle = None
        if self.loaded:
            result['manifest_unload_status'] = safe_cleanup(lambda: self.meta.dll.TdhUnloadManifest(self.path))
            self.loaded = False
        return result


def owner(pipe, name, session_guid, provider_guid, manifest_path):
    api, meta = controller(), Metadata()
    started, registered, enabled_at = False, U64(), None
    provider = GUID.from_buffer_copy(provider_guid)
    preparation = Preparation(meta, manifest_path, provider)
    result = {'owner_pid': os.getpid(), 'trace_started': False, 'provider_enabled': False,
              'sent': dict.fromkeys(EXPECTED_SENT, 0), 'write_errors': 0}
    try:
        bundle = preparation.open()
        result['metadata_schemas'] = preparation.schemas
        call(api, 'EventRegister', U32, [PTR, PTR, PTR, c.POINTER(U64)])
        call(api, 'EventWrite', U32, [U64, c.POINTER(Descriptor), U32, c.POINTER(EventData)])
        call(api, 'EventUnregister', U32, [U64])
        status = api.EventRegister(provider, None, None, c.byref(registered))
        if status or not registered.value: raise RuntimeError(f'register_provider_failed_{status}')
        storage, _ = properties(name, session_guid)
        trace = U64()
        status = api.StartTraceW(c.byref(trace), name, storage)
        if status: raise RuntimeError(f'start_trace_failed_{status}')
        started = result['trace_started'] = True
        pipe.send({'ready': True})
        deadline, enabled, emitted = time.monotonic() + 10, False, False
        while time.monotonic() < deadline:
            if not pipe.poll(0.05): continue
            try: command = pipe.recv()
            except EOFError: break
            if command == 'done': break
            if command == 'enable' and not enabled:
                params = Enable(2, 0x10, 0, GUID.from_buffer_copy(session_guid), bundle[2], 2)
                enabled_at = time.monotonic(); deadline = min(deadline, enabled_at + 5)
                status = api.EnableTraceEx2(trace, provider, 1, 4, 0x2000, 0, 1000, c.byref(params))
                if status: raise RuntimeError(f'enable_provider_failed_{status}')
                enabled = result['provider_enabled'] = True
                pipe.send({'enabled': True})
            elif command == 'emit' and enabled and not emitted:
                for kind, event_id, payload in emission_plan():
                    if time.monotonic() >= deadline: raise RuntimeError('emission_deadline')
                    event, raw = descriptor(event_id), c.create_string_buffer(payload)
                    data = EventData(c.addressof(raw), len(payload), 0)
                    status = api.EventWrite(registered, c.byref(event), 1, c.byref(data))
                    if status:
                        result['write_errors'] += 1
                        raise RuntimeError(f'event_write_failed_{status}')
                    result['sent'][kind] += 1
                emitted = True
                pipe.send({'emitted': result['sent']})
            else:
                raise RuntimeError('invalid_owner_command')
    except BaseException as exc:
        result['error'] = str(exc)
    finally:
        result['cleanup'] = (safe_cleanup(lambda: stop_owned(api, name, session_guid, trace)) if started
                             else {'stopped': True, 'never_started': True})
        if enabled_at is not None: result['enable_to_stop_seconds'] = time.monotonic() - enabled_at
        if registered.value: result['unregister_status'] = safe_cleanup(lambda: api.EventUnregister(registered))
        result.update(preparation.close())
        try: pipe.send({'finished': result})
        except (EOFError, BrokenPipeError): pass
        pipe.close()


def passed(report):
    own = report.get('owner', {})
    return bool(not report.get('error') and not own.get('error')
        and report.get('received') == EXPECTED_RECEIVED and own.get('sent') == EXPECTED_SENT
        and own.get('write_errors') == 0 and own.get('provider_enabled')
        and own.get('cleanup', {}).get('stopped') and own.get('cleanup', {}).get('events_lost') == 0
        and own.get('enable_to_stop_seconds', 99) <= 5
        and all(own.get(key) == 0 for key in ('unregister_status', 'filter_cleanup_status', 'manifest_unload_status'))
        and report.get('cleanup_verification', {}).get('stopped')
        and report.get('owner_exitcode') == 0 and not report.get('owner_terminated')
        and report.get('consumer_finished') and report.get('process_trace_status') == [0]
        and report.get('manifest_directory_removed') and report.get('source_unchanged'))


def run_canary():
    abi_check()
    if os.name != 'nt': raise RuntimeError('windows_required')
    if c.sizeof(EventData) != 16: raise RuntimeError('event_data_abi_mismatch')
    ctx, api = mp.get_context('spawn'), controller()
    provider, session_guid = uuid.uuid4(), uuid.uuid4().bytes_le
    name = 'WechatBridge-Transport-' + uuid.uuid4().hex
    before = source_fingerprints()
    report = {'schema': 1, 'passed': False, 'trace_started': False,
              'win32k_verified': False, 'live_diagnostic_ready': False,
              'session_name': name, 'provider_guid': str(provider), 'source_fingerprints': before,
              'expected_sent': EXPECTED_SENT, 'expected_received': EXPECTED_RECEIVED}
    temporary = tempfile.TemporaryDirectory(prefix='wechat-bridge-etw-canary-')
    manifest = Path(temporary.name) / 'synthetic.man'
    manifest.write_text(manifest_text(provider), encoding='utf-8')
    ready, remote = ctx.Pipe()
    process = ctx.Process(target=owner, args=(remote, name, session_guid, provider.bytes_le, str(manifest)))
    consumer, thread, callback, capture, statuses = U64(0xffffffffffffffff), None, None, None, []
    evidence = None
    try:
        process.start(); remote.close()
        report['owner_pid'] = process.pid
        evidence = Evidence(provider.bytes_le, process.pid)
        if not ready.poll(4): raise RuntimeError('owner_ready_timeout')
        message = ready.recv()
        if message != {'ready': True}:
            report['owner'] = message.get('finished', {})
            raise RuntimeError('owner_not_ready')
        report['trace_started'] = True
        callback = CALLBACK(None, c.POINTER(Record))(evidence.consume)
        capture = Logfile(); capture.name = name; capture.mode = 0x10000100
        capture.callback = c.cast(callback, PTR)
        consumer.value = api.OpenTraceW(c.byref(capture))
        if consumer.value == 0xffffffffffffffff: raise RuntimeError('open_trace_failed')
        thread = threading.Thread(target=lambda: statuses.append(api.ProcessTrace(c.byref(consumer), 1, None, None)), daemon=True)
        thread.start()
        for command, expected in (('enable', {'enabled': True}), ('emit', {'emitted': EXPECTED_SENT})):
            ready.send(command)
            if not ready.poll(2): raise RuntimeError(f'owner_{command}_timeout')
            message = ready.recv()
            if message != expected:
                report['owner'] = message.get('finished', {})
                raise RuntimeError(f'owner_{command}_failed')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not evidence.abort.is_set():
            if evidence.counts['positive'] == COUNT: break
            time.sleep(0.01)
        # Stop flushes pending buffers; classification is evaluated after consumer exits.
    except BaseException as exc:
        report['error'] = str(exc)
    finally:
        if process.pid:
            try:
                ready.send('done')
                if ready.poll(3):
                    message = ready.recv()
                    if 'finished' in message: report['owner'] = message['finished']
            except (EOFError, BrokenPipeError): pass
            process.join(1)
            if process.is_alive():
                process.terminate(); process.join(2); report['owner_terminated'] = True
            report['owner_exitcode'] = process.exitcode
        report['cleanup_verification'] = (safe_cleanup(lambda: stop_owned(api, name, session_guid)) if not process.is_alive()
                                         else {'stopped': False, 'error': 'owner_still_alive'})
        if thread: thread.join(2)
        if consumer.value != 0xffffffffffffffff: report['close_trace_status'] = safe_cleanup(lambda: api.CloseTrace(consumer))
        if thread and thread.is_alive(): thread.join(1)
        if thread and thread.is_alive():
            _LIVE_CONSUMERS.append((thread, callback, capture, consumer, evidence, api))
        ready.close()
        report['consumer_finished'] = not thread or not thread.is_alive()
        report['process_trace_status'] = statuses
        report['received'] = evidence.counts if evidence else dict.fromkeys(EXPECTED_RECEIVED, 0)
        report['metadata_events'] = evidence.metadata_count if evidence else 0
        report['source_unchanged'] = source_fingerprints() == before
        if not process.is_alive():
            temporary.cleanup()
            report['manifest_directory_removed'] = not manifest.parent.exists()
        else:
            report['manifest_directory_removed'] = False
        report['passed'] = passed(report)
    return report


def check_metadata():
    """No controller, EventRegister, window, child process or trace APIs."""
    abi_check()
    if os.name != 'nt': raise RuntimeError('windows_required')
    provider, before = uuid.uuid4(), source_fingerprints()
    report = {'schema': 1, 'passed': False, 'trace_started': False, 'win32k_verified': False,
              'live_diagnostic_ready': False, 'provider_guid': str(provider),
              'source_fingerprints': before, 'mode': 'metadata_only'}
    with tempfile.TemporaryDirectory(prefix='wechat-bridge-etw-metadata-') as directory:
        manifest = Path(directory) / 'synthetic.man'
        manifest.write_text(manifest_text(provider), encoding='utf-8')
        preparation = Preparation(Metadata(), str(manifest), GUID.from_buffer_copy(provider.bytes_le))
        try:
            preparation.open()
            report['metadata_schemas'] = preparation.schemas
            report['filter_constructed'] = True
        except BaseException as exc:
            report['error'] = str(exc)
        finally:
            report.update(preparation.close())
    report['manifest_directory_removed'] = not Path(directory).exists()
    report['source_unchanged'] = source_fingerprints() == before
    report['passed'] = bool(not report.get('error') and report.get('filter_constructed')
                            and report.get('filter_cleanup_status') == 0
                            and report.get('manifest_unload_status') == 0
                            and report['manifest_directory_removed'] and report['source_unchanged'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run-synthetic', action='store_true')
    mode.add_argument('--check-metadata', action='store_true')
    args = parser.parse_args()
    if not args.run_synthetic and not args.check_metadata:
        print(json.dumps({'trace_started': False, 'win32k_verified': False, 'live_diagnostic_ready': False,
                          'entrypoint': '--run-synthetic', 'requires': 'Windows x64 and existing ETW privilege'}))
        return 0
    report = check_metadata() if args.check_metadata else run_canary()
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
