"""Pure offline contracts: never register providers, spawn children or start ETW."""
import ctypes as c
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from scripts import probe_private_etw_scope as scope


class PrivateScopeContracts(unittest.TestCase):
    def setUp(self):
        self.guid = uuid.UUID('6940e956-8657-4a35-a63f-23bef798e049').bytes_le

    def event(self, pid=11, tid=21, sequence=0, nonce=77):
        record = scope.Record()
        record.header.provider[:] = self.guid
        record.header.pid, record.header.tid, record.header.flags = pid, tid, 0x40
        record.header.descriptor = scope.Descriptor.from_buffer_copy(scope.EVENT)
        payload = c.create_string_buffer(struct.pack('<II', sequence, nonce))
        record.data, record.data_length = c.addressof(payload), 8
        return record, payload

    def test_v2_layout_and_same_pid_filter_for_every_api(self):
        scope.check_abi()
        obj = scope.Scope('Synthetic-only', self.guid, 11, 'C:/owned/source.etl')
        for _ in range(3):
            storage, properties = obj.properties()
            self.assertEqual(c.sizeof(scope.PropertiesV2), 144)
            self.assertEqual(properties.wnode.flags, 0x820000)
            self.assertEqual(properties.v2_control, 2)
            self.assertEqual(properties.mode, 0x801)
            self.assertEqual(properties.maximum_file, 4)
            self.assertEqual(properties.filter_count, 1)
            descriptor = properties.filters[0]
            self.assertEqual((descriptor.kind, descriptor.size), (scope.PID_FILTER, 4))
            self.assertEqual(c.cast(descriptor.pointer, c.POINTER(scope.U32)).contents.value, 11)
            self.assertEqual(descriptor.pointer, obj.enable().filters[0].pointer)
            self.assertGreaterEqual(len(storage) - properties.file_offset, 2048)

    def test_scope_never_accepts_invalid_pid(self):
        for pid in (0, -1, 2**32, '11'):
            with self.assertRaises(ValueError):
                scope.Scope('Synthetic', self.guid, pid, 'file.etl')

    def test_stop_preserves_filter_and_checks_guid_before_stop(self):
        obj = scope.Scope('Synthetic', self.guid, 11, 'file.etl')
        seen = []
        def control(handle, name, storage, operation):
            p = scope.PropertiesV2.from_buffer(storage)
            seen.append((name, operation, p.filters[0].pointer))
            return 4201 if len(seen) == 3 else 0
        api = Mock(ControlTraceW=Mock(side_effect=control))
        result = scope.stop_scope(api, obj, scope.U64(123))
        self.assertEqual([x[1] for x in seen], [0, 1, 0])
        self.assertEqual(len({x[2] for x in seen}), 1)
        self.assertTrue(result['absent'])

    def test_stop_refuses_wrong_guid(self):
        obj = scope.Scope('Synthetic', self.guid, 11, 'file.etl')
        def query(handle, name, storage, operation):
            scope.PropertiesV2.from_buffer(storage).wnode.guid[:] = bytes(16)
            return 0
        api = Mock(ControlTraceW=Mock(side_effect=query))
        self.assertEqual(scope.stop_scope(api, obj)['error'], 'scope_query_or_identity_failed')
        self.assertEqual(api.ControlTraceW.call_count, 1)

    def test_stop_error_is_not_absence(self):
        obj = scope.Scope('Synthetic', self.guid, 11, 'file.etl')
        api = Mock(ControlTraceW=Mock(return_value=5))
        self.assertFalse(scope.stop_scope(api, obj).get('absent', False))

    def test_unknown_header_does_not_dereference_payload(self):
        event, raw = self.event(pid=99)
        event.data = 1
        evidence = scope.Evidence(self.guid, (11, 21), (12, 22), 77)
        with patch.object(scope.c, 'string_at', side_effect=AssertionError('payload accessed')) as read:
            evidence.consume(c.pointer(event))
        read.assert_not_called()
        self.assertEqual(evidence.counts['unknown'], 1)
        self.assertTrue(evidence.aborted)

    def test_excluded_own_process_is_rejected_without_payload_or_stack(self):
        event, raw = self.event(pid=12, tid=22)
        event.data = 1
        event.extended_count = 65535
        evidence = scope.Evidence(self.guid, (11, 21), (12, 22), 77)
        with patch.object(scope.c, 'string_at', side_effect=AssertionError('payload accessed')) as read:
            evidence.consume(c.pointer(event))
        read.assert_not_called()
        self.assertEqual(evidence.counts, {'positive': 0, 'negative': 1, 'unknown': 0, 'malformed': 0})

    def test_exact_sequence_count_and_duplicate_nonce_rejected(self):
        evidence = scope.Evidence(self.guid, (11, 21), (12, 22), 77)
        for sequence in range(scope.COUNT):
            event, raw = self.event(sequence=sequence)
            evidence.consume(c.pointer(event))
        self.assertEqual(evidence.counts['positive'], scope.COUNT)
        event, raw = self.event(sequence=0)
        evidence.consume(c.pointer(event))
        self.assertEqual(evidence.counts['malformed'], 1)

    def test_stale_phase_nonce_rejected(self):
        evidence = scope.Evidence(self.guid, (11, 21), (12, 22), 78)
        event, raw = self.event()
        evidence.consume(c.pointer(event))
        self.assertEqual(evidence.counts['positive'], 0)
        self.assertEqual(evidence.counts['malformed'], 1)

    def test_full_descriptor_checked_before_payload(self):
        evidence = scope.Evidence(self.guid, (11, 21), (12, 22), 77)
        event, raw = self.event()
        event.header.descriptor.keyword = 1
        event.data = 1
        with patch.object(scope.c, 'string_at', side_effect=AssertionError('payload accessed')) as read:
            evidence.consume(c.pointer(event))
        read.assert_not_called()
        self.assertEqual(evidence.counts['malformed'], 1)

    def test_private_pid_suffix_and_all_negative_files_are_included(self):
        with tempfile.TemporaryDirectory(dir=scope.ROOT / '.local') as directory:
            root = Path(directory)
            for name in ('phase0.etl', 'phase0.etl_12'):
                (root / name).touch()
            self.assertEqual(len(scope.private_files(root, 'phase0', (11, 12))), 2)
            (root / 'phase0.etl_12').rename(root / 'phase0.etl_99')
            with self.assertRaisesRegex(RuntimeError, 'unexpected_private_filename'):
                scope.private_files(root, 'phase0', (11, 12))

    def test_missing_negative_control_prevents_pass(self):
        phase = {'trace_started': True, 'start_status': 0, 'enable_status': 0, 'sent': 4,
            'opposite_sent': 4, 'duration_seconds': 1, 'file_count': 1, 'process_status': [0],
            'close_status': [0], 'received': {'positive': 4, 'negative': 0, 'unknown': 0, 'malformed': 0},
            'cleanup': {'absent': True, 'stop_status': 0, 'events_lost': 0}}
        self.assertTrue(scope.phase_passed(phase))
        for key, value in [('opposite_sent', 0), ('file_count', 2), ('duration_seconds', 6)]:
            self.assertFalse(scope.phase_passed(dict(phase, **{key: value})))

    def test_default_invocation_does_not_run_native(self):
        with patch.object(scope.sys, 'argv', ['probe_private_etw_scope.py']), patch.object(scope, 'run') as run:
            self.assertEqual(scope.main(), 0)
        run.assert_not_called()

    def test_cleanup_exception_is_visible(self):
        result = scope.safe(lambda: (_ for _ in ()).throw(OSError('failed')))
        self.assertIn('OSError:failed', result['error'])


if __name__ == '__main__':
    unittest.main()
