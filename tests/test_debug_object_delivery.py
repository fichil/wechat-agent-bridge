"""Delivery-gap contracts; no native DLL, client or paid API is used."""
from contextlib import redirect_stdout, redirect_stderr
import ctypes as c
import io
import unittest
from unittest.mock import Mock, patch

from scripts import probe_debug_object_delivery as probe


def identity():
    return dict(pid=101, main_tid=102, address=4096)


def initial():
    return dict(state=3, pid=101, tid=102)


def hit():
    return dict(state=8, pid=101, tid=102, code=probe.recovery.SINGLE_STEP,
                address=4096, first_chance=1)


def intent():
    return dict(original=[0] * 6, armed=[4096, 0, 0, 0, 0, 1])


def journal_at_hit(published=True):
    journal = probe.Journal(identity(), [0] * 6)
    for fact in ({'event': initial()}, {'arm_intent': intent()}, {'armed': True},
                 {'initial_continued': True}):
        journal.accept(fact)
    if published:
        journal.accept({'event': hit()})
    return journal


class DeliveryBoundaryTests(unittest.TestCase):
    def test_default_and_external_target_options_never_initialize_native(self):
        with patch.object(probe, 'fingerprints') as hashes, patch.object(probe, 'run_case') as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for args in (['--run'], ['--pid', '1'], ['--checkpoint', 'after_attach'], ['--shared-delivery'],
                         ['--terminal-boundaries'], ['--run-synthetic', '--terminal-boundaries']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(args)
        hashes.assert_not_called(); run.assert_not_called()

    def test_x64_native_layout_and_owned_handle_fields(self):
        probe.layout_checked()
        self.assertEqual(c.sizeof(probe.NativeEvent), 184)
        event = probe.NativeEvent()
        event.state = 3
        for i, value in enumerate((11, 22, 333, 44, 555)):
            event.data.words[i] = value
        self.assertEqual(probe.native_event_handles(event), [11, 22, 44])
        event.state = 2
        self.assertEqual(probe.native_event_handles(event), [11])
        event.state = 9
        self.assertEqual(probe.native_event_handles(event), [11])
        event.state = 8
        self.assertEqual(probe.native_event_handles(event), [])

    def test_object_created_with_kill_on_close_disabled(self):
        native = Mock()
        def create(pointer, rights, attributes, flags):
            self.assertEqual((rights, attributes, flags), (0x1f000f, None, 0))
            c.cast(pointer, c.POINTER(c.c_void_p))[0] = 555
            return 0
        native.NtCreateDebugObject.side_effect = create
        self.assertEqual(probe.create_object(native), 555)

    def test_native_wait_has_hard_relative_timeout_and_ignores_timeout_buffer(self):
        bound = Mock(identity=identity())
        native = Mock()
        def wait(handle, alertable, timeout, event):
            self.assertEqual(handle, 777)
            self.assertFalse(alertable)
            self.assertEqual(c.cast(timeout, c.POINTER(c.c_int64))[0], -2000000)
            return probe.TIMEOUT
        native.NtWaitForDebugEvent.side_effect = wait
        self.assertIsNone(probe.wait_event(bound, 777, native, 200))
        bound.kernel.CloseHandle.assert_not_called()
        native.NtWaitForDebugEvent.reset_mock()
        for invalid in (-1, 501, True, 1.1):
            with self.assertRaisesRegex(RuntimeError, 'not_bounded'):
                probe.wait_event(bound, 777, native, invalid)
        native.NtWaitForDebugEvent.assert_not_called()

    def test_native_wait_mismatched_target_closes_only_returned_event_handles(self):
        bound = Mock(identity=identity())
        bound.kernel.CloseHandle.return_value = 1
        native = Mock()
        def wait(_handle, _alertable, _timeout, event):
            item = c.cast(event, c.POINTER(probe.NativeEvent))[0]
            item.state, item.client.pid, item.client.tid = 9, 999, 102
            item.data.words[0] = 123
            return 0
        native.NtWaitForDebugEvent.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, 'identity_invalid'):
            probe.wait_event(bound, 777, native)
        bound.kernel.CloseHandle.assert_called_once_with(123)

    def test_reused_process_cannot_receive_object_or_attach(self):
        bound = Mock(identity=dict(pid=101, created=111))
        bound.kernel.OpenProcess.return_value = 55
        bound.kernel.CloseHandle.return_value = 1
        with patch.object(probe.owned, 'creation_time', return_value=112), \
                self.assertRaisesRegex(RuntimeError, 'identity_changed'):
            with probe.mutation_handle(bound):
                self.fail('must not grant mutation handle')
        bound.kernel.CloseHandle.assert_called_once_with(55)

    def test_journal_rejects_fault_control_and_out_of_order_mutations(self):
        for record in ({'fault_checkpoint': 'hit_unrecorded'}, {'armed': True},
                       {'initial_continued': True}, {'done': True}, {'arm_intent': intent()},
                       {'event': hit()}, {'event': initial(), 'done': True}):
            with self.assertRaises(RuntimeError):
                probe.Journal(identity(), [0] * 6).accept(record)

    def test_journal_preserves_missing_event_as_missing(self):
        journal = journal_at_hit(False)
        self.assertIsNone(journal.pending)
        self.assertFalse(journal.hit_seen)
        journal.accept({'event': hit()})
        self.assertEqual(journal.pending, hit())
        with self.assertRaisesRegex(RuntimeError, 'duplicate_pending'):
            journal.accept({'event': hit()})
        journal.accept({'done': True})
        self.assertTrue(journal.done)

    def test_published_hit_requires_exact_identity_address_and_first_chance(self):
        for key, value in (('pid', 999), ('tid', 103), ('code', probe.recovery.BREAKPOINT),
                           ('address', 4097), ('first_chance', 0), ('state', 6), ('state', True)):
            with self.assertRaisesRegex(RuntimeError, 'event_invalid'):
                probe.validate_event(dict(hit(), **{key: value}), identity(), hit=True)
        with self.assertRaises(RuntimeError):
            probe.validate_event(dict(hit(), command='continue'), identity(), hit=True)

    def test_unacknowledged_arm_and_restore_compare_actual_context(self):
        thread = Mock()
        for observed in ([0] * 6, intent()['armed']):
            thread.context.return_value = (None, 0, observed)
            result = probe.restore_before_continue(thread, intent(), initial())
            self.assertEqual(result, 'original_already_present' if observed == [0] * 6 else 'original_restored')
        thread.set_registers.assert_called_once_with([0] * 6)
        thread.reset_mock()
        thread.context.return_value = (None, 0, [0] * 6)
        self.assertEqual(probe.restore_before_continue(thread, intent(), hit()), 'original_already_present')
        thread.set_registers.assert_not_called()

    def test_unowned_context_blocks_restore(self):
        thread = Mock()
        for observed in ([4097, 0, 0, 0, 0, 1], [4096, 0, 0, 0, 0xffff0ff3, 0x401]):
            thread.context.return_value = (None, 0, observed)
            with self.assertRaises(RuntimeError):
                probe.restore_before_continue(thread, intent(), hit())
        thread.set_registers.assert_not_called()

    def test_unpublished_event_timeout_cannot_trigger_register_write_or_continue(self):
        bound, thread, native, receipt = Mock(), Mock(), Mock(), {}
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'wait_event', return_value=None) as wait, \
                patch.object(probe, 'detach') as detach, \
                self.assertRaisesRegex(RuntimeError, 'not_replayable'):
            probe.recover(bound, thread, 88, journal_at_hit(False), native, receipt)
        wait.assert_called_once_with(bound, 88, native, 200)
        self.assertEqual(receipt['native_replay'], None)
        thread.context.assert_not_called(); thread.set_registers.assert_not_called()
        native.NtDebugContinue.assert_not_called(); detach.assert_not_called()

    def test_actual_replayed_event_validated_before_restore_and_continue(self):
        bound, thread, native = Mock(identity=identity()), Mock(), Mock()
        thread.context.return_value = (None, 0, [4096, 0, 0, 0, 0xffff0ff1, 0x401])
        order = []
        thread.set_registers.side_effect = lambda _: order.append('restore')
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'wait_event', return_value=hit()), \
                patch.object(probe, 'continue_event', side_effect=lambda *_: order.append('continue')), \
                patch.object(probe, 'detach', side_effect=lambda *_: order.append('detach')):
            probe.recover(bound, thread, 88, journal_at_hit(False), native, {})
        self.assertEqual(order, ['restore', 'continue', 'detach'])
        bound.kernel.ResumeThread.assert_not_called()

    def test_before_attach_and_before_intent_never_write_registers(self):
        bound, thread, native = Mock(), Mock(), Mock()
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=False), \
                patch.object(probe, 'detach') as detach:
            probe.recover(bound, thread, 88, probe.Journal(identity(), [0] * 6), native, {})
        detach.assert_not_called()
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'detach') as detach:
            probe.recover(bound, thread, 88, probe.Journal(identity(), [0] * 6), native, {})
        detach.assert_called_once()
        thread.context.assert_not_called(); thread.set_registers.assert_not_called()

    def test_lost_debug_association_after_arm_is_not_success(self):
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=False), \
                self.assertRaisesRegex(RuntimeError, 'association_lost'):
            probe.recover(Mock(), Mock(), 88, journal_at_hit(True), Mock(), {})

    def test_schedule_preserves_unpublished_event_failures_and_seeded_states(self):
        cases = list(probe.schedule())
        self.assertEqual(len(cases), 37)
        self.assertEqual(len(set(cases)), 37)
        self.assertEqual(cases[-2:], [('crash', 'hit_before_decode', 'existing_both'),
                                     ('watchdog', 'hit_before_decode', 'existing_both')])
        self.assertEqual(sum(fixture == 'existing_both' for _, _, fixture in cases), 18)
        for state in ('failed', 'complete'):
            self.assertFalse(probe.qualified(dict(state=state, handles_closed=True)))

    def test_shared_wait_requires_empty_output_fresh_epoch_and_no_pending_event(self):
        storage = (c.c_uint64 * 23)()
        journal = probe.Journal(identity(), [0] * 6)
        journal.begin_shared_wait(1, storage)
        for serial in (1, 3, True, 129):
            with self.assertRaises(RuntimeError):
                journal.begin_shared_wait(serial, storage)
        storage[5] = 1
        with self.assertRaises(RuntimeError):
            journal.begin_shared_wait(2, storage)
        storage[5] = 0
        journal.accept({'event': initial()})
        with self.assertRaises(RuntimeError):
            journal.begin_shared_wait(2, storage)

    def test_shared_output_is_the_native_syscall_destination(self):
        storage = (c.c_uint64 * 23)()
        bound, native = Mock(identity=identity()), Mock()
        def wait(_handle, _alertable, _timeout, output):
            self.assertEqual(c.cast(output, c.c_void_p).value, c.addressof(storage))
            event = c.cast(output, c.POINTER(probe.NativeEvent))[0]
            event.state, event.client.pid, event.client.tid = 8, 101, 102
            event.data.exception.record.code = probe.recovery.SINGLE_STEP
            event.data.exception.record.address = 4096
            event.data.exception.first_chance = 1
            return 0
        native.NtWaitForDebugEvent.side_effect = wait
        self.assertEqual(probe.wait_event(bound, 777, native, storage=storage), hit())
        self.assertEqual(probe.event_metadata(probe.NativeEvent.from_buffer(storage)), hit())
        bound.kernel.CloseHandle.assert_not_called()

    def test_shared_replay_rejects_partial_stale_wrong_or_unbound_metadata(self):
        storage = (c.c_uint64 * 23)()
        journal = journal_at_hit(False)
        with self.assertRaises(RuntimeError):
            probe.shared_event_after_owner_exit(storage, journal)
        journal.begin_shared_wait(1, storage)
        self.assertIsNone(probe.shared_event_after_owner_exit(storage, journal))
        event = probe.NativeEvent.from_buffer(storage)
        event.state = 8
        with self.assertRaises(RuntimeError):
            probe.shared_event_after_owner_exit(storage, journal)
        event.client.pid, event.client.tid = 101, 102
        event.data.exception.record.code = probe.recovery.SINGLE_STEP
        event.data.exception.record.address = 4096
        event.data.exception.first_chance = 1
        self.assertEqual(probe.shared_event_after_owner_exit(storage, journal), hit())
        event.client.tid = 103
        with self.assertRaises(RuntimeError):
            probe.shared_event_after_owner_exit(storage, journal)

    def test_shared_recovered_event_needs_no_replay_or_fault_label(self):
        bound, thread, native, receipt = Mock(identity=identity()), Mock(), Mock(), {}
        journal = journal_at_hit(False)
        storage = (c.c_uint64 * 23)()
        journal.begin_shared_wait(1, storage)
        thread.context.return_value = (None, 0, [4096, 0, 0, 0, 0xffff0ff1, 0x401])
        with patch.object(probe.recovery.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'shared_event_after_owner_exit', return_value=hit()), \
                patch.object(probe, 'wait_event') as wait, \
                patch.object(probe, 'continue_event') as continuation, patch.object(probe, 'detach'):
            probe.recover(bound, thread, 88, journal, native, receipt, storage=storage)
        wait.assert_not_called()
        thread.set_registers.assert_called_once_with([0] * 6)
        continuation.assert_called_once_with(bound, 88, native, hit())
        self.assertEqual(receipt['shared_event'], hit())

    def terminal_journal(self):
        journal = journal_at_hit()
        journal.thread_baseline = dict(thread_created=50, prior_suspend_count=0, debug_registers=[0] * 6)
        journal.accept({'stop_ready': [0] * 6})
        return journal

    def test_stop_intent_requires_verified_hit_original_and_no_later_wait(self):
        journal = journal_at_hit()
        with self.assertRaisesRegex(RuntimeError, 'stop_intent_invalid'):
            journal.accept({'stop_ready': [0] * 6})
        journal = self.terminal_journal()
        for fact in ({'stop_ready': [0] * 6}, {'armed': True}, {'arm_intent': intent()}):
            with self.assertRaises(RuntimeError):
                journal.accept(fact)
        journal.accept({'terminal_continued': True})
        self.assertIsNone(journal.pending)
        with self.assertRaises(RuntimeError):
            journal.begin_shared_wait(1, (c.c_uint64 * 23)())
        with self.assertRaises(RuntimeError):
            journal.accept({'terminal_continued': True})

    def test_unacknowledged_continue_is_never_retried(self):
        journal = self.terminal_journal()
        observed = dict(thread_created=50, prior_suspend_count=0, debug_registers=[0] * 6)
        for present in (True, False):
            bound, thread, native, receipt = Mock(), Mock(), Mock(), {}
            with patch.object(probe.recovery.retention, 'debugger_present', return_value=present), \
                    patch.object(probe, 'observe_terminal_thread', return_value=observed), \
                    patch.object(probe, 'detach') as detach, patch.object(probe, 'continue_event') as continuation:
                probe.recover(bound, thread, 88, journal, native, receipt)
            self.assertFalse(receipt['continuation_acknowledged'])
            continuation.assert_not_called(); thread.set_registers.assert_not_called()
            self.assertEqual(detach.call_count, int(present))

    def test_changed_original_state_refuses_terminal_detach(self):
        journal = self.terminal_journal()
        good = dict(thread_created=50, prior_suspend_count=0, debug_registers=[0] * 6)
        for key, value in (('thread_created', 51), ('prior_suspend_count', 1),
                           ('debug_registers', [4096, 0, 0, 0, 0, 1])):
            with patch.object(probe.recovery.retention, 'debugger_present', return_value=True), \
                    patch.object(probe, 'observe_terminal_thread', return_value=dict(good, **{key: value})), \
                    patch.object(probe, 'detach') as detach, self.assertRaisesRegex(RuntimeError, 'state_changed'):
                probe.recover(Mock(), Mock(), 88, journal, Mock(), {})
            detach.assert_not_called()

    def test_observation_pause_is_balanced_without_consuming_original_pause(self):
        original, observer = Mock(tid=102, created=50), Mock(created=50, handle=77)
        observer.kernel.SuspendThread.return_value = 2
        observer.kernel.ResumeThread.return_value = 3
        observer.context.return_value = (None, 0, [0] * 6)
        with patch.object(probe.recovery, 'BoundThread', return_value=observer):
            actual = probe.observe_terminal_thread(Mock(), original)
        self.assertEqual(actual['prior_suspend_count'], 2)
        observer.kernel.ResumeThread.assert_called_once_with(77)
        observer.set_registers.assert_not_called(); observer.close.assert_called_once()

    def test_failed_observation_does_not_remove_unowned_suspension(self):
        for failed_suspend in (True, False):
            observer = Mock(created=50, handle=77)
            observer.kernel.SuspendThread.return_value = 0xffffffff if failed_suspend else 0
            observer.kernel.ResumeThread.return_value = 1
            observer.context.side_effect = RuntimeError('read_failed')
            with patch.object(probe.recovery, 'BoundThread', return_value=observer), self.assertRaises(RuntimeError):
                probe.observe_terminal_thread(Mock(), Mock(tid=102, created=50))
            self.assertEqual(observer.kernel.ResumeThread.call_count, int(not failed_suspend))
            observer.close.assert_called_once()

    def test_terminal_schedule_covers_both_sides_of_continue_and_detach(self):
        cases = list(probe.schedule(True))
        self.assertEqual(len(cases), 13)
        for stage in probe.TERMINAL_CHECKPOINTS:
            for fixture in ('zero_baseline', 'existing_both'):
                for mode in ('crash', 'watchdog'):
                    self.assertIn((mode, stage, fixture), cases)


if __name__ == '__main__':
    unittest.main()
