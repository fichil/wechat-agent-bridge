"""Synthetic takeover contracts; never loads a debugger or native DLL."""
from contextlib import redirect_stdout, redirect_stderr
import ctypes as c
import io
import struct
import unittest
from unittest.mock import Mock, patch

from scripts import probe_debug_object_recovery as probe


class DebugObjectRecoveryTests(unittest.TestCase):
    def test_write_representation_preserves_all_configurable_fields(self):
        registers = [16, 24, 32, 40, 0xffff07f0, 0x900404]
        self.assertEqual(probe.encode_debug_context(registers), [16, 24, 32, 40, 0, 0x900004])
        self.assertEqual(registers[4], 0xffff07f0)
        for dr6 in (1, 2, 0x2000, 0x4000, 0xffff0ff0, 0xffff07f1, 0xffffe7f0):
            with self.assertRaisesRegex(RuntimeError, 'not_representable'):
                probe.encode_debug_context(registers[:4] + [dr6, registers[5]])

    def test_encoded_context_requires_exact_native_readback(self):
        registers = [16, 24, 32, 40, 0xffff07f0, 0x900404]
        encoded = probe.encode_debug_context(registers)
        storage = c.create_string_buffer(1247)
        pointer = (c.addressof(storage) + 15) & ~15
        thread = object.__new__(probe.BoundThread)
        thread.handle, thread.kernel, thread.check = 7, Mock(), Mock()
        thread.kernel.SetThreadContext.return_value = 1
        thread.context = Mock(side_effect=[(storage, pointer, registers), (storage, pointer, encoded)])
        thread.set_registers(registers)
        self.assertEqual(list(struct.unpack('<6Q', c.string_at(pointer + 72, 48))), encoded)
        self.assertEqual(thread.last_register_write['observed'], encoded)
        thread.context = Mock(side_effect=[(storage, pointer, registers), (storage, pointer, encoded[:3] + [999, 0, encoded[5]])])
        with self.assertRaisesRegex(RuntimeError, 'readback_mismatch'):
            thread.set_registers(registers)

    def test_unrepresentable_status_rejected_before_context_read_or_write(self):
        thread = object.__new__(probe.BoundThread)
        thread.kernel, thread.context = Mock(), Mock()
        with self.assertRaisesRegex(RuntimeError, 'not_representable'):
            thread.set_registers([0, 0, 0, 0, 1, 0])
        thread.context.assert_not_called()
        thread.kernel.SetThreadContext.assert_not_called()

    def test_pipe_close_is_distinct_from_process_exit(self):
        for error in (EOFError, BrokenPipeError):
            pipe = Mock()
            pipe.poll.side_effect = error()
            self.assertEqual(probe.poll_record(pipe), ('closed', None))
            pipe.recv.assert_not_called()
        pipe = Mock()
        pipe.poll.return_value = False
        self.assertEqual(probe.poll_record(pipe), ('idle', None))
        pipe.poll.return_value = True
        pipe.recv.return_value = {'detached': True}
        self.assertEqual(probe.poll_record(pipe), ('record', {'detached': True}))

    def test_default_and_short_or_external_cli_rejected_before_native(self):
        with patch.object(probe, 'fingerprints') as hashes, patch.object(probe, 'run_case') as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for args in (['--run'], ['--pid', '123'], ['--stage', 'attached']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(args)
        hashes.assert_not_called(); run.assert_not_called()

    def test_arm_preserves_other_slots_and_rejects_in_use_slot_zero(self):
        original = [0, 123, 456, 789, 0, (5 << 20) | 4]
        armed = probe.armed_registers(original, 987)
        self.assertEqual(armed, [987, 123, 456, 789, 0, (5 << 20) | 5])
        self.assertEqual(original[0], 0)
        for invalid in ([0] * 5, [0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 2],
                        [0, 0, 0, 0, 0, -1], [0, 0, 0, 0, 0, True]):
            with self.assertRaises(RuntimeError):
                probe.armed_registers(invalid, 987)

    def test_restore_uses_original_not_all_zero(self):
        original = [777, 123, 0, 0, 0, 4]
        armed = probe.armed_registers(original, 987)
        self.assertEqual(probe.safe_restore(original, armed, armed[:4] + [0xffff0ff1, armed[5]]), original)

    def test_other_breakpoint_trap_or_changed_register_is_not_owned(self):
        original = [0] * 6
        armed = probe.armed_registers(original, 987)
        for dr6 in (0, 1, 2, 3, 0x2001, 0x4001, 0x8001, 0xffff07f1, 0xfffe0ff1):
            with self.assertRaisesRegex(RuntimeError, 'unowned_debug_exception'):
                probe.safe_restore(original, armed, armed[:4] + [dr6, armed[5]])
        for index in (0, 1, 2, 3, 5):
            current = armed[:4] + [1, armed[5]]
            current[index] += 1
            with self.assertRaisesRegex(RuntimeError, 'ownership_changed'):
                probe.safe_restore(original, armed, current)

    def test_only_dr7_architectural_reserved_one_may_change_at_hit(self):
        original = [0] * 6
        armed = probe.armed_registers(original, 987)
        self.assertEqual(probe.safe_restore(original, armed, [987, 0, 0, 0, 0xffff0ff1, 0x401]), original)
        for bit in (1, 2, 8, 9, 11, 13, 16, 18, 20, 32):
            with self.assertRaisesRegex(RuntimeError, 'ownership_changed'):
                probe.safe_restore(original, armed, [987, 0, 0, 0, 1, 0x401 | (1 << bit)])

    def test_pending_event_requires_bound_target_tid_address_and_intent(self):
        identity = dict(pid=8, main_tid=9, address=987)
        baseline = {'9': {'debug_registers': [0] * 6}}
        intent = dict(original=[0] * 6, armed=probe.armed_registers([0] * 6, 987))
        pending = dict(pid=8, tid=9, code=probe.SINGLE_STEP, address=987, stage='breakpoint')
        self.assertEqual(probe.validate_hit({'pending': pending}, identity, baseline, intent), pending)
        for key, value in (('pid', 7), ('tid', 10), ('code', probe.BREAKPOINT),
                           ('address', 988), ('stage', 'other')):
            with self.assertRaises(RuntimeError):
                probe.validate_hit({'pending': dict(pending, **{key: value})}, identity, baseline, intent)
        with self.assertRaises(RuntimeError):
            probe.validate_hit({'pending': pending}, identity, baseline, None)
        with self.assertRaises(RuntimeError):
            probe.validate_hit({'pending': dict(pending, command='resume_all')}, identity, baseline, intent)

    def test_reused_thread_is_closed_without_context_write(self):
        bound = Mock(identity={'pid': 8})
        bound.kernel.OpenThread.return_value = 55
        bound.kernel.GetProcessIdOfThread.return_value = 9
        bound.kernel.CloseHandle.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'thread_identity_changed'):
            probe.BoundThread(bound, 9, 10)
        bound.kernel.GetThreadContext.assert_not_called()
        bound.kernel.SetThreadContext.assert_not_called()
        bound.kernel.CloseHandle.assert_called_once_with(55)

    def test_recovery_must_restore_before_continue_and_detach(self):
        order = []
        bound = Mock(identity={'pid': 8, 'created': 17}, handle=1)
        bound.kernel.OpenProcess.return_value = 22
        bound.kernel.CloseHandle.return_value = 1
        thread = Mock()
        thread.context.return_value = (None, 0, [987, 0, 0, 0, 0xffff0ff1, 1])
        thread.set_registers.side_effect = lambda _: order.append('restore')
        api = Mock()
        api.NtDebugContinue.side_effect = lambda *_: order.append('continue') or 0
        api.NtRemoveProcessDebug.side_effect = lambda *_: order.append('detach') or 0
        event = dict(pid=8, tid=9, stage='breakpoint')
        intent = dict(original=[0] * 6, armed=[987, 0, 0, 0, 0, 1])
        with patch.object(probe.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'native_recovery_api', return_value=api), \
                patch.object(probe.owned, 'creation_time', return_value=17):
            receipt = probe.recover(bound, thread, 88, event, intent)
        self.assertEqual(order, ['restore', 'continue', 'detach'])
        self.assertTrue(all(receipt.values()))
        bound.kernel.ResumeThread.assert_not_called()
        bound.kernel.SuspendThread.assert_not_called()

    def test_changed_context_prevents_continue_and_detach(self):
        bound, thread, api = Mock(), Mock(), Mock()
        thread.context.return_value = (None, 0, [988, 0, 0, 0, 1, 1])
        with patch.object(probe.retention, 'debugger_present', return_value=True), \
                patch.object(probe, 'native_recovery_api', return_value=api), \
                self.assertRaisesRegex(RuntimeError, 'ownership_changed'):
            probe.recover(bound, thread, 88, dict(stage='breakpoint'),
                          dict(original=[0] * 6, armed=[987, 0, 0, 0, 0, 1]))
        thread.set_registers.assert_not_called()
        api.NtDebugContinue.assert_not_called()
        api.NtRemoveProcessDebug.assert_not_called()


if __name__ == '__main__':
    unittest.main()
