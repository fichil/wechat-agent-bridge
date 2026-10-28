"""Pure layout/filter checks; no native calls, windows or ETW session."""
import ctypes as c
import struct
import unittest
from unittest.mock import patch

from scripts.test_focus_etw import (abi_check, matches, properties, Properties, Logfile,
                                    validate_schema, Evidence, Record, PROVIDER, SESSION_METADATA,
                                    stimulus_counts, record_stimulus, source_qualified, probe_source)


class FocusEtwContract(unittest.TestCase):
    def test_x64_abi(self):
        if c.sizeof(c.c_void_p) != 8: self.skipTest('x64 ABI only')
        abi_check()
        self.assertEqual(Logfile.callback.offset, 424)

    def test_only_selected_new_tid_matches(self):
        target = {'pid': 123, 'tid': 456}
        self.assertTrue(matches(2, struct.pack('<II', 10, 456), target))
        self.assertFalse(matches(2, struct.pack('<II', 456, 10), target))
        self.assertFalse(matches(2, struct.pack('<II', 10, 123), target))
        self.assertFalse(matches(26, struct.pack('<III', 1, 10, 123), target))

    def test_manifest_order_and_field_types_must_match_decoder(self):
        fields = [{'name': 'OldThreadId', 'in_type': 8}, {'name': 'NewThreadId', 'in_type': 8}]
        validate_schema(2, fields)
        for changed in (fields[::-1], fields[:1], fields + fields,
                        [{'name': 'OldThreadId', 'in_type': 4}, fields[1]]):
            with self.assertRaisesRegex(RuntimeError, 'schema_mismatch'):
                validate_schema(2, changed)

    def test_only_known_session_metadata_may_be_ignored(self):
        evidence = Evidence({'pid': 123, 'tid': 456, 'modules': []})
        record = Record()
        record.header.provider[:] = SESSION_METADATA
        evidence.consume(c.pointer(record))
        self.assertEqual(evidence.other_provider, 1)
        self.assertEqual(evidence.callbacks, 1)
        self.assertFalse(evidence.abort.is_set())
        record.header.provider[:] = bytes(16)
        evidence.consume(c.pointer(record))
        self.assertEqual(evidence.rejected, 1)
        self.assertEqual(evidence.callbacks, 2)
        self.assertTrue(evidence.abort.is_set())
        self.assertEqual(evidence.stacks, [])

    def test_rejected_payload_never_reads_or_records_stack(self):
        evidence = Evidence({'pid': 123, 'tid': 456, 'modules': []})
        record = Record()
        record.header.provider[:] = PROVIDER
        record.header.descriptor.id = 2
        record.header.pid = 123
        payload = c.create_string_buffer(struct.pack('<II', 456, 999))
        record.data, record.data_length = c.addressof(payload), 8
        record.extended_count = 65535  # Would be invalid if inspected.
        evidence.consume(c.pointer(record))
        self.assertEqual(evidence.rejected, 1)
        self.assertEqual(evidence.malformed, 0)
        self.assertTrue(evidence.abort.is_set())
        self.assertEqual(evidence.stacks, [])

    def test_missing_or_malformed_payload_never_matches(self):
        for event_id, data in ((2, b''), (2, b'\0' * 9), (99, b'\0' * 12)):
            self.assertFalse(matches(event_id, data, {'pid': 0, 'tid': 0}))

    def test_session_has_no_log_file_or_kernel_global_flags(self):
        buffer, p = properties('synthetic-test', bytes(range(16)))
        self.assertEqual(p.file_offset, 0)
        self.assertEqual(p.enable, 0)
        self.assertEqual(p.mode, 0x10000100)
        self.assertEqual(bytes(p.wnode.guid), bytes(range(16)))
        self.assertEqual(p.wnode.size, len(buffer))
        self.assertEqual(p.name_offset, c.sizeof(Properties))

    def test_local_edit_focus_does_not_qualify_foreground_source(self):
        counts = stimulus_counts()
        record_stimulus(counts, dict(exercised=True, local_focus_verified=True,
            foreground_result=False, own_foreground_before=False, own_foreground_after=False))
        self.assertEqual(counts['local_focus_verified'], 1)
        self.assertEqual(counts['foreground_transitions'], 0)
        self.assertFalse(source_qualified([counts, counts]))

    def test_source_requires_success_and_observed_transition_for_both_targets(self):
        zero, counts = stimulus_counts(), stimulus_counts()
        for success, before, after in ((True, True, True), (True, False, False), (False, False, True)):
            record_stimulus(counts, dict(exercised=True, local_focus_verified=True,
                foreground_result=success, own_foreground_before=before, own_foreground_after=after))
        self.assertEqual(counts['foreground_transitions'], 0)
        record_stimulus(counts, dict(exercised=True, local_focus_verified=True,
            foreground_result=True, own_foreground_before=False, own_foreground_after=True))
        self.assertFalse(source_qualified([counts, zero]))
        self.assertTrue(source_qualified([counts, counts]))
        self.assertFalse(source_qualified([counts]))

    def test_old_or_invalid_source_telemetry_is_rejected(self):
        for invalid in ({'exercised': True, 'local_focus_verified': True},
                        dict(exercised=True, local_focus_verified=True,
                             foreground_result=1, own_foreground_before=False, own_foreground_after=True)):
            with self.assertRaisesRegex(RuntimeError, 'incomplete_source_telemetry'):
                record_stimulus(stimulus_counts(), invalid)

    def test_source_probe_never_starts_etw_and_reports_invalid_stimulus(self):
        local_only = dict(exercised=True, local_focus_verified=True, foreground_result=False,
                          own_foreground_before=False, own_foreground_after=False)
        with patch('scripts.test_focus_etw.input_snapshot', return_value=('test', 1, 2)), \
             patch('scripts.test_focus_etw.exercise', return_value=local_only), \
             patch('scripts.test_focus_etw.time.sleep'), \
             patch('scripts.test_focus_etw.controller', side_effect=AssertionError('no trace')):
            result = probe_source([object(), object()], ('test', 1, 2))
        self.assertTrue(result['completed'])
        self.assertFalse(result['trace_started'])
        self.assertFalse(result['source_qualified'])
        self.assertEqual([item['exercises'] for item in result['stimuli']], [4, 4])

    def test_source_probe_stops_before_exercise_if_user_desktop_changes(self):
        with patch('scripts.test_focus_etw.input_snapshot', return_value=('test', 2, 2)), \
             patch('scripts.test_focus_etw.exercise') as exercise:
            with self.assertRaisesRegex(RuntimeError, 'user_desktop_changed'):
                probe_source([object(), object()], ('test', 1, 2))
        exercise.assert_not_called()


if __name__ == '__main__':
    unittest.main()
