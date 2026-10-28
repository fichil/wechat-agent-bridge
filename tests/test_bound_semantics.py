import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import probe_bound_semantics as p
from wechat_agent_bridge.console_models import ConsoleError


class BoundSemanticsTests(unittest.TestCase):
    def uia(self):
        return SimpleNamespace(**{name: index for index, name in enumerate(
            [x for n in p.PATTERNS for x in ('UIA_Is' + n + 'PatternAvailablePropertyId', 'UIA_' + n + 'PatternId')], 1)})

    def test_default_never_opens_client(self):
        with patch.object(p.original, 'run', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main([]), 0)

    def test_available_pattern_is_not_executed_or_verified(self):
        element = Mock()
        element.GetCurrentPropertyValue.side_effect = lambda prop: prop == self.uia().UIA_IsInvokePatternAvailablePropertyId
        result = p.pattern_inventory(element, self.uia())
        self.assertEqual(result['Invoke']['state'], 'available')
        element.GetCurrentPattern.return_value.Invoke.assert_not_called()
        self.assertTrue(all(not row['action_executed'] for row in result.values()))

    def test_false_or_failed_properties_do_not_acquire_pattern(self):
        for value in (False, None):
            element = Mock()
            element.GetCurrentPropertyValue.return_value = value
            p.pattern_inventory(element, self.uia())
            element.GetCurrentPattern.assert_not_called()
        element = Mock()
        element.GetCurrentPropertyValue.side_effect = RuntimeError('private text')
        self.assertNotIn('private text', json.dumps(p.pattern_inventory(element, self.uia())))

    def test_mismatched_process_stops_before_patterns(self):
        element = Mock(CurrentProcessId=8)
        element.GetRuntimeId.return_value = [7, 1]
        with self.assertRaises(ConsoleError): p.element_metadata(element, self.uia(), 7)
        element.GetCurrentPropertyValue.assert_not_called()

    def test_changed_runtime_is_rejected_and_name_never_read(self):
        element = Mock(CurrentProcessId=7, CurrentClassName='mmui::XOutlineButton',
                       CurrentControlType=50000, CurrentNativeWindowHandle=0, CurrentIsEnabled=True)
        element.GetRuntimeId.side_effect = ([7, 1], [7, 2])
        element.GetCurrentPropertyValue.return_value = False
        with self.assertRaises(ConsoleError): p.element_metadata(element, self.uia(), 7)
        self.assertNotIn('CurrentName', element.__dict__)

    def test_worker_rejects_write_without_importing_native(self):
        pipe = Mock()
        p.worker(pipe, {}, [], {}, True)
        pipe.send.assert_called_once_with(dict(kind='error', value='semantic_inventory_write_forbidden'))

    def test_virtual_child_null_hwnd_is_not_a_native_window(self):
        element = Mock(CurrentProcessId=7, CurrentClassName='mmui::XOutlineButton',
                       CurrentControlType=50000, CurrentNativeWindowHandle=None, CurrentIsEnabled=True)
        element.GetRuntimeId.return_value = [7, 1]
        element.GetCurrentPropertyValue.return_value = False
        result = p.element_metadata(element, self.uia(), 7)
        self.assertEqual(result['native_hwnd'], 0)
        self.assertFalse(result['operational_support_verified'])
