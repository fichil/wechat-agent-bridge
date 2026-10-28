"""Own synthetic records only: imports never start ETW or load UIA."""
import ctypes as c
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_event_source as probe
from scripts.test_focus_etw import Extended


class UiaEventSourceTests(unittest.TestCase):
    def record(self, frames=(0x10472F,)):
        record = probe.Record()
        record.header.provider[:] = probe.PROVIDER
        record.header.pid, record.header.tid = 12, 34
        record.header.flags = 0x40
        record.header.descriptor = probe.Descriptor.from_buffer_copy(probe.EVENT)
        raw = c.create_string_buffer(struct.pack('<' + 'Q' * (1 + len(frames)), 1,
                                               *(0x1000000 + f for f in frames)))
        ext = (Extended * 1)(Extended(0, 6, 0, 8 * (1 + len(frames)), c.addressof(raw)))
        record.extended_count, record.extended = 1, ext
        evidence = probe.Evidence(12, 34, [(0x1000000, 0x400000, 'UIAutomationCore.dll')])
        return evidence, record, (raw, ext)

    def test_private_flags_cannot_enable_cross_process_or_realtime(self):
        storage, properties = probe.file_properties(Path('synthetic.etl'))
        self.assertEqual(properties.mode, 0x20801)
        self.assertFalse(properties.mode & 0x100)
        self.assertEqual(bytes(properties.wnode.guid), probe.PROVIDER)
        self.assertEqual(properties.maximum_file, 4)
        self.assertLess(properties.name_offset, properties.file_offset)
        self.assertLess(properties.file_offset, len(storage))

    def test_foreign_owner_rejected_before_invalid_extended_pointer(self):
        evidence, record, keepalive = self.record()
        record.header.pid = 99
        record.extended = c.cast(1, c.POINTER(Extended))
        evidence.consume(c.pointer(record))
        self.assertEqual((evidence.accepted, evidence.rejected), (0, 1))

    def test_exact_entry_stack_and_maximum_count(self):
        evidence, record, keepalive = self.record()
        for i in range(2 * probe.COUNT + 1):
            record.header.descriptor = probe.Descriptor.from_buffer_copy(
                probe.EVENT if i % 2 == 0 else probe.STOP_EVENT)
            evidence.consume(c.pointer(record))
        self.assertEqual((evidence.accepted, evidence.rejected), (2 * probe.COUNT, 1))
        self.assertEqual(evidence.stacks[0][0]['rva'], 0x10472F)

    def test_missing_unknown_and_outside_entry_stacks_rejected(self):
        for frames in ((), (0x500000,), (0x104904,)):
            with self.subTest(frames=frames):
                evidence, record, keepalive = self.record(frames)
                evidence.consume(c.pointer(record))
                self.assertEqual((evidence.accepted, evidence.rejected), (0, 1))

    def test_schema_requires_no_payload_and_expected_task(self):
        meta = Mock()
        raw = probe.PROVIDER + b'\0' * 16 + probe.EVENT + b'\0' * 80
        meta.buffer.side_effect = lambda _name, _provider, descriptor: (
            probe.PROVIDER + b'\0' * 16 + bytes(descriptor._obj) + b'\0' * 80)
        info = {'provider': 'Microsoft-Windows-UIAutomationCore',
                'task': 'UIAutomationCoreAPI_UiaRaiseEvent', 'fields': []}
        with patch.object(probe, 'parse_event', return_value=info):
            self.assertEqual([len(item) for item in probe.validate_metadata(meta)], [64, 64])
        for change in ({'fields': [{}]}, {'task': 'HandleWinEvent'}, {'provider': 'other'}):
            with patch.object(probe, 'parse_event', return_value=dict(info, **change)):
                with self.assertRaises(RuntimeError):
                    probe.validate_metadata(meta)

    def test_report_needs_cleanup_exact_api_outcome_and_stacks(self):
        result = dict(trace_started=True, enable_status=0, dll_unchanged=True, stack_requested=True,
                      api_results=[0x80070057] * probe.COUNT, process_status=0,
                      accepted=2 * probe.COUNT, rejected=0, stacks=[[{}]] * (2 * probe.COUNT),
                      cleanup={'stop_status': 0, 'events_lost': 0,
                               'query_after_stop': 4201, 'close_status': 0})
        self.assertTrue(probe.passed(result))
        for change in ({'api_results': [0] * probe.COUNT}, {'cleanup': {}},
                       {'rejected': 1}, {'stacks': []}, {'dll_unchanged': False},
                       {'error': 'timeout'}, {'stack_requested': False}):
            self.assertFalse(probe.passed(dict(result, **change)))

    def test_entry_only_does_not_read_extended_data_or_prove_stack(self):
        evidence, record, keepalive = self.record()
        evidence.require_stack = False
        record.extended = c.cast(1, c.POINTER(Extended))
        evidence.consume(c.pointer(record))
        self.assertEqual((evidence.accepted, evidence.rejected), (1, 0))
        self.assertEqual(evidence.stacks, [])

    def test_cleanup_failure_keeps_files_and_reports_unconfirmed_child(self):
        worker = Mock(pid=123, exitcode=None)
        worker.is_alive.return_value = True
        worker.terminate.side_effect = OSError('synthetic')
        with patch.object(probe.shutil, 'rmtree') as remove:
            result = probe.finalize_owned(worker, Path('uia-own-synthetic'), Path('.'))
        self.assertFalse(result['child_terminated'])
        self.assertEqual(result['cleanup_errors'], ['child_exit_unconfirmed'])
        remove.assert_not_called()

    def test_cleanup_refuses_changed_temporary_directory_scope(self):
        worker = Mock(pid=123, exitcode=0)
        worker.is_alive.return_value = False
        with patch.object(probe.shutil, 'rmtree') as remove:
            result = probe.finalize_owned(worker, Path('outside/uia-own-synthetic'), Path('.'))
        self.assertTrue(result['child_terminated'])
        self.assertEqual(result['cleanup_errors'], ['temporary_cleanup_failed'])
        remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
