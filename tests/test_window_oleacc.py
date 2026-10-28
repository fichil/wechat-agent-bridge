import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from scripts import window_oleacc as oleacc
from scripts import probe_window_oleacc as synthetic
from wechat_agent_bridge.console_models import ConsoleError


class WindowOleaccTests(unittest.TestCase):
    def setUp(self):
        self.port = Mock()
        class NoContent:
            def __getattr__(self, name): raise AssertionError('Content or action queried: ' + name)
        self.port.create.return_value = (0, NoContent())
        self.port.owner.return_value = (0, 101)

    def test_scoped_presence_never_reads_interface_content_or_invokes(self):
        result = oleacc.inspect_window(self.port, 101)
        self.assertEqual(result['status'], 'scoped_interface_available')
        self.assertTrue(result['scope_verified'])
        for key in ('interface_content_read','hit_test_called','action_executed'):
            self.assertFalse(result[key])

    def test_factory_failure_and_null_are_distinct_and_never_query_owner(self):
        for reply, status in (((-2147467262,None),'factory_failed'),((0,None),'factory_null')):
            self.port.create.return_value = reply
            self.assertEqual(oleacc.inspect_window(self.port,101)['status'],status)
        self.port.owner.assert_not_called()

    def test_wrong_source_window_is_rejected_before_factory(self):
        self.port.require_window.side_effect = ConsoleError('foreign')
        with self.assertRaises(ConsoleError): oleacc.inspect_window(self.port,101)
        self.port.create.assert_not_called()

    def test_returned_object_foreign_owner_is_not_promoted(self):
        self.port.owner.return_value = (0,999)
        self.port.require_window.side_effect = lambda hwnd: (_ for _ in ()).throw(ConsoleError('foreign')) if hwnd==999 else None
        result = oleacc.inspect_window(self.port,101)
        self.assertTrue(result['interface_available'])
        self.assertFalse(result['scope_verified'])
        self.assertEqual(result['status'],'object_outside_selected_root')

    def test_owner_failure_or_no_hwnd_cannot_prove_scope(self):
        for reply in ((-1,0),(0,0)):
            self.port.owner.return_value = reply
            self.assertEqual(oleacc.inspect_window(self.port,101)['status'],'object_window_unavailable')

    def test_source_recycled_after_factory_is_rejected(self):
        self.port.require_window.side_effect = [None,None,ConsoleError('changed')]
        with self.assertRaises(ConsoleError): oleacc.inspect_window(self.port,101)

    def test_synthetic_cli_defaults_to_no_native_actions(self):
        with patch.object(synthetic.mp,'get_context') as factory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(synthetic.main([]),0)
            factory.assert_not_called()


if __name__=='__main__': unittest.main()
