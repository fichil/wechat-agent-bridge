import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from scripts import probe_sent_message_metadata as probe
from wechat_agent_bridge.console_models import ConsoleError


class SentMetadataTests(unittest.TestCase):
    def test_default_never_reads_client(self):
        with patch.object(probe.original,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]),0)

    def test_sample_requires_actual_request_and_exact_process(self):
        fp={'pid':1,'created':2,'hwnd':3}; runtime=[42,3,4,-100]
        report=dict(state='message_echo_observed',patch={'fingerprint':fp},patch_restored=True,
            desktop_unchanged=True,events_observed=0,cleanup_errors=[],worker=dict(new_exact_echo_count=1,
            draft_empty_after=True,echo_runtime_id=runtime,native_evidence=dict(qt_method_called=True,method_returned=True,exception_code=0)))
        self.assertEqual(probe.require_sent_sample(report,fp),runtime)
        with self.assertRaises(ConsoleError): probe.require_sent_sample(report,{'pid':1,'created':3})
        report['worker']['native_evidence']['method_returned']=False
        with self.assertRaises(ConsoleError): probe.require_sent_sample(report,fp)

    def test_outside_module_pointer_stops_before_any_object_read(self):
        fp=SimpleNamespace(module_base=0x100000,module_size=0x10000)
        memory=Mock(); memory.number.return_value=0x400000
        with patch('wechat_agent_bridge.adapters.runtime_accessibility.interface_for_id',return_value=0x300000):
            with self.assertRaisesRegex(ConsoleError,'table_invalid'): probe.inspect_row(memory,fp,Mock(),[42,1])
        self.assertEqual(memory.number.call_count,1)
