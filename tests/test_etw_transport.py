"""Synthetic records and mocked ETW only; no tracing, windows or paid APIs."""
import contextlib
import copy
import ctypes as c
import io
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid
import xml.etree.ElementTree as ET

from scripts import test_etw_transport as transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.guid = uuid.UUID('af2cb79f-e9e3-418a-a4b7-5d1c88f87511')
        self.evidence = transport.Evidence(self.guid.bytes_le, 123)

    def record(self, event_id=2, index=0, value=transport.POSITIVE, pid=123, provider=None):
        record = transport.Record()
        record.header.provider[:] = provider or self.guid.bytes_le
        record.header.pid, record.header.descriptor.id = pid, event_id
        payload = c.create_string_buffer(struct.pack('<II', index, value))
        record.data, record.data_length = c.addressof(payload), 8
        return record, payload

    def consume(self, **kwargs):
        record, payload = self.record(**kwargs)
        self.evidence.consume(c.pointer(record))

    def test_manifest_has_unique_provider_and_only_synthetic_pair(self):
        root = ET.fromstring(transport.manifest_text(self.guid))
        ns = {'e': 'http://schemas.microsoft.com/win/2004/08/events'}
        provider = root.find('e:instrumentation/e:events/e:provider', ns)
        self.assertEqual(provider.attrib['guid'], '{' + str(self.guid) + '}')
        self.assertIsNone(provider.find('e:channels', ns))
        self.assertEqual([node.attrib['name'] for node in provider.findall('e:templates/e:template/e:data', ns)],
                         ['OldThreadId', 'NewThreadId'])
        self.assertEqual([node.attrib['value'] for node in provider.findall('e:events/e:event', ns)], ['2', '3'])

    def test_event_data_abi_and_exact_emission_counts(self):
        self.assertEqual(c.sizeof(transport.EventData), 16)
        counts = dict.fromkeys(transport.EXPECTED_SENT, 0)
        for kind, event_id, payload in transport.emission_plan():
            counts[kind] += 1
            self.assertEqual(len(payload), 8)
            if kind == 'wrong_id':
                self.assertEqual(event_id, 3)
                self.assertEqual(struct.unpack('<II', payload)[1], transport.POSITIVE)
        self.assertEqual(counts, transport.EXPECTED_SENT)

    def test_each_positive_must_arrive_exactly_once(self):
        for index in range(transport.COUNT): self.consume(index=index)
        self.assertEqual(self.evidence.counts, transport.EXPECTED_RECEIVED)
        self.assertFalse(self.evidence.abort.is_set())
        self.consume(index=0)
        self.assertEqual(self.evidence.counts['malformed'], 1)
        self.assertTrue(self.evidence.abort.is_set())

    def test_negative_and_wrong_id_are_counted_as_filter_failures(self):
        self.consume(value=transport.NEGATIVE)
        self.consume(event_id=3)
        self.assertEqual(self.evidence.counts['negative'], 1)
        self.assertEqual(self.evidence.counts['wrong_id'], 1)
        self.assertEqual(self.evidence.counts['positive'], 0)
        self.assertTrue(self.evidence.abort.is_set())

    def test_unrelated_source_rejected_before_invalid_payload_access(self):
        record, payload = self.record(pid=999)
        record.data = 1  # Must never dereference data belonging to unknown source.
        record.extended_count = 65535
        self.evidence.consume(c.pointer(record))
        self.assertEqual(self.evidence.counts['unexpected'], 1)
        self.assertEqual(self.evidence.counts['malformed'], 0)

    def test_default_cli_never_invokes_native_runner(self):
        stream = io.StringIO()
        with patch.object(transport.sys, 'argv', ['test_etw_transport.py']), \
                patch.object(transport, 'run_canary') as native, contextlib.redirect_stdout(stream):
            self.assertEqual(transport.main(), 0)
        native.assert_not_called()
        self.assertFalse(json.loads(stream.getvalue())['trace_started'])

    def test_metadata_cli_never_invokes_trace_runner(self):
        stream = io.StringIO()
        with patch.object(transport.sys, 'argv', ['test_etw_transport.py', '--check-metadata']), \
                patch.object(transport, 'run_canary') as native, \
                patch.object(transport, 'check_metadata', return_value={'passed': True, 'trace_started': False}) as metadata, \
                contextlib.redirect_stdout(stream):
            self.assertEqual(transport.main(), 0)
        native.assert_not_called()
        metadata.assert_called_once()

    def test_cleanup_exception_is_reported_without_skipping_unload(self):
        dll = SimpleNamespace(TdhCleanupPayloadEventFilterDescriptor=MagicMock(side_effect=OSError('cleanup failed')),
                              TdhUnloadManifest=MagicMock(return_value=0))
        preparation = transport.Preparation(SimpleNamespace(dll=dll), 'synthetic.man', None)
        preparation.loaded = True
        preparation.bundle = (transport.Filter(), object(), object())
        result = preparation.close()
        self.assertEqual(result['filter_cleanup_status'], {'error': 'cleanup failed'})
        self.assertEqual(result['manifest_unload_status'], 0)
        dll.TdhUnloadManifest.assert_called_once()

    def test_success_requires_counts_cleanup_and_unchanged_sources(self):
        report = {'received': dict(transport.EXPECTED_RECEIVED), 'owner_exitcode': 0,
                  'consumer_finished': True, 'process_trace_status': [0],
                  'manifest_directory_removed': True, 'source_unchanged': True,
                  'cleanup_verification': {'stopped': True},
                  'owner': {'sent': dict(transport.EXPECTED_SENT), 'write_errors': 0,
                            'provider_enabled': True, 'cleanup': {'stopped': True, 'events_lost': 0},
                            'enable_to_stop_seconds': 2, 'unregister_status': 0,
                            'filter_cleanup_status': 0, 'manifest_unload_status': 0}}
        self.assertTrue(transport.passed(report))
        for change in ({'source_unchanged': False}, {'owner_terminated': True},
                       {'manifest_directory_removed': False}, {'process_trace_status': [1223]},
                       {'consumer_finished': False}, {'received': dict.fromkeys(transport.EXPECTED_RECEIVED, 0)}):
            self.assertFalse(transport.passed(dict(report, **change)))
        for key, value in (('unregister_status', 5), ('manifest_unload_status', 5),
                           ('write_errors', 1), ('enable_to_stop_seconds', 6)):
            changed = copy.deepcopy(report); changed['owner'][key] = value
            self.assertFalse(transport.passed(changed))

    def mock_owner(self, write_status=0):
        api = SimpleNamespace(**{name: MagicMock(return_value=0) for name in (
            'EventRegister', 'EventWrite', 'EventUnregister', 'StartTraceW', 'EnableTraceEx2')})
        api.EventRegister.side_effect = lambda _g, _cb, _ctx, out: self.set_u64(out, 42)
        api.StartTraceW.side_effect = lambda out, _n, _p: self.set_u64(out, 73)
        api.EventWrite.return_value = write_status
        dll = SimpleNamespace(**{name: MagicMock(return_value=0) for name in (
            'TdhLoadManifest', 'TdhUnloadManifest', 'TdhCleanupPayloadEventFilterDescriptor')})
        meta = SimpleNamespace(dll=dll)
        bundle = (transport.Filter(1234, 64, 0x80000100), object(), (transport.Filter * 2)())
        pipe = MagicMock()
        pipe.poll.return_value = True
        pipe.recv.side_effect = ['enable', 'emit', 'done']
        with patch.object(transport, 'controller', return_value=api), \
                patch.object(transport, 'Metadata', return_value=meta), \
                patch.object(transport, 'validate_manifest', return_value=[]), \
                patch.object(transport, 'build_filter', return_value=bundle), \
                patch.object(transport, 'stop_owned', return_value={'stopped': True, 'events_lost': 0}) as stop:
            transport.owner(pipe, 'random-test-session', bytes(range(16)), self.guid.bytes_le, 'synthetic.man')
        result = pipe.send.call_args_list[-1].args[0]['finished']
        stop.assert_called_once()
        api.EventUnregister.assert_called_once()
        dll.TdhUnloadManifest.assert_called_once_with('synthetic.man')
        dll.TdhCleanupPayloadEventFilterDescriptor.assert_called_once()
        pipe.close.assert_called_once()
        return result, api

    @staticmethod
    def set_u64(pointer, value):
        c.cast(pointer, c.POINTER(transport.U64)).contents.value = value
        return 0

    def test_owner_emits_all_three_controls_then_cleans_resources(self):
        result, api = self.mock_owner()
        self.assertNotIn('error', result)
        self.assertEqual(result['sent'], transport.EXPECTED_SENT)
        self.assertEqual(api.EventWrite.call_count, 3 * transport.COUNT)
        self.assertEqual(result['manifest_unload_status'], 0)

    def test_write_failure_still_stops_unregisters_and_unloads(self):
        result, api = self.mock_owner(write_status=5)
        self.assertEqual(result['error'], 'event_write_failed_5')
        self.assertEqual(result['write_errors'], 1)
        self.assertEqual(api.EventWrite.call_count, 1)
        self.assertTrue(result['cleanup']['stopped'])


if __name__ == '__main__':
    unittest.main()
