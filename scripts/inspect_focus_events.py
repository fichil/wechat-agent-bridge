"""Read local ETW manifests and compile example filters; never start a trace.

Only standard-library TDH metadata APIs and provider-resource file reads are
used. No process/window handle is opened. Filter construction allocates a local
TDH object, then deletes it; success does NOT prove kernel-provider filtering or
stack capture. Run: python -X utf8 scripts/inspect_focus_events.py
"""
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import uuid


MAX_BUFFER = 1024 * 1024
PROVIDERS = (
    ('Microsoft-Windows-Win32k', '8c416c79-d49b-4f01-a467-e56d3aa8234c',
     (2, 26, 59, 60, 61, 62, 92, 93, 203, 204, 258, 259), 'kernel'),
    ('Microsoft-Windows-UIAutomationCore', '820a42d8-38c4-465d-b64e-d7d56ea1d612',
     (335, 336, 513, 514, 515), 'user'),
)
INPUT_TYPES = {1: 'UnicodeString', 7: 'Int32', 8: 'UInt32', 10: 'UInt64',
               16: 'Pointer', 17: 'FILETIME', 20: 'HexInt32', 21: 'HexInt64'}
SOURCES = {
    'scope_filter': 'https://learn.microsoft.com/en-us/windows/win32/api/evntprov/ns-evntprov-event_filter_descriptor',
    'payload_filter': 'https://learn.microsoft.com/en-us/windows/win32/api/tdh/nf-tdh-tdhcreatepayloadfilter',
    'payload_types': 'https://learn.microsoft.com/en-us/windows/win32/api/tdh/ns-tdh-payload_filter_predicate',
    'stacks': 'https://learn.microsoft.com/en-us/windows-hardware/test/wpt/eventprovider',
}


def utf16_at(blob, offset):
    if offset == 0:
        return None
    if offset < 112 or offset % 2 or offset >= len(blob):
        raise ValueError('metadata_string_offset_invalid')
    for end in range(offset, len(blob) - 1, 2):
        if blob[end:end + 2] == b'\0\0':
            return blob[offset:end].decode('utf-16-le')
    raise ValueError('metadata_string_unterminated')


def event_descriptors(blob):
    if len(blob) < 8:
        raise ValueError('metadata_event_list_short')
    count = struct.unpack_from('<I', blob)[0]
    if count > 8192 or 8 + count * 16 > len(blob):
        raise ValueError('metadata_event_count_invalid')
    return [blob[8 + i * 16:24 + i * 16] for i in range(count)]


def parse_event(blob):
    """Bounded parsing of TRACE_EVENT_INFO, without any event payload data."""
    if not 112 <= len(blob) <= MAX_BUFFER:
        raise ValueError('metadata_size_invalid')
    count, top = struct.unpack_from('<II', blob, 100)
    if top > count or count > 256 or 112 + count * 24 > len(blob):
        raise ValueError('metadata_property_count_invalid')
    event_id, version, channel, level, opcode, task, keyword = struct.unpack_from('<HBBBBHQ', blob, 32)
    fields = []
    for i in range(count):
        flags, name, in_type, out_type, _map, number, length, _tags = struct.unpack_from('<IIHHIHHI', blob, 112 + i * 24)
        # Struct/custom schemas are not flattened or guessed by this probe.
        if flags & (1 | 128):
            raise ValueError('metadata_complex_property_unsupported')
        fields.append({'name': utf16_at(blob, name), 'in_type': in_type,
                       'type_name': INPUT_TYPES.get(in_type, 'TDH_' + str(in_type)),
                       'out_type': out_type, 'flags': flags, 'count_or_index': number,
                       'length_or_index': length, 'top_level': i < top})
    return {'id': event_id, 'version': version, 'channel': channel, 'level': level,
            'opcode': opcode, 'task_id': task, 'keyword': hex(keyword),
            'provider': utf16_at(blob, struct.unpack_from('<I', blob, 52)[0]),
            'task': utf16_at(blob, struct.unpack_from('<I', blob, 68)[0]),
            'fields': fields}


def filtering_evidence(mode, constructions):
    return {
        'provider_mode': mode,
        'pid_scope_filter': ('not_usable_for_kernel_registration' if mode == 'kernel'
                             else 'documented_for_user_provider_not_tested'),
        'payload_filter_constructions': constructions,
        'payload_filter_effective': 'not_tested',
        'user_stack_capture': 'not_tested',
        'set_foreground_window_call_observed': False,
        'trace_started': False,
        'live_diagnostic_ready': False,
        'limits': [
            'TDH construction validates metadata only; no EnableTraceEx2 call is made.',
            'PID scope filters do not enable a provider registered by a kernel driver.',
            'Payload predicates support integers/FILETIME, not Pointer fields such as hwnd.',
            'Focus-change events describe a change, not every SetForegroundWindow call.',
            'Real-time/file exclusion and user-stack causality require a synthetic trace test.',
        ],
    }


class PayloadPredicate(c.Structure):
    _fields_ = [('field', c.c_wchar_p), ('operator', c.c_uint16), ('value', c.c_wchar_p)]


class Metadata:
    def __init__(self):
        self.dll = c.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/tdh.dll'), winmode=0x800)
        self.dll.TdhEnumerateManifestProviderEvents.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(c.c_uint32)]
        self.dll.TdhGetManifestEventInformation.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_uint32)]
        self.dll.TdhCreatePayloadFilter.argtypes = [c.c_void_p, c.c_void_p, c.c_ubyte, c.c_uint32,
                                                   c.POINTER(PayloadPredicate), c.POINTER(c.c_void_p)]
        self.dll.TdhDeletePayloadFilter.argtypes = [c.POINTER(c.c_void_p)]
        for name in ('TdhEnumerateManifestProviderEvents', 'TdhGetManifestEventInformation',
                     'TdhCreatePayloadFilter', 'TdhDeletePayloadFilter'):
            getattr(self.dll, name).restype = c.c_uint32

    def buffer(self, name, *arguments):
        size = c.c_uint32()
        status = getattr(self.dll, name)(*arguments, None, c.byref(size))
        if status != 122 or not 1 <= size.value <= MAX_BUFFER:
            raise ValueError(f'{name}_size_failed_{status}')
        data = c.create_string_buffer(size.value)
        capacity = size.value
        status = getattr(self.dll, name)(*arguments, data, c.byref(size))
        if status or size.value > capacity:
            raise ValueError(f'{name}_read_failed_{status}')
        return data.raw[:size.value]

    def compile_filter(self, provider, descriptor, field):
        result = c.c_void_p()
        predicate = PayloadPredicate(field, 0, '1234567')  # Synthetic integer; never a selected PID.
        status = self.dll.TdhCreatePayloadFilter(provider, descriptor, False, 1,
                                                 c.byref(predicate), c.byref(result))
        cleanup = None
        if result.value:
            cleanup = self.dll.TdhDeletePayloadFilter(c.byref(result))
        return {'field': field, 'synthetic_value': 1234567,
                'construction_status': status, 'construction_succeeded': status == 0,
                'cleanup_status': cleanup, 'applied_to_session': False}


def resource_fingerprint(guid):
    import winreg
    key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\WINEVT\Publishers\{' + guid + '}'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
        resource = winreg.QueryValueEx(handle, 'ResourceFileName')[0]
    path = Path(os.path.expandvars(resource))
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'resource_file': str(path), 'sha256': digest, 'bytes': path.stat().st_size}


def inspect_provider(metadata, name, guid, wanted, mode):
    provider = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    raw = metadata.buffer('TdhEnumerateManifestProviderEvents', provider)
    descriptors = event_descriptors(raw)
    events, constructions = [], []
    for descriptor in descriptors:
        event_id = struct.unpack_from('<H', descriptor)[0]
        if event_id not in wanted:
            continue
        native = (c.c_ubyte * 16).from_buffer_copy(descriptor)
        blob = metadata.buffer('TdhGetManifestEventInformation', provider, native)
        if blob[:16] != uuid.UUID(guid).bytes_le or blob[32:48] != descriptor:
            raise ValueError('metadata_descriptor_identity_changed')
        event = parse_event(blob)
        if event['provider'] != name:
            raise ValueError('metadata_provider_identity_changed')
        events.append(event)
        field = {2: 'NewThreadId', 26: 'NewProcessId', 258: 'NewProcessId', 259: 'OldProcessId'}.get(event_id)
        if field and mode == 'kernel' and any(p['name'] == field and p['in_type'] == 8 for p in event['fields']):
            check = metadata.compile_filter(provider, native, field)
            constructions.append({'event_id': event_id, 'version': event['version'], **check})
    evidence_hash = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
    return {'name': name, 'guid': guid, 'manifest_event_count': len(descriptors),
            'resource': resource_fingerprint(guid), 'selected_metadata_sha256': evidence_hash,
            'events': events, 'missing_event_ids': sorted(set(wanted) - {e['id'] for e in events}),
            'filtering': filtering_evidence(mode, constructions)}


def main():
    if os.name != 'nt':
        print(json.dumps({'error': 'windows_required', 'trace_started': False}))
        return 2
    report = {'schema': 1, 'os_version': platform.version(), 'trace_started': False,
              'live_diagnostic_ready': False, 'providers': [], 'sources': SOURCES}
    metadata = Metadata()
    for provider in PROVIDERS:
        try:
            report['providers'].append(inspect_provider(metadata, *provider))
        except (OSError, ValueError) as exc:
            report['providers'].append({'name': provider[0], 'error': str(exc)})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any('error' in provider for provider in report['providers']))


if __name__ == '__main__':
    raise SystemExit(main())
