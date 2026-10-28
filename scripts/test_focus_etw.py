"""Opt-in, own-synthetic Win32k ETW experiment. Never use a real client PID.

Default invocation only describes the experiment. --probe-source creates two
own child windows on a random non-input desktop and measures whether they can
produce a foreground transition, without ETW. --run-synthetic additionally
requires that source check before attempting reciprocal payload filters. Local
edit focus alone is never a positive control for Win32k FocusChange. No
SwitchDesktop, input simulation, clipboard write, file trace or global unfiltered
fallback exists. Microsoft ABI declarations are independently typed.
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
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_focus_events import Metadata, PayloadPredicate, event_descriptors, parse_event

U16, U32, U64, PTR = c.c_uint16, c.c_uint32, c.c_uint64, c.c_void_p
GUID = c.c_ubyte * 16
PROVIDER = uuid.UUID('8c416c79-d49b-4f01-a467-e56d3aa8234c').bytes_le
SESSION_METADATA = uuid.UUID('68fdd900-4a3e-11d1-84f4-0000f80464e3').bytes_le
# Keep this experiment to the one event tested by both reciprocal controls.
EVENT_FIELDS = {2: ('NewThreadId', 4, 8)}
EVENT_SCHEMAS = {2: (('OldThreadId', 8), ('NewThreadId', 8))}
CALLBACK = getattr(c, 'WINFUNCTYPE', c.CFUNCTYPE)
_LIVE_CONSUMERS = []  # Retain callback memory if a native consumer fails to stop.


class Wnode(c.Structure):
    _fields_ = [('size', U32), ('provider', U32), ('historical', U64), ('time', U64),
                ('guid', GUID), ('clock', U32), ('flags', U32)]


class Properties(c.Structure):
    _fields_ = [('wnode', Wnode)] + [(name, U32) for name in (
        'buffer_kb', 'minimum', 'maximum', 'maximum_file', 'mode', 'flush', 'enable',
        'age', 'buffers', 'free', 'events_lost', 'written', 'log_lost', 'realtime_lost')]
    _fields_ += [('logger_thread', PTR), ('file_offset', U32), ('name_offset', U32)]


class Descriptor(c.Structure):
    _fields_ = [('id', U16), ('version', c.c_ubyte), ('channel', c.c_ubyte),
                ('level', c.c_ubyte), ('opcode', c.c_ubyte), ('task', U16), ('keyword', U64)]


class Header(c.Structure):
    _fields_ = [('size', U16), ('header_type', U16), ('flags', U16), ('property', U16),
                ('tid', U32), ('pid', U32), ('time', U64), ('provider', GUID),
                ('descriptor', Descriptor), ('processor_time', U64), ('activity', GUID)]


class Extended(c.Structure):
    _fields_ = [('reserved', U16), ('kind', U16), ('linkage', U16), ('size', U16), ('data', U64)]


class Record(c.Structure):
    _fields_ = [('header', Header), ('context', U32), ('extended_count', U16),
                ('data_length', U16), ('extended', c.POINTER(Extended)), ('data', PTR), ('user', PTR)]


class Logfile(c.Structure):
    # x64 SDK EVENT_TRACE_LOGFILEW. Unused nested event/header outputs are opaque.
    _fields_ = [('file', c.c_wchar_p), ('name', c.c_wchar_p), ('time', U64), ('read', U32),
                ('mode', U32), ('current_event', c.c_ubyte * 88),
                ('log_header', c.c_ubyte * 280), ('buffer_callback', PTR),
                ('buffer_size', U32), ('filled', U32), ('lost', U32), ('callback', PTR),
                ('kernel', U32), ('context', PTR)]


class Filter(c.Structure):
    _fields_ = [('pointer', U64), ('size', U32), ('kind', U32)]


class Enable(c.Structure):
    _fields_ = [('version', U32), ('properties', U32), ('flags', U32), ('source', GUID),
                ('filters', c.POINTER(Filter)), ('count', U32)]


def abi_check():
    expected = ((Wnode, 48), (Properties, 120), (Descriptor, 16), (Header, 80),
                (Extended, 16), (Record, 112), (Logfile, 448), (Filter, 16), (Enable, 48))
    if c.sizeof(PTR) != 8 or any(c.sizeof(kind) != size for kind, size in expected):
        raise RuntimeError('x64_abi_mismatch')
    if Logfile.callback.offset != 424 or Record.data.offset != 96:
        raise RuntimeError('x64_callback_offset_mismatch')


def call(dll, name, result, arguments):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, arguments
    return fn


def desktop_name(user, handle):
    buffer, needed = c.create_unicode_buffer(256), U32()
    fn = call(user, 'GetUserObjectInformationW', c.c_int, [PTR, c.c_int, PTR, U32, c.POINTER(U32)])
    if not fn(handle, 2, buffer, c.sizeof(buffer), c.byref(needed)):
        raise RuntimeError('desktop_name_unavailable')
    return buffer.value


def input_snapshot():
    user = c.WinDLL('user32', use_last_error=True)
    handle = call(user, 'OpenInputDesktop', PTR, [U32, c.c_int, U32])(0, False, 1)
    if not handle:
        raise RuntimeError('input_desktop_unavailable')
    try:
        return (desktop_name(user, handle),
                call(user, 'GetForegroundWindow', PTR, [])(),
                call(user, 'GetClipboardSequenceNumber', U32, [])())
    finally:
        call(user, 'CloseDesktop', c.c_int, [PTR])(handle)


def module_ranges():
    kernel = c.WinDLL('kernel32', use_last_error=True)
    process = call(kernel, 'GetCurrentProcess', PTR, [])()
    handles, needed = (PTR * 256)(), U32()
    if not call(kernel, 'K32EnumProcessModules', c.c_int, [PTR, PTR, U32, c.POINTER(U32)])(
            process, handles, c.sizeof(handles), c.byref(needed)) or needed.value > c.sizeof(handles):
        raise RuntimeError('synthetic_modules_unavailable')
    class ModuleInfo(c.Structure):
        _fields_ = [('base', PTR), ('size', U32), ('entry', PTR)]
    result = []
    for handle in handles[:needed.value // c.sizeof(PTR)]:
        info, name = ModuleInfo(), c.create_unicode_buffer(260)
        if not call(kernel, 'K32GetModuleInformation', c.c_int, [PTR, PTR, PTR, U32])(
                process, handle, c.byref(info), c.sizeof(info)):
            raise RuntimeError('synthetic_module_range_failed')
        if not call(kernel, 'K32GetModuleBaseNameW', U32, [PTR, PTR, c.c_wchar_p, U32])(
                process, handle, name, len(name)):
            raise RuntimeError('synthetic_module_name_failed')
        result.append((info.base, info.size, name.value))
    return result


def window_child(pipe, name):
    user, kernel = c.WinDLL('user32', use_last_error=True), c.WinDLL('kernel32')
    desktop, top = None, None
    try:
        desktop = call(user, 'OpenDesktopW', PTR, [c.c_wchar_p, U32, c.c_int, U32])(name, 0, False, 0x83)
        if not desktop or not call(user, 'SetThreadDesktop', c.c_int, [PTR])(desktop):
            raise RuntimeError('private_desktop_attach_failed')
        tid = call(kernel, 'GetCurrentThreadId', U32, [])()
        current = call(user, 'GetThreadDesktop', PTR, [U32])(tid)
        if desktop_name(user, current) != name or input_snapshot()[0] == name:
            raise RuntimeError('private_desktop_not_isolated')
        create = call(user, 'CreateWindowExW', PTR, [U32, c.c_wchar_p, c.c_wchar_p, U32,
                      c.c_int, c.c_int, c.c_int, c.c_int, PTR, PTR, PTR, PTR])
        top = create(0, 'STATIC', 'Synthetic ETW target', 0xcf0000, 0, 0, 200, 120, None, None, None, None)
        edits = [create(0, 'EDIT', '', 0x50800000, 0, n * 30, 120, 25, top, None, None, None) for n in range(2)]
        if not top or not all(edits):
            raise RuntimeError('synthetic_window_creation_failed')
        call(user, 'ShowWindow', c.c_int, [PTR, c.c_int])(top, 4)  # Private desktop, no activation.
        set_foreground = call(user, 'SetForegroundWindow', c.c_int, [PTR])
        get_foreground = call(user, 'GetForegroundWindow', PTR, [])
        set_focus = call(user, 'SetFocus', PTR, [PTR])
        get_focus = call(user, 'GetFocus', PTR, [])
        pipe.send({'ready': True, 'pid': os.getpid(), 'tid': tid, 'modules': module_ranges()})
        until = time.monotonic() + 30
        message = c.create_string_buffer(48)  # x64 MSG, private thread only.
        peek = call(user, 'PeekMessageW', c.c_int, [PTR, PTR, U32, U32, U32])
        translate = call(user, 'TranslateMessage', c.c_int, [PTR])
        dispatch = call(user, 'DispatchMessageW', c.c_ssize_t, [PTR])
        while time.monotonic() < until:
            while peek(message, None, 0, 0, 1):
                translate(message); dispatch(message)
            if not pipe.poll(0.01): continue
            command = pipe.recv()
            if command == 'stop': break
            if command != 'exercise': raise RuntimeError('unexpected_synthetic_command')
            if input_snapshot()[0] == name or desktop_name(user, current) != name:
                raise RuntimeError('private_desktop_changed')
            own_foreground_before = get_foreground() == top
            foreground_result = bool(set_foreground(top))
            own_foreground_after = get_foreground() == top
            results = []
            for edit in edits:
                set_focus(edit)
                results.append(get_focus() == edit)
            pipe.send({'exercised': True, 'local_focus_verified': all(results),
                       'foreground_result': foreground_result,
                       'own_foreground_before': own_foreground_before,
                       'own_foreground_after': own_foreground_after})
    except BaseException as exc:
        try: pipe.send({'error': str(exc)})
        except (BrokenPipeError, EOFError): pass
    finally:
        if top: call(user, 'DestroyWindow', c.c_int, [PTR])(top)
        if desktop: call(user, 'CloseDesktop', c.c_int, [PTR])(desktop)
        pipe.close()


def properties(name, guid):
    encoded = (name + '\0').encode('utf-16-le')
    buffer = c.create_string_buffer(c.sizeof(Properties) + len(encoded))
    p = Properties.from_buffer(buffer)
    p.wnode.size, p.wnode.flags, p.wnode.clock = len(buffer), 0x20000, 1
    p.wnode.guid[:] = guid
    p.buffer_kb, p.minimum, p.maximum = 16, 4, 4
    p.mode, p.flush, p.name_offset = 0x10000100, 1, c.sizeof(Properties)
    c.memmove(c.addressof(buffer) + p.name_offset, encoded, len(encoded))
    return buffer, p


def controller():
    api = c.WinDLL('advapi32', use_last_error=True)
    call(api, 'StartTraceW', U32, [c.POINTER(U64), c.c_wchar_p, PTR])
    call(api, 'ControlTraceW', U32, [U64, c.c_wchar_p, PTR, U32])
    call(api, 'EnableTraceEx2', U32, [U64, PTR, U32, c.c_ubyte, U64, U64, U32, c.POINTER(Enable)])
    call(api, 'OpenTraceW', U64, [c.POINTER(Logfile)])
    call(api, 'ProcessTrace', U32, [c.POINTER(U64), U32, PTR, PTR])
    call(api, 'CloseTrace', U32, [U64])
    return api


def stop_owned(api, name, guid, trace=0):
    buffer, p = properties(name, guid)
    status = api.ControlTraceW(trace, None if trace else name, buffer, 0)
    if status == 4201:
        return {'stopped': True, 'already_absent': True}
    if status or bytes(p.wnode.guid) != guid:
        return {'stopped': False, 'error': 'session_query_or_identity_failed', 'status': status}
    status = api.ControlTraceW(trace, None if trace else name, buffer, 1)
    losses = p.events_lost + p.log_lost + p.realtime_lost
    check, _ = properties(name, guid)
    absent = api.ControlTraceW(0, name, check, 0) == 4201
    return {'stopped': status == 0 and absent, 'status': status, 'events_lost': losses}


def session_owner(pipe, name, guid, target, stacks):
    """Only this bounded child creates/enables/stops this session.

    The parent never creates a session after this monitor exits. If creation or
    enable times out, the parent terminates this own child before GUID cleanup.
    No provider is enabled until the parent has opened the realtime consumer.
    """
    api, bundle, started, enabled_at = controller(), None, False, None
    result = {'stopped': False, 'trace_started': False, 'provider_enabled': False,
              'owner_pid': os.getpid(), 'event_ids': list(EVENT_FIELDS)}
    try:
        bundle = build_filters(target)
        storage, _ = properties(name, guid)
        trace = U64()
        status = api.StartTraceW(c.byref(trace), name, storage)
        if status: raise RuntimeError(f'start_trace_failed_{status}')
        started = True
        result['trace_started'] = True
        pipe.send({'ready': True})
        deadline, enabled = time.monotonic() + 10, False
        while time.monotonic() < deadline:
            if not pipe.poll(0.05): continue
            try: command = pipe.recv()
            except EOFError: break
            if command == 'done': break
            if command != 'enable' or enabled: raise RuntimeError('invalid_owner_command')
            enable = Enable(2, (4 if stacks else 0) | 0x10, 0,
                            GUID.from_buffer_copy(guid), bundle[4], 3 if stacks else 2)
            # Includes synchronous EnableTraceEx2 time within the five-second cap.
            enabled_at = time.monotonic()
            deadline = min(deadline, enabled_at + 5)
            status = api.EnableTraceEx2(trace, GUID.from_buffer_copy(PROVIDER), 1,
                                       4, 0x2000, 0, 1000, c.byref(enable))
            if status: raise RuntimeError(f'enable_filtered_provider_failed_{status}')
            enabled = True
            result['provider_enabled'] = True
            pipe.send({'enabled': True})
    except BaseException as exc:
        result['error'] = str(exc)
    finally:
        if started: result.update(stop_owned(api, name, guid, trace))
        else: result.update(stopped=True, never_started=True)
        if enabled_at is not None:
            result['enable_to_stop_seconds'] = time.monotonic() - enabled_at
        if bundle:
            meta, owned, aggregate, _, _ = bundle
            meta.dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(aggregate))
            for item in owned: meta.dll.TdhDeletePayloadFilter(c.byref(item))
        try: pipe.send({'finished': result})
        except (EOFError, BrokenPipeError): pass
        pipe.close()


def matches(event_id, data, target):
    definition = EVENT_FIELDS.get(event_id)
    if not definition or len(data) != definition[2]:
        return False
    value = struct.unpack_from('<I', data, definition[1])[0]
    return value == target['tid' if event_id == 2 else 'pid']


class Evidence:
    def __init__(self, target):
        self.target = target
        self.accepted = self.rejected = self.malformed = self.other_provider = 0
        self.callbacks = 0
        self.stacks = []
        self.unknown_stack_source = 0
        self.abort = threading.Event()

    def consume(self, pointer):
        self.callbacks += 1
        try:
            record = pointer.contents
            provider = bytes(record.header.provider)
            if provider == SESSION_METADATA:
                self.other_provider += 1  # Known ETW bookkeeping, no fields copied.
                return
            if provider != PROVIDER:
                self.rejected += 1; self.abort.set(); return
            event_id = record.header.descriptor.id
            expected = EVENT_FIELDS.get(event_id)
            if record.header.descriptor.version != 0 or not expected or record.data_length != expected[2]:
                self.malformed += 1; self.abort.set(); return
            data = c.string_at(record.data, record.data_length)
            if not matches(event_id, data, self.target):
                self.rejected += 1; self.abort.set(); return
            self.accepted += 1
            # Event payload ownership is not enough to attribute its caller stack.
            if record.header.pid != self.target['pid']:
                self.unknown_stack_source += 1
                return
            if record.extended_count > 32:
                self.malformed += 1; self.abort.set(); return
            for i in range(record.extended_count):
                ext = record.extended[i]
                if ext.kind != 6 or not 8 <= ext.size <= 8 + 192 * 8 or (ext.size - 8) % 8:
                    continue
                raw = c.string_at(ext.data, ext.size)
                frames = []
                for address in struct.unpack('<' + 'Q' * ((ext.size - 8) // 8), raw[8:]):
                    for base, size, name in self.target['modules']:
                        if base <= address < base + size:
                            frames.append({'module': name, 'rva': hex(address - base)}); break
                if frames and len(self.stacks) < 8:
                    self.stacks.append(frames[:64])
        except BaseException:
            self.malformed += 1
            self.abort.set()


def validate_schema(event_id, fields):
    if tuple((field['name'], field['in_type']) for field in fields) != EVENT_SCHEMAS.get(event_id):
        raise RuntimeError('focus_payload_schema_mismatch')


def build_filters(target):
    meta, provider = Metadata(), GUID.from_buffer_copy(PROVIDER)
    descriptors = event_descriptors(meta.buffer('TdhEnumerateManifestProviderEvents', provider))
    chosen = {struct.unpack_from('<H', d)[0]: d for d in descriptors
              if struct.unpack_from('<H', d)[0] in EVENT_FIELDS and d[2] == 0}
    if set(chosen) != set(EVENT_FIELDS): raise RuntimeError('focus_manifest_incomplete')
    dll, owned = meta.dll, []
    call(dll, 'TdhAggregatePayloadFilters', U32, [U32, c.POINTER(PTR), PTR, c.POINTER(Filter)])
    call(dll, 'TdhCleanupPayloadEventFilterDescriptor', U32, [c.POINTER(Filter)])
    aggregate = Filter()
    try:
        for event_id, raw in chosen.items():
            descriptor = Descriptor.from_buffer_copy(raw)
            info = parse_event(meta.buffer('TdhGetManifestEventInformation', provider, c.byref(descriptor)))
            validate_schema(event_id, info['fields'])
            field = EVENT_FIELDS[event_id][0]
            predicate = PayloadPredicate(field, 0, str(target['tid' if event_id == 2 else 'pid']))
            result = PTR()
            status = dll.TdhCreatePayloadFilter(provider, c.byref(descriptor), False, 1,
                                                c.byref(predicate), c.byref(result))
            if result.value: owned.append(result)
            if status or not result.value: raise RuntimeError(f'payload_construction_failed_{status}')
        array = (PTR * len(owned))(*(p.value for p in owned))
        status = dll.TdhAggregatePayloadFilters(len(array), array, None, c.byref(aggregate))
        if status or not aggregate.pointer: raise RuntimeError(f'payload_aggregate_failed_{status}')
        encoded_ids = struct.pack('<BBH' + 'H' * len(EVENT_FIELDS), 1, 0, len(EVENT_FIELDS), *EVENT_FIELDS)
        event_ids = c.create_string_buffer(encoded_ids)
        filters = (Filter * 3)(aggregate, Filter(c.addressof(event_ids), len(encoded_ids), 0x80000200),
                               Filter(c.addressof(event_ids), len(encoded_ids), 0x80001000))
        return meta, owned, aggregate, event_ids, filters
    except BaseException:
        if aggregate.pointer: dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(aggregate))
        for item in owned: dll.TdhDeletePayloadFilter(c.byref(item))
        raise


def exercise(pipe):
    pipe.send('exercise')
    if not pipe.poll(0.5): raise RuntimeError('synthetic_exercise_timeout')
    result = pipe.recv()
    if result.get('error') or not result.get('local_focus_verified'):
        raise RuntimeError('synthetic_local_focus_not_verified')
    return result


def stimulus_counts():
    return {'exercises': 0, 'local_focus_verified': 0, 'foreground_api_success': 0,
            'own_foreground_readbacks': 0, 'foreground_transitions': 0}


def record_stimulus(counts, result):
    # Boolean checks reject missing telemetry from an older/mismatched child.
    fields = ('exercised', 'local_focus_verified', 'foreground_result',
              'own_foreground_before', 'own_foreground_after')
    if any(type(result.get(field)) is not bool for field in fields):
        raise RuntimeError('incomplete_source_telemetry')
    if not result['exercised'] or not result['local_focus_verified']:
        raise RuntimeError('synthetic_local_focus_not_verified')
    counts['exercises'] += 1
    counts['local_focus_verified'] += 1
    counts['foreground_api_success'] += int(result['foreground_result'])
    counts['own_foreground_readbacks'] += int(result['own_foreground_after'])
    counts['foreground_transitions'] += int(result['foreground_result']
        and not result['own_foreground_before'] and result['own_foreground_after'])


def source_qualified(stimuli):
    return len(stimuli) == 2 and all(item['foreground_transitions'] > 0 for item in stimuli)


def probe_source(pipes, initial):
    """Bounded local-window exercise, no ETW controller or provider calls."""
    result = {'completed': False, 'trace_started': False, 'source_qualified': False,
              'stimuli': [stimulus_counts(), stimulus_counts()]}
    for _ in range(4):
        for index, pipe in enumerate(pipes):
            if input_snapshot() != initial:
                raise RuntimeError('user_desktop_changed')
            record_stimulus(result['stimuli'][index], exercise(pipe))
            time.sleep(0.05)
    result['completed'] = True
    result['source_qualified'] = source_qualified(result['stimuli'])
    if not result['source_qualified']:
        result['limitation'] = 'private_desktop_foreground_source_not_verified'
    return result


def run_phase(ctx, targets, pipes, selected, stacks, initial):
    name = 'WechatBridge-SyntheticFocus-' + uuid.uuid4().hex
    guid = uuid.uuid4().bytes_le
    evidence, api = Evidence(targets[selected]), controller()
    ready, remote = ctx.Pipe()
    owner = ctx.Process(target=session_owner,
                        args=(remote, name, guid, targets[selected], stacks))
    consumer = U64(0xffffffffffffffff)
    capture, thread, callback, process_status = None, None, None, []
    result = {'phase': 'stack' if stacks else 'filter', 'selected': selected, 'passed': False,
              'session_name': name, 'trace_started': False, 'stack_requested': stacks,
              'event_ids': list(EVENT_FIELDS), 'exercise_counts': [0, 0],
              'stimuli': [stimulus_counts(), stimulus_counts()]}
    try:
        owner.start(); remote.close()
        result['owner_pid'] = owner.pid
        if not ready.poll(4): raise RuntimeError('session_owner_ready_timeout')
        message = ready.recv()
        if message != {'ready': True}:
            result['owner_cleanup'] = message.get('finished', {})
            raise RuntimeError('session_owner_not_ready')
        result['trace_started'] = True
        callback = CALLBACK(None, c.POINTER(Record))(evidence.consume)
        capture = Logfile(); capture.name = name; capture.mode = 0x10000100
        capture.callback = c.cast(callback, PTR)
        consumer.value = api.OpenTraceW(c.byref(capture))
        if consumer.value == 0xffffffffffffffff: raise RuntimeError('open_trace_failed')
        thread = threading.Thread(target=lambda: process_status.append(api.ProcessTrace(c.byref(consumer), 1, None, None)), daemon=True)
        thread.start()
        ready.send('enable')
        if not ready.poll(2): raise RuntimeError('session_owner_enable_timeout')
        message = ready.recv()
        if message != {'enabled': True}:
            result['owner_cleanup'] = message.get('finished', {})
            raise RuntimeError('session_owner_enable_failed')
        result['provider_enabled'] = True
        enabled_at = time.monotonic()
        while time.monotonic() - enabled_at < 2:
            for i in (1 - selected, selected):
                if evidence.abort.is_set(): raise RuntimeError('filter_or_metadata_violation')
                if not owner.is_alive(): raise RuntimeError('session_owner_exited_early')
                if input_snapshot() != initial: raise RuntimeError('user_desktop_changed')
                record_stimulus(result['stimuli'][i], exercise(pipes[i]))
                result['exercise_counts'][i] += 1
                time.sleep(0.05)
        result['capture_seconds'] = time.monotonic() - enabled_at
    except BaseException as exc:
        result['error'] = str(exc)
    finally:
        if owner.pid:
            try:
                ready.send('done')
                if ready.poll(3):
                    message = ready.recv()
                    if 'finished' in message: result['owner_cleanup'] = message['finished']
            except (EOFError, BrokenPipeError): pass
            owner.join(1)
            if owner.is_alive():
                # Prevent a delayed StartTrace or EnableTraceEx2 from racing cleanup.
                owner.terminate(); owner.join(2)
                result['owner_terminated'] = True
            result['owner_exitcode'] = owner.exitcode
        # Parent fault recovery is limited to our random name AND matching GUID.
        result['cleanup_verification'] = (stop_owned(api, name, guid) if not owner.is_alive()
                                         else {'stopped': False, 'error': 'owner_still_alive'})
        if thread: thread.join(2)
        if consumer.value != 0xffffffffffffffff: api.CloseTrace(consumer)
        if thread and thread.is_alive(): thread.join(1)
        if thread and thread.is_alive():
            _LIVE_CONSUMERS.append((thread, callback, capture, consumer, evidence, api))
        ready.close()
        cleanup = result.get('owner_cleanup', {})
        result.update(callbacks=evidence.callbacks, session_metadata_events=evidence.other_provider,
                      accepted=evidence.accepted, rejected=evidence.rejected,
                      malformed=evidence.malformed, unknown_stack_source=evidence.unknown_stack_source,
                      own_user_stacks=evidence.stacks, consumer_finished=not thread or not thread.is_alive(),
                      process_trace_status=process_status, positive_observed=evidence.accepted > 0)
        result['passed'] = bool(not result.get('error') and evidence.accepted and not evidence.rejected
            and not evidence.malformed and source_qualified(result['stimuli']) and cleanup.get('stopped')
            and not cleanup.get('error') and cleanup.get('events_lost') == 0
            and result['cleanup_verification'].get('stopped') and result['consumer_finished']
            and process_status == [0] and result.get('owner_exitcode') == 0
            and not result.get('owner_terminated') and cleanup.get('enable_to_stop_seconds', 99) <= 5
            and (not stacks or evidence.stacks))
    return result


def create_private_desktop(user, name, input_name):
    kernel = c.WinDLL('kernel32')
    tid = call(kernel, 'GetCurrentThreadId', U32, [])()
    get_thread = call(user, 'GetThreadDesktop', PTR, [U32])
    original = get_thread(tid)
    original_name = desktop_name(user, original)
    desktop = None
    try:
        try:
            desktop = call(user, 'CreateDesktopW', PTR, [c.c_wchar_p, c.c_wchar_p, PTR, U32, U32, PTR])(
                name, None, None, 0, 0x83, None)
            if not desktop or desktop_name(user, desktop) != name or name == input_name:
                raise RuntimeError('private_desktop_creation_failed')
        finally:
            # CreateDesktop assigns the calling thread; restore that association,
            # without changing the workstation input desktop or any window focus.
            if not call(user, 'SetThreadDesktop', c.c_int, [PTR])(original):
                raise RuntimeError('controller_thread_desktop_restore_failed')
            if desktop_name(user, get_thread(tid)) != original_name:
                raise RuntimeError('controller_thread_desktop_mismatch')
    except BaseException:
        if desktop: call(user, 'CloseDesktop', c.c_int, [PTR])(desktop)
        raise
    return desktop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run-synthetic', action='store_true')
    mode.add_argument('--probe-source', action='store_true')
    args = parser.parse_args()
    if not args.run_synthetic and not args.probe_source:
        print(json.dumps({'trace_started': False, 'live_diagnostic_ready': False,
                          'entrypoint': '--probe-source',
                          'requires': 'Windows x64, isolated desktop; ETW requires a verified source and privilege'}))
        return 0
    abi_check()
    if os.name != 'nt': raise RuntimeError('windows_required')
    ctx, user = mp.get_context('spawn'), c.WinDLL('user32', use_last_error=True)
    initial, name = input_snapshot(), 'BridgeSynthetic-' + uuid.uuid4().hex
    desktop = None
    children, pipes, targets = [], [], []
    report = {'schema': 2, 'passed': False, 'live_diagnostic_ready': False, 'phases': [],
              'trace_started': False, 'mode': 'source' if args.probe_source else 'etw',
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        desktop = create_private_desktop(user, name, initial[0])
        for _ in range(2):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=window_child, args=(child, name))
            process.start(); child.close(); children.append(process); pipes.append(parent)
            if not parent.poll(4): raise RuntimeError('private_child_timeout')
            target = parent.recv()
            if not target.get('ready') or target['pid'] != process.pid: raise RuntimeError('private_child_identity_failed')
            targets.append(target)
        report['source_probe'] = probe_source(pipes, initial)
        if args.run_synthetic and report['source_probe']['source_qualified']:
            for selected in (0, 1):
                phase = run_phase(ctx, targets, pipes, selected, False, initial)
                report['phases'].append(phase)
                if not phase['passed']: break
            if len(report['phases']) == 2 and all(p['passed'] for p in report['phases']):
                report['phases'].append(run_phase(ctx, targets, pipes, 0, True, initial))
            report['passed'] = len(report['phases']) == 3 and all(p['passed'] for p in report['phases'])
        elif args.run_synthetic:
            report['error'] = 'foreground_source_not_verified_no_trace_started'
        report['trace_started'] = any(phase['trace_started'] for phase in report['phases'])
    except BaseException as exc:
        report['error'] = str(exc)
    finally:
        for pipe in pipes:
            try: pipe.send('stop')
            except (EOFError, BrokenPipeError): pass
        for process in children:
            process.join(2)
            if process.is_alive(): process.terminate(); process.join(2)
        for pipe in pipes: pipe.close()
        report['children_finished'] = all(not process.is_alive() for process in children)
        report['desktop_closed'] = not desktop or bool(call(user, 'CloseDesktop', c.c_int, [PTR])(desktop))
        try: report['user_desktop_unchanged'] = input_snapshot() == initial
        except BaseException as exc:
            report['user_desktop_unchanged'] = False
            report['final_snapshot_error'] = str(exc)
        report['passed'] &= report['children_finished'] and report['desktop_closed'] and report['user_desktop_unchanged']
        report['source_unchanged'] = report['source_sha256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report['passed'] &= report['source_unchanged']
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
