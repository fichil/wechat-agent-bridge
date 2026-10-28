"""Offline contracts for the own-child UIA/private-scope combination probe."""
import ctypes as c
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_private_scope as combined
from scripts.test_focus_etw import Extended, Record, Descriptor


class UiaPrivateScopeContracts(unittest.TestCase):
    def scope(self):
        return combined.Scope('WechatBridge-UiaPrivateScope-test', combined.uia.PROVIDER,
                              11, 'C:/owned/source.etl')

    def event(self, pid=11, tid=21, stop=False):
        event = Record()
        event.header.provider[:] = combined.uia.PROVIDER
        event.header.pid, event.header.tid, event.header.flags = pid, tid, 0x40
        event.header.descriptor = Descriptor.from_buffer_copy(combined.uia.STOP_EVENT if stop else combined.uia.EVENT)
        stack = c.create_string_buffer(struct.pack('<QQ', 0, 0x10000000 + 0x104300))
        extended = (Extended * 1)(Extended(0, 6, 0, 16, c.addressof(stack)))
        event.extended, event.extended_count = extended, 1
        return event, stack, extended

    def test_scope_reuses_pid_filter_and_only_requests_stack_property(self):
        obj = self.scope()
        storage, p = obj.properties()
        self.assertEqual(len(storage), 4240)
        self.assertEqual((p.name_offset, p.file_offset), (144, 2192))
        self.assertEqual((p.mode, p.v2_control, p.filter_count), (0x801, 2, 1))
        self.assertEqual(obj.enable().properties, 4)
        self.assertEqual(obj.enable().filters[0].pointer, p.filters[0].pointer)

    def write_outputs(self, obj, storage, name=None, filename=None):
        p = combined.private.PropertiesV2.from_buffer(storage)
        for offset, value in ((p.name_offset, obj.name if name is None else name),
                              (p.file_offset, obj.path if filename is None else filename)):
            encoded = (value + '\0').encode('utf-16-le')
            c.memmove(c.addressof(storage) + offset, encoded, len(encoded))

    def test_stop_requires_actual_output_not_prefilled_input(self):
        obj, calls = self.scope(), []
        def control(handle, name, storage, command):
            calls.append(command)
            if len(calls) == 1:
                p = combined.private.PropertiesV2.from_buffer(storage)
                self.assertEqual(combined.returned_wide(storage, p.name_offset), '')
                self.assertEqual(combined.returned_wide(storage, p.file_offset), '')
                self.write_outputs(obj, storage, filename=obj.path + '_11')
            return 4201 if len(calls) == 3 else 0
        api = Mock(ControlTraceW=Mock(side_effect=control))
        result = combined.stop_owned_scope(api, obj)
        self.assertTrue(result['actual_name_and_file_verified'])
        self.assertTrue(result['absent'])
        self.assertEqual(calls, [0, 1, 0])

    def test_shared_guid_with_no_output_or_wrong_name_cannot_stop(self):
        for name in (None, 'OtherSession'):
            obj = self.scope()
            def query(handle, actual, storage, command):
                if name:
                    self.write_outputs(obj, storage, name=name)
                return 0
            api = Mock(ControlTraceW=Mock(side_effect=query))
            result = combined.stop_owned_scope(api, obj)
            self.assertEqual(result['error'], 'owned_query_identity_unproven')
            self.assertFalse(result['query_evidence']['name_matches'])
            self.assertEqual(result['query_evidence']['name_empty'], name is None)
            self.assertEqual(api.ControlTraceW.call_count, 1)

    def test_other_pid_file_path_does_not_prove_ownership(self):
        obj = self.scope()
        def query(handle, name, storage, command):
            self.write_outputs(obj, storage, filename=obj.path + '_12')
            return 0
        api = Mock(ControlTraceW=Mock(side_effect=query))
        self.assertFalse(combined.stop_owned_scope(api, obj).get('absent', False))
        self.assertEqual(api.ControlTraceW.call_count, 1)

    def test_query_string_boundaries(self):
        storage = c.create_string_buffer(160)
        for offset in (0, 143, 145, 160, 2**32):
            with self.assertRaises(ValueError):
                combined.returned_wide(storage, offset)
        c.memset(c.addressof(storage) + 144, 65, 16)
        with self.assertRaisesRegex(ValueError, 'unterminated'):
            combined.returned_wide(storage, 144)

    def test_unknown_and_negative_never_reach_stack_decoder(self):
        for pid, tid, key in ((99, 22, 'unknown'), (12, 22, 'negative'), (11, 99, 'unknown')):
            evidence = combined.Evidence((11, 21), (12, 22), [])
            event, stack, extended = self.event(pid, tid)
            event.data, event.extended_count = 1, 65535
            event.extended = c.cast(c.c_void_p(1), c.POINTER(Extended))
            with patch.object(evidence.inner, 'consume') as decoder:
                evidence.consume(c.pointer(event))
            decoder.assert_not_called()
            self.assertEqual(evidence.counts[key], 1)
            self.assertTrue(evidence.aborted)

    def test_selected_pairs_and_module_rva_stacks(self):
        evidence = combined.Evidence((11, 21), (12, 22), [(0x10000000, 0x800000, 'UIAutomationCore.dll')])
        for index in range(8):
            event, stack, extended = self.event(stop=index % 2 == 1)
            evidence.consume(c.pointer(event))
        self.assertEqual(evidence.counts, {'positive': 8, 'negative': 0, 'unknown': 0, 'malformed': 0})
        self.assertEqual(len(evidence.inner.stacks), 8)
        self.assertEqual(evidence.inner.stacks[0], [{'module': 'UIAutomationCore.dll', 'rva': 0x104300}])

    def test_unknown_module_stack_and_out_of_order_event_fail(self):
        for modules, stop in (([], False), ([(0x10000000, 0x800000, 'UIAutomationCore.dll')], True)):
            evidence = combined.Evidence((11, 21), (12, 22), modules)
            event, stack, extended = self.event(stop=stop)
            evidence.consume(c.pointer(event))
            self.assertEqual(evidence.counts['malformed'], 1)
            self.assertTrue(evidence.aborted)

    def test_failed_start_does_not_query_or_stop_shared_provider(self):
        own = {'pid': 11, 'tid': 21}
        pipe = Mock()
        pipe.poll.return_value = True
        pipe.recv.side_effect = [{'bind': [own, {'pid': 12, 'tid': 22}]}, {'phase': 0}]
        api = Mock()
        api.StartTraceW.return_value = 183
        with patch.object(combined, 'initialize_source', return_value=(None, Mock(), own)), \
                patch.object(combined.private, 'controller', return_value=api), \
                patch.object(combined, 'validate_identities', side_effect=lambda values, own: values), \
                patch.object(combined, 'OwnedProcesses'), \
                patch.object(combined.uia, 'uia_hash', return_value=combined.uia.DLL_HASH), \
                patch.object(combined, 'stop_owned_scope') as stop:
            combined.source_child(pipe, 'C:/owned', '0' * 32, True)
        stop.assert_not_called()
        api.EnableTraceEx2.assert_not_called()

    def test_source_exit_prevents_start_and_enable(self):
        own = {'pid': 11, 'tid': 21}
        pipe = Mock()
        pipe.poll.return_value = True
        pipe.recv.side_effect = [{'bind': [own, {'pid': 12, 'tid': 22}]}, {'phase': 0}]
        api, owned = Mock(), Mock()
        owned.check.side_effect = RuntimeError('own_source_exited')
        with patch.object(combined, 'initialize_source', return_value=(None, Mock(), own)), \
                patch.object(combined.private, 'controller', return_value=api), \
                patch.object(combined, 'validate_identities', side_effect=lambda values, own: values), \
                patch.object(combined, 'OwnedProcesses', return_value=owned), \
                patch.object(combined.uia, 'uia_hash', return_value=combined.uia.DLL_HASH):
            combined.source_child(pipe, 'C:/owned', '0' * 32, True)
        api.StartTraceW.assert_not_called()
        api.EnableTraceEx2.assert_not_called()

    def test_phase_requires_scope_ownership_and_negative_source(self):
        phase = {'trace_started': True, 'start_status': 0, 'enable_status': 0,
            'source_results': [combined.INVALID] * 4, 'opposite_results': [combined.INVALID] * 4,
            'duration_seconds': 1, 'file_count': 1, 'process_status': [0], 'close_status': [0],
            'received': {'positive': 8, 'negative': 0, 'unknown': 0, 'malformed': 0}, 'stacks': [[]] * 8,
            'cleanup': {'absent': True, 'stop_status': 0, 'events_lost': 0, 'actual_name_and_file_verified': True}}
        self.assertTrue(combined.phase_passed(phase))
        self.assertFalse(combined.phase_passed(dict(phase, opposite_results=[])))
        phase['cleanup']['actual_name_and_file_verified'] = False
        self.assertFalse(combined.phase_passed(phase))

    def test_default_does_not_run_native(self):
        with patch.object(combined.sys, 'argv', ['probe_uia_private_scope.py']), patch.object(combined, 'run') as run:
            self.assertEqual(combined.main(), 0)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
