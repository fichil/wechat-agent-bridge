"""Own-source and synthetic record tests only; no windows or ETW are opened."""
import contextlib
import copy
import ctypes as c
import io
import struct
import unittest
from unittest.mock import MagicMock, patch

from scripts import test_win32k_message_etw as message


def schema():
    return {'id': 60, 'version': 0, 'channel': 21, 'level': 4, 'opcode': 1, 'task_id': 60,
            'keyword': '0x400000000400000',
            'fields': [{'name': name, 'in_type': kind, 'length_or_index': length,
                        'flags': 0, 'count_or_index': 1, 'top_level': True}
                       for name, kind, length in message.SCHEMA]}


class MessageSourceTests(unittest.TestCase):
    def test_hidden_window_class_abi(self):
        self.assertEqual(c.sizeof(message.WindowClass), 80)
        self.assertEqual(message.WindowClass.proc.offset, 8)

    def test_peer_requires_exact_pid_tid_and_hidden_window(self):
        expected = {'pid': 101, 'tid': 202}
        message.check_peer(expected, 101, 202, False)
        for pid, tid, visible in ((102, 202, False), (101, 203, False), (101, 202, True)):
            with self.assertRaisesRegex(RuntimeError, 'identity_or_visibility'):
                message.check_peer(expected, pid, tid, visible)

    def test_round_rejects_duplicate_and_late_synthetic_messages(self):
        state = message.RoundState()
        with self.assertRaises(RuntimeError): state.sending(1)
        state.arm(10)
        token = state.sending(1)
        self.assertTrue(state.receive(1, token))
        self.assertFalse(state.receive(1, token))
        with self.assertRaises(RuntimeError): state.sending(1)
        state.arm(11)
        self.assertFalse(state.receive(1, token))
        self.assertTrue(state.receive(1, state.sending(1)))
        with self.assertRaises(RuntimeError): state.arm(11)

    def test_probe_requires_bidirectional_ack_and_exact_counters(self):
        source = MagicMock()
        initial = [{'sent': 0, 'received': 0, 'bad': 0}] * 2
        final = [{'sent': message.COUNT, 'received': message.COUNT, 'bad': 0}] * 2
        source.stats.side_effect = [initial, final]
        self.assertTrue(message.source_probe(source)['passed'])
        self.assertEqual(source.send.call_count, 2 * message.COUNT)
        source.stats.side_effect = [initial, [final[0], {'sent': message.COUNT, 'received': 0, 'bad': 0}]]
        self.assertFalse(message.source_probe(source)['passed'])

    def test_source_only_does_not_touch_metadata_or_controller(self):
        source = MagicMock()
        source.close.return_value = {'passed': True}
        with patch.object(message, 'Source', return_value=source), \
                patch.object(message, 'fingerprints', return_value={'synthetic': 'hash'}), \
                patch.object(message, 'source_probe', return_value={'passed': True}), \
                patch.object(message, 'metadata') as metadata, patch.object(message, 'controller') as controller:
            report = message.run(False)
        self.assertTrue(report['passed'])
        self.assertFalse(report['trace_started'])
        metadata.assert_not_called(); controller.assert_not_called(); source.close.assert_called_once()

    def test_source_start_failure_still_closes_owned_resources(self):
        source = MagicMock(); source.start.side_effect = RuntimeError('child_identity_failed')
        source.close.return_value = {'passed': False}
        with patch.object(message, 'Source', return_value=source), \
                patch.object(message, 'fingerprints', return_value={'synthetic': 'hash'}):
            report = message.run(False)
        self.assertFalse(report['passed'])
        self.assertEqual(report['error'], 'child_identity_failed')
        source.close.assert_called_once()

    def test_pipe_cleanup_error_does_not_skip_other_child(self):
        source = object.__new__(message.Source)
        source.initial, source.desktop = ('synthetic', 1, 2), None
        source.children = [MagicMock(pid=101, exitcode=0), MagicMock(pid=102, exitcode=0)]
        for process in source.children: process.is_alive.return_value = False
        source.pipes = [MagicMock(), MagicMock()]
        source.pipes[0].poll.side_effect = OSError('closed pipe')
        source.pipes[1].recv.return_value = {'finished': {
            key: True for key in ('window_destroyed', 'class_unregistered', 'thread_desktop_restored', 'desktop_closed')}}
        with patch.object(message, 'input_snapshot', return_value=source.initial): result = source.close()
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['children']), 2)
        for process in source.children: process.join.assert_called_once()
        for pipe in source.pipes: pipe.close.assert_called_once()
        self.assertTrue(result['desktop_closed'])

    def test_default_entry_does_nothing_native(self):
        with patch.object(message.sys, 'argv', ['test_win32k_message_etw.py']), \
                patch.object(message, 'run') as native, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(message.main(), 0)
        native.assert_not_called()


class MessageEventTests(unittest.TestCase):
    def setUp(self):
        self.offsets = message.schema_offsets(schema())
        self.receiver = {'pid': 101, 'tid': 202, 'hwnd': 303}
        self.sender = {'pid': 404, 'tid': 505, 'hwnd': 606, 'modules': [(0x100000, 0x1000, 'own.dll')]}

    def record(self):
        record = message.Record()
        record.header.provider[:] = message.PROVIDER
        record.header.flags, record.header.pid, record.header.tid = 0x40, 404, 505
        record.header.descriptor = message.Descriptor(60, 0, 21, 4, 1, 60, 0x400000)
        payload = c.create_string_buffer(53)
        for name, key, code in (('pidReceiver', 'pid', '<I'), ('tidReceiver', 'tid', '<I'), ('hwnd', 'hwnd', '<Q')):
            struct.pack_into(code, payload, self.offsets[name], self.receiver[key])
        record.data, record.data_length = c.addressof(payload), 53
        return record, payload

    def test_metadata_uses_packed_offsets_and_int8(self):
        self.assertEqual(self.offsets['message'], 33)
        self.assertEqual(self.offsets['pidReceiver'], 45)
        self.assertEqual(self.offsets['tidReceiver'], 49)
        changed = schema(); changed['fields'][0]['in_type'] = 4
        with self.assertRaisesRegex(RuntimeError, 'schema_mismatch'): message.schema_offsets(changed)
        changed = schema(); changed['fields'][1]['length_or_index'] = 4
        with self.assertRaisesRegex(RuntimeError, 'schema_mismatch'): message.schema_offsets(changed)
        changed = schema(); changed['keyword'] = '0x2000'
        with self.assertRaisesRegex(RuntimeError, 'keyword_mismatch'): message.schema_offsets(changed)

    def test_nonown_sender_rejected_before_invalid_payload_dereference(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, False)
        record, payload = self.record(); record.header.pid = 999; record.data = 1
        with patch.object(message.c, 'string_at') as read:
            evidence.consume(c.pointer(record))
        read.assert_not_called()
        self.assertEqual(evidence.rejected, 1)
        self.assertEqual(evidence.malformed, 0)
        self.assertEqual(evidence.callbacks, 1)
        self.assertEqual(evidence.reasons, {'sender': 1})
        self.assertTrue(evidence.abort.is_set())
        self.assertTrue(all(value == 0 for value in evidence.own_sender_rejection_ownership.values()))

    def rejected_own_header(self, ownership):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, True)
        record, payload = self.record()
        record.header.pid, record.header.tid = self.receiver['pid'], self.receiver['tid']
        for name, key, code in (('pidReceiver', 'pid', '<I'), ('tidReceiver', 'tid', '<I'), ('hwnd', 'hwnd', '<Q')):
            struct.pack_into(code, payload, self.offsets[name], ownership[key])
        # This pointer would be unsafe to dereference. Rejected own headers must
        # return before even inspecting extended data, including in stack mode.
        record.extended_count = 1
        record.extended = c.cast(c.c_void_p(1), type(record.extended))
        with patch.object(message.c, 'string_at', wraps=c.string_at) as read:
            evidence.consume(c.pointer(record))
        self.assertEqual([(call.args[0] - record.data, call.args[1]) for call in read.call_args_list],
                         [(self.offsets['pidReceiver'], 4), (self.offsets['tidReceiver'], 4), (self.offsets['hwnd'], 8)])
        self.assertEqual((evidence.accepted, evidence.rejected, evidence.malformed, evidence.unknown_stack), (0, 1, 0, 0))
        self.assertEqual(evidence.stacks, [])
        self.assertEqual(evidence.reasons, {'sender': 1})
        self.assertTrue(evidence.abort.is_set())
        return evidence

    def test_known_own_header_selected_receiver_is_counted_but_rejected(self):
        evidence = self.rejected_own_header(self.receiver)
        self.assertEqual(evidence.own_sender_rejection_ownership,
                         {'selected_receiver': 1, 'opposite_receiver': 0, 'other_or_mixed': 0})

    def test_known_own_header_opposite_receiver_is_counted_but_rejected(self):
        evidence = self.rejected_own_header(self.sender)
        self.assertEqual(evidence.own_sender_rejection_ownership,
                         {'selected_receiver': 0, 'opposite_receiver': 1, 'other_or_mixed': 0})

    def test_known_own_header_mixed_ownership_cannot_pass(self):
        evidence = self.rejected_own_header({**self.receiver, 'tid': self.sender['tid']})
        self.assertEqual(evidence.own_sender_rejection_ownership,
                         {'selected_receiver': 0, 'opposite_receiver': 0, 'other_or_mixed': 1})

    def test_known_pid_wrong_tid_is_rejected_before_payload(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, True)
        record, payload = self.record()
        record.header.pid, record.header.tid, record.data = self.receiver['pid'], 999, 1
        with patch.object(message.c, 'string_at') as read:
            evidence.consume(c.pointer(record))
        read.assert_not_called()
        self.assertEqual((evidence.accepted, evidence.rejected, evidence.malformed), (0, 1, 0))
        self.assertEqual(evidence.stacks, [])
        self.assertTrue(evidence.abort.is_set())
        self.assertTrue(all(value == 0 for value in evidence.own_sender_rejection_ownership.values()))

    def test_wrong_receiver_and_32_bit_header_are_rejected(self):
        for which in ('receiver', 'header'):
            evidence = message.Evidence(self.receiver, self.sender, self.offsets, False)
            record, payload = self.record()
            if which == 'receiver': struct.pack_into('<I', payload, self.offsets['pidReceiver'], 999)
            else: record.header.flags = 0x20
            evidence.consume(c.pointer(record))
            self.assertTrue(evidence.abort.is_set())
            self.assertEqual(evidence.accepted, 0)

    def test_excess_event_count_cannot_be_positive_control(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, False)
        record, payload = self.record()
        for _ in range(message.COUNT + 1): evidence.consume(c.pointer(record))
        self.assertTrue(evidence.abort.is_set())
        self.assertEqual(evidence.rejected, 1)

    def test_stack_output_contains_only_own_module_rva(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, True)
        record, payload = self.record()
        raw = c.create_string_buffer(struct.pack('<QQQ', 0, 0x100080, 0xDEADBEEF))
        extended_type = type(record.extended)._type_
        extended = (extended_type * 1)(extended_type(0, 6, 0, 24, c.addressof(raw)))
        record.extended, record.extended_count = extended, 1
        evidence.consume(c.pointer(record))
        self.assertEqual(evidence.stacks, [[{'module': 'own.dll', 'rva': '0x80'}]])
        self.assertFalse(evidence.abort.is_set())

    def test_missing_stack_stops_stack_phase(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, True)
        record, payload = self.record(); evidence.consume(c.pointer(record))
        self.assertEqual(evidence.unknown_stack, 1)
        self.assertTrue(evidence.abort.is_set())

    def test_null_extended_data_pointer_is_rejected(self):
        evidence = message.Evidence(self.receiver, self.sender, self.offsets, True)
        record, payload = self.record(); record.extended_count = 1
        evidence.consume(c.pointer(record))
        self.assertEqual(evidence.malformed, 1)
        self.assertEqual(evidence.reasons, {'null_stack': 1})
        self.assertTrue(evidence.abort.is_set())


if __name__ == '__main__':
    unittest.main()
