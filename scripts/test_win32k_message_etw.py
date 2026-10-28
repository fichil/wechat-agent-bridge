"""Own-window Win32k event 60 experiment; default does nothing native.

--probe-source creates two hidden message-only windows on a private non-input
desktop and exchanges fixed integer messages. --run-synthetic additionally
tests receiver PID/TID payload filters, then stacks only after both controls.
Never accepts a PID/HWND, activates a window, uses the clipboard, requests
elevation, installs a manifest, or claims focus-event/live-client readiness.
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
from test_focus_etw import (U32, U64, PTR, GUID, Descriptor, Record, Logfile, Filter,
    Enable, CALLBACK, PROVIDER, SESSION_METADATA, abi_check, call, controller,
    properties, stop_owned, input_snapshot, desktop_name, create_private_desktop, module_ranges)
from inspect_focus_events import Metadata, PayloadPredicate, event_descriptors, parse_event

COUNT, MESSAGE, MAGIC, ACK = 4, 0x8321, 0x4D314554, 0x25100000
SCHEMA = [('CallbackCount', 3, 1), ('pqmsg', 16, 8), ('hwnd', 16, 8),
          ('WParam', 16, 8), ('LParam', 16, 8), ('message', 8, 4),
          ('inputReadyTimeMs', 8, 4), ('flags', 8, 4), ('pidReceiver', 8, 4), ('tidReceiver', 8, 4)]
_LIVE_CONSUMERS = []


def safe(action):
    try: return action()
    except BaseException as exc: return {'error': str(exc)}


def fingerprints():
    directory = Path(__file__).resolve().parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('test_win32k_message_etw.py', 'test_focus_etw.py', 'inspect_focus_events.py')}


def schema_offsets(info):
    if (info['id'], info['version'], info['channel'], info['level'], info['opcode'], info['task_id']) != (60, 0, 21, 4, 1, 60):
        raise RuntimeError('event60_descriptor_mismatch')
    if int(info['keyword'], 16) != 0x400000000400000:
        raise RuntimeError('event60_keyword_mismatch')
    fields = info['fields']
    actual = [(f['name'], f['in_type'], f['length_or_index']) for f in fields]
    if actual != SCHEMA or any(f['flags'] or f['count_or_index'] != 1 or not f['top_level'] for f in fields):
        raise RuntimeError('event60_schema_mismatch')
    offsets, position = {}, 0
    for name, _, size in actual:
        offsets[name] = position
        position += size
    if position != 53: raise RuntimeError('event60_payload_length_mismatch')
    return offsets


def metadata():
    meta, provider = Metadata(), GUID.from_buffer_copy(PROVIDER)
    raw = [item for item in event_descriptors(meta.buffer('TdhEnumerateManifestProviderEvents', provider))
           if struct.unpack_from('<H', item)[0] == 60 and item[2] == 0]
    if len(raw) != 1: raise RuntimeError('event60_descriptor_ambiguous')
    event = Descriptor.from_buffer_copy(raw[0])
    blob = meta.buffer('TdhGetManifestEventInformation', provider, c.byref(event))
    if blob[:16] != PROVIDER or blob[32:48] != raw[0]: raise RuntimeError('event60_metadata_identity_mismatch')
    info = parse_event(blob)
    offsets = schema_offsets(info)
    return meta, provider, event, info, offsets


def check_peer(expected, actual_pid, actual_tid, visible):
    if expected['pid'] != actual_pid or expected['tid'] != actual_tid or visible:
        raise RuntimeError('synthetic_peer_identity_or_visibility_mismatch')


class RoundState:
    def __init__(self):
        self.nonce, self.received, self.sent = None, set(), set()

    def arm(self, nonce):
        if type(nonce) is not int or not 1 <= nonce <= 0x7fffffff or nonce == self.nonce:
            raise RuntimeError('synthetic_round_nonce_invalid')
        self.nonce, self.received, self.sent = nonce, set(), set()

    def receive(self, sequence, token):
        if self.nonce is None or token != (MAGIC << 32) | self.nonce or not 1 <= sequence <= COUNT or sequence in self.received:
            return False
        self.received.add(sequence)
        return True

    def sending(self, sequence):
        if self.nonce is None or type(sequence) is not int or not 1 <= sequence <= COUNT or sequence in self.sent:
            raise RuntimeError('synthetic_send_duplicate_or_unarmed')
        self.sent.add(sequence)  # Claim before the attempt; uncertain results never retry.
        return (MAGIC << 32) | self.nonce


class WindowClass(c.Structure):
    _fields_ = [('size', U32), ('style', U32), ('proc', PTR), ('class_extra', c.c_int),
                ('window_extra', c.c_int), ('instance', PTR), ('icon', PTR), ('cursor', PTR),
                ('background', PTR), ('menu', c.c_wchar_p), ('name', c.c_wchar_p), ('small_icon', PTR)]


def window_child(pipe, desktop_id):
    user, kernel = c.WinDLL('user32', use_last_error=True), c.WinDLL('kernel32')
    desktop, hwnd, atom, original = None, None, 0, None
    cleanup, sent, received, bad, peer = {}, 0, 0, 0, None
    round_state = RoundState()
    try:
        tid = call(kernel, 'GetCurrentThreadId', U32, [])()
        get_desktop = call(user, 'GetThreadDesktop', PTR, [U32])
        original = get_desktop(tid)
        desktop = call(user, 'OpenDesktopW', PTR, [c.c_wchar_p, U32, c.c_int, U32])(desktop_id, 0, False, 0x83)
        if not desktop or not call(user, 'SetThreadDesktop', c.c_int, [PTR])(desktop):
            raise RuntimeError('private_desktop_attach_failed')
        if desktop_name(user, get_desktop(tid)) != desktop_id or input_snapshot()[0] == desktop_id:
            raise RuntimeError('private_desktop_not_isolated')
        def dispatch(_hwnd, message, wparam, lparam):
            nonlocal received, bad
            if message == 0x81: return 1  # WM_NCCREATE; permit our hidden window creation.
            if message == MESSAGE:
                if round_state.receive(wparam, lparam):
                    received += 1
                    return ACK + wparam
                bad += 1
            return 0  # No focus, system-command handling, forwarding or pointer dereference.
        wndproc = CALLBACK(c.c_ssize_t, PTR, U32, U64, c.c_ssize_t)(dispatch)
        instance = call(kernel, 'GetModuleHandleW', PTR, [c.c_wchar_p])(None)
        class_name = 'BridgeMessage-' + uuid.uuid4().hex
        cls = WindowClass(c.sizeof(WindowClass), 0, c.cast(wndproc, PTR), 0, 0, instance,
                          None, None, None, None, class_name, None)
        atom = call(user, 'RegisterClassExW', c.c_uint16, [c.POINTER(WindowClass)])(c.byref(cls))
        if not atom: raise RuntimeError('synthetic_class_registration_failed')
        create = call(user, 'CreateWindowExW', PTR, [U32, c.c_wchar_p, c.c_wchar_p, U32,
                      c.c_int, c.c_int, c.c_int, c.c_int, PTR, PTR, PTR, PTR])
        hwnd = create(0, class_name, '', 0, 0, 0, 0, 0, PTR(-3), None, instance, None)
        visible = call(user, 'IsWindowVisible', c.c_int, [PTR])
        if not hwnd or visible(hwnd): raise RuntimeError('hidden_window_creation_failed')
        get_owner = call(user, 'GetWindowThreadProcessId', U32, [PTR, c.POINTER(U32)])
        send = call(user, 'SendMessageTimeoutW', c.c_ssize_t, [PTR, U32, U64, c.c_ssize_t, U32, U32, c.POINTER(U64)])
        pipe.send({'ready': True, 'pid': os.getpid(), 'tid': tid, 'hwnd': hwnd, 'modules': module_ranges()})
        peek = call(user, 'PeekMessageW', c.c_int, [PTR, PTR, U32, U32, U32])
        translate = call(user, 'TranslateMessage', c.c_int, [PTR])
        dispatch_message = call(user, 'DispatchMessageW', c.c_ssize_t, [PTR])
        message_buffer, deadline = c.create_string_buffer(48), time.monotonic() + 30
        while time.monotonic() < deadline:
            while peek(message_buffer, None, 0, 0, 1):
                translate(message_buffer); dispatch_message(message_buffer)
            if not pipe.poll(0.01): continue
            command = pipe.recv()
            if command == 'stop': break
            if command == 'stats':
                pipe.send({'sent': sent, 'received': received, 'bad': bad}); continue
            if isinstance(command, dict) and set(command) == {'round'}:
                round_state.arm(command['round'])
                pipe.send({'armed': True}); continue
            if isinstance(command, dict) and set(command) == {'bind'} and peer is None:
                peer = command['bind']
                if peer['pid'] == os.getpid(): raise RuntimeError('same_process_peer_forbidden')
                pipe.send({'bound': True}); continue
            if not isinstance(command, dict) or set(command) != {'send'} or peer is None:
                raise RuntimeError('invalid_synthetic_command')
            sequence = command['send']
            token = round_state.sending(sequence)
            if input_snapshot()[0] == desktop_id or desktop_name(user, get_desktop(tid)) != desktop_id:
                raise RuntimeError('private_desktop_changed')
            pid = U32()
            peer_tid = get_owner(peer['hwnd'], c.byref(pid))
            check_peer(peer, pid.value, peer_tid, bool(visible(peer['hwnd'])))
            if desktop_name(user, get_desktop(peer_tid)) != desktop_id:
                raise RuntimeError('peer_desktop_mismatch')
            result = U64()
            c.set_last_error(0)
            status = send(peer['hwnd'], MESSAGE, sequence, token, 0x23, 200, c.byref(result))
            if not status or result.value != ACK + sequence:
                raise RuntimeError('synthetic_send_unknown_or_failed')
            sent += 1
            pipe.send({'acknowledged': True})
    except BaseException as exc:
        try: pipe.send({'error': str(exc)})
        except (EOFError, BrokenPipeError): pass
    finally:
        if hwnd: cleanup['window_destroyed'] = bool(call(user, 'DestroyWindow', c.c_int, [PTR])(hwnd))
        if atom: cleanup['class_unregistered'] = bool(call(user, 'UnregisterClassW', c.c_int, [c.c_wchar_p, PTR])(class_name, instance))
        if original: cleanup['thread_desktop_restored'] = bool(call(user, 'SetThreadDesktop', c.c_int, [PTR])(original))
        if desktop: cleanup['desktop_closed'] = bool(call(user, 'CloseDesktop', c.c_int, [PTR])(desktop))
        try: pipe.send({'finished': cleanup})
        except (EOFError, BrokenPipeError): pass
        pipe.close()


class Source:
    def __init__(self):
        self.ctx, self.user = mp.get_context('spawn'), c.WinDLL('user32', use_last_error=True)
        self.initial, self.name = input_snapshot(), 'BridgeMessageDesktop-' + uuid.uuid4().hex
        self.desktop, self.children, self.pipes, self.targets = None, [], [], []

    def start(self):
        self.desktop = create_private_desktop(self.user, self.name, self.initial[0])
        for _ in range(2):
            parent, child = self.ctx.Pipe()
            process = self.ctx.Process(target=window_child, args=(child, self.name))
            self.children.append(process); self.pipes.append(parent)
            try: process.start()
            finally: child.close()
            if not parent.poll(4): raise RuntimeError('synthetic_child_ready_timeout')
            target = parent.recv()
            if not target.get('ready') or target.get('pid') != process.pid:
                raise RuntimeError('synthetic_child_identity_failed')
            self.targets.append(target)
        for index in range(2):
            peer = self.targets[1 - index]
            answer = self.command(index, {'bind': {key: peer[key] for key in ('pid', 'tid', 'hwnd')}})
            if answer != {'bound': True}: raise RuntimeError('synthetic_bind_failed')

    def command(self, index, command):
        if input_snapshot() != self.initial: raise RuntimeError('user_desktop_changed')
        self.pipes[index].send(command)
        if not self.pipes[index].poll(1): raise RuntimeError('synthetic_command_timeout')
        result = self.pipes[index].recv()
        if 'error' in result: raise RuntimeError(result['error'])
        return result

    def send(self, sender, sequence):
        if self.command(sender, {'send': sequence}) != {'acknowledged': True}:
            raise RuntimeError('synthetic_ack_missing')

    def stats(self):
        return [self.command(index, 'stats') for index in range(2)]

    def arm(self):
        nonce = (uuid.uuid4().int & 0x7fffffff) or 1
        for index in range(2):
            if self.command(index, {'round': nonce}) != {'armed': True}:
                raise RuntimeError('synthetic_round_arm_failed')

    def close(self):
        report = {'children': []}
        for pipe in self.pipes:
            try: pipe.send('stop')
            except (EOFError, OSError): pass
        for process, pipe in zip(self.children, self.pipes):
            result = {}
            try:
                if pipe.poll(2): result = pipe.recv().get('finished', {})
            except (EOFError, OSError): result['pipe_cleanup_failed'] = True
            try:
                if process.pid:
                    process.join(1)
                    if process.is_alive(): process.terminate(); process.join(2); result['terminated'] = True
            except BaseException as exc: result['process_cleanup_error'] = str(exc)
            result['exitcode'] = safe(lambda: process.exitcode)
            result['stopped'] = safe(process.is_alive) is False
            report['children'].append(result)
            safe(pipe.close)
        report['desktop_closed'] = not self.desktop or safe(lambda: bool(call(self.user, 'CloseDesktop', c.c_int, [PTR])(self.desktop)))
        report['user_desktop_unchanged'] = safe(lambda: input_snapshot() == self.initial)
        report['passed'] = bool(len(report['children']) == 2 and report['desktop_closed'] is True
            and report['user_desktop_unchanged'] is True and all(
                item.get('exitcode') == 0 and not item.get('terminated') and item.get('stopped')
                and all(item.get(key) for key in ('window_destroyed', 'class_unregistered',
                                                 'thread_desktop_restored', 'desktop_closed'))
                for item in report['children']))
        return report


def deltas(before, after):
    return [{key: right[key] - left[key] for key in ('sent', 'received', 'bad')}
            for left, right in zip(before, after)]


def source_probe(source):
    source.arm()
    before = source.stats()
    for sequence in range(1, COUNT + 1):
        source.send(0, sequence); source.send(1, sequence)
    delta = deltas(before, source.stats())
    return {'passed': delta == [{'sent': COUNT, 'received': COUNT, 'bad': 0}] * 2,
            'per_child_counts': delta}


def build_filters(target):
    meta, provider, event, _, _ = metadata()
    predicates = (PayloadPredicate * 2)(PayloadPredicate('pidReceiver', 0, str(target['pid'])),
                                         PayloadPredicate('tidReceiver', 0, str(target['tid'])))
    single, aggregate = PTR(), Filter()
    call(meta.dll, 'TdhAggregatePayloadFilters', U32, [U32, c.POINTER(PTR), PTR, c.POINTER(Filter)])
    call(meta.dll, 'TdhCleanupPayloadEventFilterDescriptor', U32, [c.POINTER(Filter)])
    try:
        status = meta.dll.TdhCreatePayloadFilter(provider, c.byref(event), False, 2, predicates, c.byref(single))
        if status or not single.value: raise RuntimeError(f'payload_create_failed_{status}')
        array = (PTR * 1)(single.value)
        status = meta.dll.TdhAggregatePayloadFilters(1, array, None, c.byref(aggregate))
        if status or not aggregate.pointer: raise RuntimeError(f'payload_aggregate_failed_{status}')
        ids = c.create_string_buffer(struct.pack('<BBHH', 1, 0, 1, 60))
        filters = (Filter * 3)(aggregate, Filter(c.addressof(ids), 6, 0x80000200),
                               Filter(c.addressof(ids), 6, 0x80001000))
        return meta, aggregate, ids, filters
    except BaseException:
        if aggregate.pointer: meta.dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(aggregate))
        raise
    finally:
        if single.value: meta.dll.TdhDeletePayloadFilter(c.byref(single))


def session_owner(pipe, name, guid, receiver, stacks):
    api, bundle, started, enabled_at = controller(), None, False, None
    result = {'trace_started': False, 'provider_enabled': False}
    try:
        bundle = build_filters(receiver)
        storage, _ = properties(name, guid); trace = U64()
        status = api.StartTraceW(c.byref(trace), name, storage)
        if status: raise RuntimeError(f'start_trace_failed_{status}')
        started = result['trace_started'] = True
        pipe.send({'ready': True})
        deadline, enabled = time.monotonic() + 10, False
        while time.monotonic() < deadline:
            if not pipe.poll(0.05): continue
            try: command = pipe.recv()
            except EOFError: break
            if command == 'done': break
            if command != 'enable' or enabled: raise RuntimeError('invalid_owner_command')
            params = Enable(2, 0x10 | (4 if stacks else 0), 0, GUID.from_buffer_copy(guid), bundle[3], 3 if stacks else 2)
            enabled_at = time.monotonic(); deadline = min(deadline, enabled_at + 5)
            status = api.EnableTraceEx2(trace, GUID.from_buffer_copy(PROVIDER), 1, 4, 0x400000, 0, 1000, c.byref(params))
            if status: raise RuntimeError(f'enable_provider_failed_{status}')
            enabled = result['provider_enabled'] = True
            pipe.send({'enabled': True})
    except BaseException as exc: result['error'] = str(exc)
    finally:
        result['cleanup'] = safe(lambda: stop_owned(api, name, guid, trace)) if started else {'stopped': True, 'never_started': True}
        if enabled_at is not None: result['enable_to_stop_seconds'] = time.monotonic() - enabled_at
        if bundle: result['filter_cleanup_status'] = safe(lambda: bundle[0].dll.TdhCleanupPayloadEventFilterDescriptor(c.byref(bundle[1])))
        try: pipe.send({'finished': result})
        except (EOFError, BrokenPipeError): pass
        pipe.close()


class Evidence:
    def __init__(self, receiver, sender, offsets, stacks):
        self.receiver, self.sender, self.offsets, self.want_stacks = receiver, sender, offsets, stacks
        self.accepted = self.rejected = self.malformed = self.unknown_stack = 0
        self.callbacks = self.metadata_events = 0
        self.reasons = {}
        self.own_sender_rejection_ownership = {
            'selected_receiver': 0, 'opposite_receiver': 0, 'other_or_mixed': 0}
        self.stacks, self.abort = [], threading.Event()

    def reject(self, malformed=False, reason='unexpected_provider'):
        if malformed: self.malformed += 1
        else: self.rejected += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        self.abort.set()

    def consume(self, pointer):
        try:
            self.callbacks += 1
            record = pointer.contents
            if bytes(record.header.provider) == SESSION_METADATA:
                self.metadata_events += 1; return
            if bytes(record.header.provider) != PROVIDER: self.reject(); return
            desc = record.header.descriptor
            if (desc.id, desc.version, desc.channel, desc.level, desc.opcode, desc.task) != (60, 0, 21, 4, 1, 60):
                self.reject(True, 'descriptor'); return
            if record.data_length != 53 or not record.data: self.reject(True, 'length'); return
            if not record.header.flags & 0x40 or record.header.flags & 0x20:
                self.reject(True, 'flags'); return
            if record.header.pid != self.sender['pid'] or record.header.tid != self.sender['tid']:
                # A rejected event from the other exact, handshaken synthetic
                # child may expose only these ownership fields. Unknown header
                # identities still stop before any payload read. This is a
                # diagnostic classification, never acceptance or stack access.
                if record.header.pid == self.receiver['pid'] and record.header.tid == self.receiver['tid']:
                    values = tuple(int.from_bytes(c.string_at(record.data + self.offsets[field], size), 'little')
                                   for field, size in (('pidReceiver', 4), ('tidReceiver', 4), ('hwnd', 8)))
                    if values == tuple(self.receiver[key] for key in ('pid', 'tid', 'hwnd')):
                        category = 'selected_receiver'
                    elif values == tuple(self.sender[key] for key in ('pid', 'tid', 'hwnd')):
                        category = 'opposite_receiver'
                    else:
                        category = 'other_or_mixed'
                    self.own_sender_rejection_ownership[category] += 1
                self.reject(reason='sender'); return
            # No whole-payload copies. Read only the three ownership fields;
            # never persist hwnd, pqmsg, message arguments or callback addresses.
            for field, size, key in (('pidReceiver', 4, 'pid'), ('tidReceiver', 4, 'tid'), ('hwnd', 8, 'hwnd')):
                value = int.from_bytes(c.string_at(record.data + self.offsets[field], size), 'little')
                if value != self.receiver[key]: self.reject(reason='receiver'); return
            self.accepted += 1
            if self.accepted > COUNT: self.reject(reason='excess_count'); return
            if not self.want_stacks: return
            if record.extended_count > 32: self.reject(True, 'extra_count'); return
            if record.extended_count and not record.extended: self.reject(True, 'null_stack'); return
            found = False
            for index in range(record.extended_count):
                ext = record.extended[index]
                if ext.kind != 6: continue
                if not ext.data: self.reject(True, 'null_stack'); return
                if not 8 <= ext.size <= 1544 or (ext.size - 8) % 8: self.reject(True, 'stack_length'); return
                frames = []
                for frame in struct.unpack('<' + 'Q' * ((ext.size - 8) // 8), c.string_at(ext.data + 8, ext.size - 8)):
                    for base, size, name in self.sender['modules']:
                        if base <= frame < base + size:
                            frames.append({'module': name, 'rva': hex(frame - base)}); break
                if frames:
                    found = True
                    if len(self.stacks) < 8: self.stacks.append(frames[:64])
            if not found:
                self.unknown_stack += 1
                self.reasons['no_own_stack'] = self.reasons.get('no_own_stack', 0) + 1
                self.abort.set()
        except BaseException: self.reject(True, 'callback_exception')


def phase_passed(result):
    own = result.get('owner', {})
    return bool(not result.get('error') and not own.get('error') and result.get('accepted') == COUNT
        and all(result.get(key) == 0 for key in ('rejected', 'malformed', 'unknown_stack'))
        and result.get('source_counts') == [{'sent': COUNT, 'received': COUNT, 'bad': 0}] * 2
        and own.get('cleanup', {}).get('stopped') and own.get('cleanup', {}).get('events_lost') == 0
        and own.get('filter_cleanup_status') == 0 and own.get('enable_to_stop_seconds', 99) <= 5
        and result.get('owner_exitcode') == 0 and not result.get('owner_terminated')
        and result.get('cleanup_verification', {}).get('stopped') and result.get('consumer_finished')
        and result.get('process_trace_status') == [0] and result.get('close_trace_status') == 0
        and (not result.get('stack_requested') or result.get('own_user_stacks')))


def run_phase(source, receiver_index, stacks, offsets):
    receiver, sender = source.targets[receiver_index], source.targets[1 - receiver_index]
    name, guid = 'WechatBridge-Message-' + uuid.uuid4().hex, uuid.uuid4().bytes_le
    api, evidence = controller(), Evidence(receiver, sender, offsets, stacks)
    ready, remote = source.ctx.Pipe()
    process = source.ctx.Process(target=session_owner, args=(remote, name, guid,
                                  {key: receiver[key] for key in ('pid', 'tid')}, stacks))
    consumer, callback, capture, thread, statuses = U64(0xffffffffffffffff), None, None, None, []
    result = {'passed': False, 'receiver': receiver_index, 'stack_requested': stacks, 'session_name': name,
              'trace_started': False}
    try:
        source.arm()
        before = source.stats()
        try: process.start()
        finally: remote.close()
        result['owner_pid'] = process.pid
        if not ready.poll(4): raise RuntimeError('owner_ready_timeout')
        message = ready.recv()
        if message != {'ready': True}:
            result['owner'] = message.get('finished', {}); raise RuntimeError('owner_not_ready')
        result['trace_started'] = True
        callback = CALLBACK(None, c.POINTER(Record))(evidence.consume)
        capture = Logfile(); capture.name = name; capture.mode = 0x10000100; capture.callback = c.cast(callback, PTR)
        consumer.value = api.OpenTraceW(c.byref(capture))
        if consumer.value == 0xffffffffffffffff: raise RuntimeError('open_trace_failed')
        thread = threading.Thread(target=lambda: statuses.append(api.ProcessTrace(c.byref(consumer), 1, None, None)), daemon=True)
        thread.start(); ready.send('enable')
        if not ready.poll(2): raise RuntimeError('owner_enable_timeout')
        message = ready.recv()
        if message != {'enabled': True}:
            result['owner'] = message.get('finished', {}); raise RuntimeError('owner_enable_failed')
        for sequence in range(1, COUNT + 1):
            for sender_index in (receiver_index, 1 - receiver_index):
                if evidence.abort.is_set(): raise RuntimeError('event_scope_or_stack_failure')
                source.send(sender_index, sequence)
        result['source_counts'] = deltas(before, source.stats())
    except BaseException as exc: result['error'] = str(exc)
    finally:
        if process.pid:
            try:
                ready.send('done')
                if ready.poll(3):
                    message = ready.recv()
                    if 'finished' in message: result['owner'] = message['finished']
            except (EOFError, BrokenPipeError): pass
            process.join(1)
            if process.is_alive(): process.terminate(); process.join(2); result['owner_terminated'] = True
            result['owner_exitcode'] = process.exitcode
        result['cleanup_verification'] = safe(lambda: stop_owned(api, name, guid)) if not process.is_alive() else {'stopped': False}
        if thread: thread.join(2)
        if consumer.value != 0xffffffffffffffff: result['close_trace_status'] = safe(lambda: api.CloseTrace(consumer))
        if thread and thread.is_alive(): thread.join(1)
        if thread and thread.is_alive(): _LIVE_CONSUMERS.append((thread, callback, capture, consumer, evidence, api))
        ready.close()
        result.update(accepted=evidence.accepted, rejected=evidence.rejected, malformed=evidence.malformed,
                      unknown_stack=evidence.unknown_stack, own_user_stacks=evidence.stacks,
                      callbacks=evidence.callbacks, session_metadata_events=evidence.metadata_events,
                      rejection_reasons=evidence.reasons,
                      own_sender_rejection_ownership=evidence.own_sender_rejection_ownership,
                      consumer_finished=not thread or not thread.is_alive(), process_trace_status=statuses)
        result['passed'] = phase_passed(result)
    return result


def run(synthetic):
    abi_check()
    if os.name != 'nt' or c.sizeof(WindowClass) != 80: raise RuntimeError('windows_x64_required')
    before, source = fingerprints(), Source()
    report = {'schema': 1, 'passed': False, 'live_diagnostic_ready': False, 'focus_event_verified': False,
              'trace_started': False, 'source_fingerprints': before, 'phases': []}
    try:
        source.start()
        report['source_probe'] = source_probe(source)
        if not report['source_probe']['passed']: raise RuntimeError('source_probe_failed')
        if synthetic:
            _, _, _, info, offsets = metadata()
            report['metadata_schema'] = info
            for receiver in (0, 1):
                result = run_phase(source, receiver, False, offsets)
                report['phases'].append(result)
                if not result['passed']: break
            if len(report['phases']) == 2 and all(item['passed'] for item in report['phases']):
                report['phases'].append(run_phase(source, 0, True, offsets))
            report['passed'] = len(report['phases']) == 3 and all(item['passed'] for item in report['phases'])
        else: report['passed'] = True
    except BaseException as exc: report['error'] = str(exc)
    finally:
        report['cleanup'] = source.close()
        report['source_unchanged'] = fingerprints() == before
        report['trace_started'] = any(item.get('trace_started') or item.get('owner', {}).get('trace_started')
                                      for item in report['phases'])
        report['passed'] &= report['cleanup']['passed'] and report['source_unchanged']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--probe-source', action='store_true')
    mode.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args()
    if not args.probe_source and not args.run_synthetic:
        print(json.dumps({'trace_started': False, 'windows_created': False,
                          'live_diagnostic_ready': False, 'focus_event_verified': False}))
        return 0
    report = run(args.run_synthetic)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
