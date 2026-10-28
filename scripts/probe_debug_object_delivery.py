"""Owned-only native debugger: pre-retention and event-publication fault windows.

No live PID, windows or production integration. Unlike the earlier experiment,
the guardian owns the debug object BEFORE attachment, and a lost event is never
reconstructed from a fault-injection checkpoint, heartbeat or guessed TID.
"""
import argparse
from contextlib import contextmanager
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
from scripts import probe_debug_object_preservation as preservation
from scripts import probe_debug_object_recovery as recovery
from scripts import probe_debugger_resume_flag as owned

CHECKPOINTS = ('before_attach', 'after_attach', 'initial_delivered', 'intent_recorded',
               'armed_unrecorded', 'hit_recorded', 'restored_unrecorded', 'hit_unrecorded',
               'hit_before_decode')
TERMINAL_CHECKPOINTS = ('stop_intent', 'continued_unrecorded', 'detached_unrecorded')
TIMEOUT = 0x102
NATIVE_CREATE_PROCESS = 3
NATIVE_SINGLE_STEP = 8


class ClientId(c.Structure):
    _fields_ = [('pid', c.c_void_p), ('tid', c.c_void_p)]


class ExceptionRecord(c.Structure):
    _fields_ = [('code', c.c_uint32), ('flags', c.c_uint32), ('record', c.c_void_p),
                ('address', c.c_void_p), ('parameter_count', c.c_uint32),
                ('parameters', c.c_uint64 * 15)]


class ExceptionState(c.Structure):
    _fields_ = [('record', ExceptionRecord), ('first_chance', c.c_uint32)]


class NativePayload(c.Union):
    _fields_ = [('exception', ExceptionState), ('words', c.c_uint64 * 20)]


class NativeEvent(c.Structure):
    _fields_ = [('state', c.c_uint32), ('client', ClientId), ('data', NativePayload)]


def layout_checked():
    if (c.sizeof(c.c_void_p) != 8 or c.sizeof(ExceptionRecord) != 152 or
            c.sizeof(NativeEvent) != 184 or NativeEvent.data.offset != 24 or
            ExceptionRecord.parameters.offset != 32):
        raise RuntimeError('delivery_native_layout_mismatch')


def api():
    layout_checked()
    native = recovery.native_recovery_api()
    signatures = {
        'NtCreateDebugObject': [c.POINTER(c.c_void_p), c.c_uint32, c.c_void_p, c.c_uint32],
        'NtDebugActiveProcess': [c.c_void_p, c.c_void_p],
        'NtWaitForDebugEvent': [c.c_void_p, c.c_ubyte, c.POINTER(c.c_int64), c.POINTER(NativeEvent)],
    }
    for name, arguments in signatures.items():
        getattr(native, name).argtypes = arguments
        getattr(native, name).restype = c.c_int32
    return native


def status_checked(status, operation):
    if status != 0:
        raise RuntimeError(operation + '_' + format(status & 0xffffffff, '08x'))


def create_object(native):
    handle = c.c_void_p()
    # No kill-on-close flag, from birth. The owner never owns the only handle.
    status_checked(native.NtCreateDebugObject(c.byref(handle), 0x1f000f, None, 0), 'delivery_create')
    if not handle.value:
        raise RuntimeError('delivery_object_missing')
    return handle.value


@contextmanager
def mutation_handle(bound, access=0x800):
    bound.check()
    handle = bound.kernel.OpenProcess(access | 0x400 | 0x100000, False, bound.identity['pid'])
    try:
        if not handle or owned.creation_time(bound.kernel, handle) != bound.identity['created']:
            raise RuntimeError('delivery_process_handle_identity_changed')
        bound.check()
        yield handle
    finally:
        if handle:
            recovery.checked(bound.kernel.CloseHandle(handle), 'delivery_process_handle_close')


def duplicate_to_owner(bound, original):
    kernel = bound.kernel
    kernel.DuplicateHandle.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p,
        c.POINTER(c.c_void_p), c.c_uint32, c.c_int, c.c_uint32]
    kernel.DuplicateHandle.restype = c.c_int
    duplicate = c.c_void_p()
    with mutation_handle(bound, 0x40) as process:
        recovery.checked(kernel.DuplicateHandle(kernel.GetCurrentProcess(), original, process,
            c.byref(duplicate), 0, False, 2), 'delivery_duplicate')
    return duplicate.value


def native_event_handles(event):
    # Exact phnt ABI. These native event handles are ours, not target handles.
    if event.state == 2:
        values = [event.data.words[0]]
    elif event.state == 3:
        values = [event.data.words[0], event.data.words[1], event.data.words[3]]
    elif event.state == 9:
        values = [event.data.words[0]]
    else:
        values = []
    handles = [int(value) for value in values if value]
    if len(set(handles)) != len(handles):
        raise RuntimeError('delivery_native_handle_alias')
    return handles


def event_metadata(event):
    record = dict(state=int(event.state), pid=int(event.client.pid or 0), tid=int(event.client.tid or 0))
    if event.state in (6, 7, 8):
        exception = event.data.exception
        record.update(code=int(exception.record.code), address=int(exception.record.address or 0),
                      first_chance=int(exception.first_chance))
    return record


def wait_event(bound, handle, native, timeout_ms=100, *, storage=None, before_decode=None):
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 500:
        raise RuntimeError('delivery_wait_not_bounded')
    bound.check()
    timeout = c.c_int64(-timeout_ms * 10000)
    if storage is not None and c.sizeof(storage) != c.sizeof(NativeEvent):
        raise RuntimeError('delivery_shared_buffer_size_invalid')
    event = NativeEvent() if storage is None else NativeEvent.from_buffer(storage)
    status = native.NtWaitForDebugEvent(handle, False, c.byref(timeout), c.byref(event))
    if status == TIMEOUT:
        return None
    status_checked(status, 'delivery_wait')
    try:
        if before_decode is not None:
            before_decode(event)
        if event.client.pid != bound.identity['pid'] or event.state not in range(2, 11):
            raise RuntimeError('delivery_native_event_identity_invalid')
        return event_metadata(event)
    finally:
        for child_handle in native_event_handles(event):
            recovery.checked(bound.kernel.CloseHandle(child_handle), 'delivery_native_event_handle_close')


def continue_event(bound, handle, native, event):
    bound.check()
    if event['pid'] != bound.identity['pid'] or type(event['tid']) is not int or event['tid'] <= 0:
        raise RuntimeError('delivery_continue_identity_changed')
    client = ClientId(event['pid'], event['tid'])
    status_checked(native.NtDebugContinue(handle, c.byref(client), recovery.CONTINUE), 'delivery_continue')


def detach(bound, handle, native):
    with mutation_handle(bound) as process:
        status_checked(native.NtRemoveProcessDebug(process, handle), 'delivery_detach')


def validate_event(event, identity, *, hit):
    expected = {'state', 'pid', 'tid'} | ({'code', 'address', 'first_chance'} if hit else set())
    if (not isinstance(event, dict) or set(event) != expected or
            any(type(value) is not int for value in event.values()) or
            event['pid'] != identity['pid'] or event['tid'] != identity['main_tid'] or
            event['state'] != (NATIVE_SINGLE_STEP if hit else NATIVE_CREATE_PROCESS) or
            (hit and (event['code'] != recovery.SINGLE_STEP or event['address'] != identity['address'] or
                      event['first_chance'] != 1))):
        raise RuntimeError('delivery_published_event_invalid')
    return dict(event)


class Journal:
    """Recovery facts only. Fault labels are deliberately kept elsewhere."""
    def __init__(self, identity, original, thread_baseline=None):
        self.identity, self.original = dict(identity), list(original)
        self.pending = self.intent = None
        self.initial_seen = self.initial_continued = self.armed = self.hit_seen = False
        self.done = False
        self.wait_serial = 0
        self.thread_baseline = dict(thread_baseline) if thread_baseline is not None else None
        self.stop_ready = self.terminal_continued = False

    def begin_shared_wait(self, serial, storage):
        # The owner waits for this durable acknowledgement before the syscall.
        if (type(serial) is not int or serial != self.wait_serial + 1 or serial > 128 or
                self.pending is not None or self.done or self.stop_ready or
                c.sizeof(storage) != c.sizeof(NativeEvent) or any(bytes(storage))):
            raise RuntimeError('delivery_shared_wait_not_fresh')
        self.wait_serial = serial

    def accept(self, record):
        if not isinstance(record, dict) or len(record) != 1:
            raise RuntimeError('delivery_journal_shape_invalid')
        key, value = next(iter(record.items()))
        if key == 'event':
            if self.pending is not None or self.done:
                raise RuntimeError('delivery_duplicate_pending')
            hit = self.initial_seen
            event = validate_event(value, self.identity, hit=hit)
            if hit and (not self.initial_continued or not self.armed or self.hit_seen):
                raise RuntimeError('delivery_hit_out_of_order')
            self.pending = event
            self.initial_seen = True
            self.hit_seen |= hit
        elif key == 'arm_intent':
            if (self.intent is not None or not self.initial_seen or self.initial_continued or
                    not isinstance(value, dict) or set(value) != {'original', 'armed'} or
                    value['original'] != self.original or
                    value['armed'] != recovery.armed_registers(self.original, self.identity['address'])):
                raise RuntimeError('delivery_intent_invalid')
            self.intent = dict(original=list(value['original']), armed=list(value['armed']))
        elif key == 'armed':
            if value is not True or self.intent is None or self.armed or self.initial_continued:
                raise RuntimeError('delivery_armed_out_of_order')
            self.armed = True
        elif key == 'initial_continued':
            if value is not True or not self.armed or self.initial_continued or self.pending is None:
                raise RuntimeError('delivery_initial_continue_out_of_order')
            self.initial_continued = True
            self.pending = None
        elif key == 'stop_ready':
            if (self.thread_baseline is None or not self.hit_seen or self.pending is None or
                    self.stop_ready or self.done or value != recovery.encode_debug_context(self.original)):
                raise RuntimeError('delivery_stop_intent_invalid')
            self.stop_ready = True
        elif key == 'terminal_continued':
            if value is not True or not self.stop_ready or self.terminal_continued or self.done:
                raise RuntimeError('delivery_terminal_continue_out_of_order')
            self.terminal_continued, self.pending = True, None
        elif key == 'done':
            if value is not True or not self.hit_seen or self.done:
                raise RuntimeError('delivery_done_out_of_order')
            self.done, self.pending = True, None
        else:
            raise RuntimeError('delivery_not_a_recovery_fact')


def restore_before_continue(thread, intent, event):
    observed = thread.context()[2]
    original, armed = intent['original'], intent['armed']
    # A restored context may be the native write form before resumption.
    if observed in (original, recovery.encode_debug_context(original)):
        return 'original_already_present'
    if event['state'] == NATIVE_SINGLE_STEP:
        desired = recovery.safe_restore(original, armed, observed)
    elif event['state'] == NATIVE_CREATE_PROCESS and observed in (armed, recovery.encode_debug_context(armed)):
        desired = list(original)
    else:
        raise RuntimeError('delivery_register_ownership_changed')
    thread.set_registers(desired)
    return 'original_restored'


def shared_event_after_owner_exit(storage, journal):
    """Read only after the bound owner has exited, never from a fault label.

    The OS writes into this shared mapping directly. No owner-side copy or
    completion flag is needed for the tested post-return publication gap.
    Partial/unexpected output is rejected; atomic kernel delivery is not assumed.
    """
    if (journal.wait_serial <= 0 or not journal.initial_continued or journal.pending is not None or
            c.sizeof(storage) != c.sizeof(NativeEvent)):
        raise RuntimeError('delivery_shared_recovery_not_bound')
    raw = bytes(storage)
    if not any(raw):
        return None
    event = NativeEvent.from_buffer_copy(raw)
    return validate_event(event_metadata(event), journal.identity, hit=True)


def observe_terminal_thread(bound, thread):
    """Read a possibly running original thread with exactly one owned pause.

    This is observation, not removal of an unowned suspend count. The retained
    original thread object and its creation time are checked before suspension.
    """
    thread.check()
    observer = recovery.BoundThread(bound, thread.tid, thread.created, fixture_suspend=True)
    paused = False
    try:
        observer.check()
        prior = observer.kernel.SuspendThread(observer.handle)
        if prior == 0xffffffff:
            raise RuntimeError('delivery_terminal_observation_suspend_failed')
        paused = True
        return dict(thread_created=observer.created, prior_suspend_count=prior,
                    debug_registers=observer.context()[2])
    finally:
        try:
            if paused and observer.kernel.ResumeThread(observer.handle) != prior + 1:
                raise RuntimeError('delivery_terminal_observation_balance_changed')
        finally:
            observer.close()


def verify_terminal_original(observed, journal):
    baseline = journal.thread_baseline
    if (not journal.stop_ready or baseline is None or
            observed.get('thread_created') != baseline['thread_created'] or
            observed.get('prior_suspend_count') != baseline['prior_suspend_count'] or
            observed.get('debug_registers') not in (journal.original, recovery.encode_debug_context(journal.original))):
        raise RuntimeError('delivery_terminal_original_state_changed')


def recover(bound, thread, handle, journal, native, receipt, *, storage=None):
    # The caller has confirmed the exact owner Process object's death.
    bound.check()
    present = recovery.retention.debugger_present(bound)
    receipt['debugger_present_before'] = present
    if journal.stop_ready:
        observed = observe_terminal_thread(bound, thread)
        receipt['terminal_thread_observation'] = observed
        verify_terminal_original(observed, journal)
        # No further arming or waiting is permitted after stop_ready. This
        # experiment must validate detachment even if Continue never happened.
        # A missing Continue acknowledgement is NOT interpreted as success.
        receipt['continuation_acknowledged'] = journal.terminal_continued
        if present:
            detach(bound, handle, native)
            receipt['detached_without_repeating_continue'] = True
        else:
            receipt['already_detached_verified'] = True
        return
    if not present:
        if journal.intent is not None or journal.initial_seen:
            raise RuntimeError('delivery_debug_association_lost')
        receipt['no_attachment_to_remove'] = True
        return
    if journal.intent is None:
        # Write-ahead protocol means no hardware register write was authorized.
        detach(bound, handle, native)
        receipt['detached_without_register_mutation'] = True
        return
    event = journal.pending
    if event is None:
        # Do not infer the missing event from the test's named fault location.
        if storage is not None:
            event = shared_event_after_owner_exit(storage, journal)
            receipt.update(shared_wait_serial=journal.wait_serial, shared_event=event)
        if event is None:
            replay = wait_event(bound, handle, native, 200)
            receipt['native_replay'] = replay
            if replay is None:
                raise RuntimeError('delivery_outstanding_event_not_replayable')
            event = validate_event(replay, journal.identity, hit=True)
    receipt['register_action'] = restore_before_continue(thread, journal.intent, event)
    continue_event(bound, handle, native, event)
    receipt['event_continued'] = True
    detach(bound, handle, native)
    receipt['detached'] = True


def owner(pipe, identity, baseline, mode, checkpoint, nonce, storage=None):
    bound = thread = None
    handle = None
    try:
        kernel = recovery.kernel_api()
        pipe.send({'ready': owned.self_identity(kernel)})
        if not pipe.poll(4):
            raise RuntimeError('delivery_object_transfer_timeout')
        transfer = pipe.recv()
        if set(transfer) != {'handle', 'nonce'} or transfer['nonce'] != nonce:
            raise RuntimeError('delivery_object_transfer_invalid')
        handle = transfer['handle']
        bound = owned.BoundProcess(identity, kernel=kernel)
        thread = recovery.BoundThread(bound, identity['main_tid'], baseline[str(identity['main_tid'])]['thread_created'])
        native = api()

        def publish(record):
            pipe.send({'fact': record})
            if not pipe.poll(3) or pipe.recv() != {'recorded': nonce}:
                raise RuntimeError('delivery_fact_ack_missing')

        def fault(name):
            if name != checkpoint:
                return
            pipe.send({'fault_checkpoint': name})
            if not pipe.poll(3) or pipe.recv() != {'fault_ready': nonce}:
                raise RuntimeError('delivery_fault_ack_missing')
            if mode == 'crash':
                os._exit(17)
            if mode == 'watchdog':
                pipe.send({'awaiting_external_termination': True})
                until = time.monotonic() + 4
                while mp.parent_process().is_alive() and time.monotonic() < until:
                    time.sleep(.025)
                os._exit(45)

        wait_serial = 0
        def next_event(timeout_ms=100):
            nonlocal wait_serial
            if storage is not None:
                c.memset(c.addressof(storage), 0, c.sizeof(storage))
                wait_serial += 1
                pipe.send({'wait_ready': wait_serial})
                if not pipe.poll(3) or pipe.recv() != {'wait_recorded': nonce, 'serial': wait_serial}:
                    raise RuntimeError('delivery_shared_wait_ack_missing')
            def before_decode(event):
                if event.state == NATIVE_SINGLE_STEP:
                    fault('hit_before_decode')
            return wait_event(bound, handle, native, timeout_ms, storage=storage, before_decode=before_decode)

        fault('before_attach')
        with mutation_handle(bound) as process:
            status_checked(native.NtDebugActiveProcess(process, handle), 'delivery_attach')
        fault('after_attach')
        event = next_event(500)
        validate_event(event, identity, hit=False)
        fault('initial_delivered')
        publish({'event': event})
        original = thread.context()[2]
        if original != baseline[str(identity['main_tid'])]['debug_registers']:
            raise RuntimeError('delivery_original_context_changed')
        intent = dict(original=original, armed=recovery.armed_registers(original, identity['address']))
        publish({'arm_intent': intent})
        fault('intent_recorded')
        thread.set_registers(intent['armed'])
        fault('armed_unrecorded')
        publish({'armed': True})
        continue_event(bound, handle, native, event)
        publish({'initial_continued': True})
        until = time.monotonic() + 4
        while time.monotonic() < until:
            event = next_event()
            if event is None:
                continue
            if event['state'] in (6, 7, 8):
                validate_event(event, identity, hit=True)
                break
            if event['state'] not in (2, 9, 10):
                raise RuntimeError('delivery_unexpected_intermediate_event')
            continue_event(bound, handle, native, event)
        else:
            raise RuntimeError('delivery_hardware_hit_timeout')
        fault('hit_unrecorded')
        publish({'event': event})
        fault('hit_recorded')
        thread.set_registers(recovery.safe_restore(original, intent['armed'], thread.context()[2]))
        fault('restored_unrecorded')
        publish({'stop_ready': thread.context()[2]})
        fault('stop_intent')
        continue_event(bound, handle, native, event)
        fault('continued_unrecorded')
        publish({'terminal_continued': True})
        detach(bound, handle, native)
        fault('detached_unrecorded')
        publish({'done': True})
    except BaseException as exc:
        pipe.send({'error': owned.error_code(exc)})
    finally:
        for port in (thread, bound):
            if port is not None:
                port.close()
        if handle:
            recovery.checked(kernel.CloseHandle(handle), 'delivery_child_object_close')
        pipe.close()


def qualified(result):
    return bool(result.get('state') == 'complete' and result.get('object_created_before_owner') is True and
        result.get('owner_exit_confirmed') is True and result.get('checkpoint_reached') is True and
        recovery.retention.expected_exit(result.get('mode'), result.get('owner_exitcode'), result.get('owner_terminated')) and
        result.get('stable_two_seconds') is True and result.get('debugger_after') is False and
        result.get('initial_threads') and result['initial_threads'] == result.get('final_threads') and
        result.get('handles_closed') is True and not result.get('cleanup_errors') and
        not any('error' in record for record in result.get('records', [])) and
        result.get('child_cleanup', [{}])[-1].get('exitcode') == 0 and
        result.get('child_cleanup', [{}])[-1].get('terminated') is False and
        (result.get('fixture') == 'zero_baseline' or (
            result.get('fixture_cleanup', {}).get('original_state_preserved') is True and
            result.get('fixture_cleanup', {}).get('after_fixture') == result.get('before_fixture') and
            result.get('fixture_source_cleanup') == [{'fixture_source_closed': True}])))


def run_case(context, mode, checkpoint, fixture_name='zero_baseline', *, shared_delivery=False):
    if (mode not in recovery.retention.MODES or checkpoint not in CHECKPOINTS + TERMINAL_CHECKPOINTS or
            fixture_name not in ('zero_baseline', 'existing_both') or type(shared_delivery) is not bool):
        raise RuntimeError('delivery_case_invalid')
    fixture = preservation.Fixture(fixture_name) if fixture_name != 'zero_baseline' else None
    counter, stop = context.Value('L', 0, lock=False), context.Value('b', False, lock=False)
    source_read, source_write = context.Pipe()
    read, write = context.Pipe()
    source = recovery.retention.source if fixture is None else fixture.source
    target = context.Process(target=source, args=(source_write, counter, stop))
    worker = None
    bound = worker_bound = thread = handle = journal_file = None
    nonce = uuid.uuid4().hex
    storage = context.RawArray(c.c_uint64, 23) if shared_delivery else None
    result = dict(mode=mode, checkpoint=checkpoint, fixture=fixture_name, state='started',
                  owner_terminated=False, records=[], cleanup_errors=[], live_eligible=False,
                  shared_delivery=shared_delivery)
    try:
        target.start(); source_write.close()
        identity = owned.receive(source_read, 5, 'delivery_source_timeout')
        bound = owned.BoundProcess(identity, target, recovery.kernel_api())
        initial = owned.process_state(bound)
        owned.check_baseline(initial)
        result['before_fixture'] = initial
        if fixture is not None:
            initial = fixture.prepare(bound, identity, initial, counter)
        result['initial_threads'] = initial
        original = initial[str(identity['main_tid'])]['debug_registers']
        recovery.encode_debug_context(original)
        thread = recovery.BoundThread(bound, identity['main_tid'], initial[str(identity['main_tid'])]['thread_created'])
        journal = Journal(identity, original, initial[str(identity['main_tid'])])
        destination = ROOT / '.local/live-acceptance' / ('debug-delivery-journal-' + nonce + '.jsonl')
        destination.parent.mkdir(parents=True, exist_ok=True)
        journal_file = destination.open('x', encoding='utf-8')
        result['journal_path'] = str(destination)
        native = api()
        handle = create_object(native)
        result['object_created_before_owner'] = True
        worker = context.Process(target=owner, args=(write, identity, initial, mode, checkpoint, nonce, storage))
        worker.start(); write.close()
        until = time.monotonic() + 13
        while time.monotonic() < until:
            state, record = recovery.poll_record(read)
            if state == 'closed':
                break
            if state == 'record':
                result['records'].append(record)
                if 'ready' in record:
                    if worker_bound is not None:
                        raise RuntimeError('delivery_duplicate_owner_ready')
                    worker_bound = owned.BoundProcess(record['ready'], worker, recovery.kernel_api())
                    remote = duplicate_to_owner(worker_bound, handle)
                    read.send(dict(handle=remote, nonce=nonce))
                elif 'fact' in record:
                    journal.accept(record['fact'])
                    journal_file.write(json.dumps(record['fact']) + '\n')
                    journal_file.flush(); os.fsync(journal_file.fileno())
                    read.send({'recorded': nonce})
                elif 'wait_ready' in record:
                    if storage is None or set(record) != {'wait_ready'}:
                        raise RuntimeError('delivery_shared_wait_not_enabled')
                    journal.begin_shared_wait(record['wait_ready'], storage)
                    journal_file.write(json.dumps(record) + '\n')
                    journal_file.flush(); os.fsync(journal_file.fileno())
                    read.send({'wait_recorded': nonce, 'serial': journal.wait_serial})
                elif 'fault_checkpoint' in record:
                    if record != {'fault_checkpoint': checkpoint} or result.get('checkpoint_reached'):
                        raise RuntimeError('delivery_wrong_fault_checkpoint')
                    result['checkpoint_reached'] = True
                    read.send({'fault_ready': nonce})
                elif record == {'awaiting_external_termination': True}:
                    if mode != 'watchdog' or not result.get('checkpoint_reached'):
                        raise RuntimeError('delivery_unexpected_termination')
                    worker.terminate(); result['owner_terminated'] = True
                elif 'error' in record:
                    raise RuntimeError('delivery_owner_failed_' + record['error'])
                else:
                    raise RuntimeError('delivery_unknown_record')
            if not worker.is_alive():
                break
        worker.join(1)
        if worker.is_alive():
            raise RuntimeError('delivery_owner_exit_timeout')
        result['records'].extend(owned.drain(read))
        result.update(owner_exit_confirmed=True, owner_exitcode=worker.exitcode)
        if not recovery.retention.expected_exit(mode, worker.exitcode, result['owner_terminated']):
            raise RuntimeError('delivery_wrong_owner_exit')
        if mode == 'normal':
            if not journal.done:
                raise RuntimeError('delivery_normal_completion_missing')
        else:
            result['recovery'] = {}
            recover(bound, thread, handle, journal, native, result['recovery'], storage=storage)
        result['debugger_after'] = recovery.retention.debugger_present(bound)
        recovery.checked(bound.kernel.CloseHandle(handle), 'delivery_guardian_object_close'); handle = None
        result['object_handle_closed'] = True
        result['stable_two_seconds'] = owned.heartbeat(counter, bound)
        result['final_threads'] = owned.process_state(bound)
        if initial != result['final_threads'] or not result['stable_two_seconds']:
            raise RuntimeError('delivery_original_state_not_restored')
        if fixture is not None:
            result['fixture_cleanup'] = fixture.finish(result['final_threads'])
        result['state'] = 'complete'
    except BaseException as exc:
        result.update(state='failed', error=owned.error_code(exc))
    finally:
        if worker is not None and worker.pid and worker.is_alive():
            result['worker_cleanup'] = owned.terminate_owned(worker)
        if handle is not None:
            # A missing event does not permit guessing a continuation. This is
            # a disposable owned child; terminate it before releasing the object.
            result['failed_target_cleanup'] = owned.terminate_owned(target)
            if bound.kernel.CloseHandle(handle):
                result['object_handle_closed'] = True
            else:
                result['cleanup_errors'].append('delivery_guardian_object_close_failed')
        stop.value = True
        if target.pid:
            target.join(.5)
        children = [item for item in (worker, target) if item is not None]
        result['child_cleanup'] = [owned.terminate_owned(item) for item in children]
        result['all_children_exited'] = all(item['dead'] for item in result['child_cleanup'])
        if fixture is not None:
            result['fixture_source_cleanup'] = owned.drain(source_read)
            result['fixture_observation'] = fixture.evidence
        for port in (journal_file, fixture, thread, worker_bound, bound):
            if port is not None:
                try:
                    port.close()
                except BaseException as exc:
                    result['cleanup_errors'].append(owned.error_code(exc))
        for pipe in (source_read, source_write, read, write):
            pipe.close()
        for process in children:
            if process.pid and not process.is_alive():
                process.close()
        result['handles_closed'] = (result['all_children_exited'] and not result['cleanup_errors'] and
                                   result.get('object_handle_closed') is True)
    result['passed'] = qualified(result)
    return result


def fingerprints():
    hashes = preservation.fingerprints()
    hashes[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return hashes


def schedule(terminal=False):
    if terminal:
        yield 'normal', 'continued_unrecorded', 'zero_baseline'
        for fixture in ('zero_baseline', 'existing_both'):
            for checkpoint in TERMINAL_CHECKPOINTS:
                for mode in ('crash', 'watchdog'):
                    yield mode, checkpoint, fixture
        return
    # Lost publication is tested last; its failure stays a failed qualification.
    yield 'normal', 'hit_recorded', 'zero_baseline'
    for fixture in ('zero_baseline', 'existing_both'):
        for checkpoint in CHECKPOINTS[:7]:
            for mode in ('crash', 'watchdog'):
                yield mode, checkpoint, fixture
    for fixture in ('zero_baseline', 'existing_both'):
        for checkpoint in CHECKPOINTS[7:]:
            for mode in ('crash', 'watchdog'):
                yield mode, checkpoint, fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--shared-delivery', action='store_true')
    parser.add_argument('--terminal-boundaries', action='store_true')
    args = parser.parse_args(argv)
    if args.shared_delivery and not args.run_synthetic:
        parser.error('--shared-delivery requires --run-synthetic')
    if args.terminal_boundaries and not (args.run_synthetic and args.shared_delivery):
        parser.error('--terminal-boundaries requires --run-synthetic --shared-delivery')
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', live_eligible=False)))
        return 0
    hashes = fingerprints()
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8 or any(
            hashes.get(name) != digest for name, digest in recovery.retention.SYSTEM_SHA.items()):
        raise RuntimeError('delivery_platform_or_fingerprint_mismatch')
    cases = []
    for mode, checkpoint, fixture in schedule(args.terminal_boundaries):
        case = run_case(mp.get_context('spawn'), mode, checkpoint, fixture, shared_delivery=args.shared_delivery)
        cases.append(case)
        print(json.dumps({key: case.get(key) for key in (
            'checkpoint', 'fixture', 'mode', 'passed', 'error', 'recovery', 'all_children_exited')}), flush=True)
        if not case['passed'] and not (checkpoint in CHECKPOINTS[7:] and
                case.get('error') == 'delivery_outstanding_event_not_replayable' and case.get('handles_closed')):
            break
    report = dict(kind='owned_native_debug_delivery_boundaries', cases=cases, fingerprints=hashes,
                  source_unchanged=hashes == fingerprints(), os=platform.version(),
                  dbgeng_owner_failure_recovered=False, live_eligible=False, shared_delivery=args.shared_delivery,
                  terminal_boundaries=args.terminal_boundaries)
    report['passed'] = (len(cases) == len(list(schedule(args.terminal_boundaries))) and report['source_unchanged'] and
                        all(item['passed'] for item in cases))
    path = ROOT / '.local/live-acceptance' / ('debug-object-delivery-' + uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(path), passed=report['passed'], live_eligible=False)))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
