from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts import own_avatar_msaa as msaa
from tests.test_own_navigation_metadata import Node, Nodes, FP
from wechat_agent_bridge.console_models import ConsoleError


class NativeMSAATests(unittest.TestCase):
    def setUp(self):
        self.node = Node([42, 101, 4, 8], 'mmui::MainTabBar')
        self.node.CurrentControlType = 50021
        self.pattern = Mock()
        self.pattern.QueryInterface.return_value = self.pattern
        self.node.GetCurrentPattern = Mock(return_value=self.pattern)
        self.record = dict(class_name='mmui::MainTabBar', control_type=50021,
                           runtime_id=list(self.node.rid), patterns={'LegacyIAccessible': True})
        self.uia = SimpleNamespace(UIA_LegacyIAccessiblePatternId=10018, IUIAutomationLegacyIAccessiblePattern=object())
    def probe(self): return msaa.probe(self.node, self.record, FP, self.uia)

    def test_null_native_interface_is_not_inferred_from_pattern_available(self):
        self.pattern.GetIAccessible.return_value = None
        result = self.probe()
        self.assertEqual(result['status'], 'native_interface_null')
        self.assertFalse(result['interface_acquired'])
        self.pattern.GetIAccessible.assert_called_once()

    def test_available_interface_is_only_presence_never_read_or_action(self):
        class Interface:
            def __getattr__(self, name): raise AssertionError('No MSAA content or action access: ' + name)
        self.pattern.GetIAccessible.return_value = Interface()
        result = self.probe()
        self.assertEqual(result['status'], 'native_interface_available')
        self.assertTrue(result['interface_acquired'])
        self.assertFalse(result['action_executed'])

    def test_other_class_or_missing_legacy_declaration_is_not_queried(self):
        for change, status in (({'class_name': 'synthetic::Chat'}, 'outside_fixed_container_scope'),
                               ({'patterns': {'LegacyIAccessible': False}}, 'legacy_pattern_not_declared')):
            self.record.update(change)
            self.assertEqual(self.probe()['status'], status)
            self.node.GetCurrentPattern.assert_not_called()
            self.record['class_name'] = 'mmui::MainTabBar'

    def test_pid_or_runtime_change_is_rejected_before_query(self):
        self.node.CurrentProcessId = 99
        with self.assertRaises(ConsoleError): self.probe()
        self.node.GetCurrentPattern.assert_not_called()
        self.node.CurrentProcessId = 77
        self.node.GetRuntimeId = Mock(side_effect=[[42,101,4,8], [42,101,4,99]])
        with self.assertRaises(ConsoleError): self.probe()
        self.pattern.GetIAccessible.assert_not_called()

    def test_interface_error_keeps_only_numeric_hresult_not_server_text(self):
        error = RuntimeError('synthetic private server text')
        error.hresult = -2147467262
        self.pattern.GetIAccessible.side_effect = error
        result = self.probe()
        self.assertEqual(result['status'], 'interface_query_failed')
        self.assertEqual(result['hresult'], -2147467262)
        self.assertNotIn('private', str(result))

    def test_recycled_container_after_interface_query_is_rejected(self):
        def query():
            self.node.rid = [99]
            return object()
        self.pattern.GetIAccessible.side_effect = query
        with self.assertRaises(ConsoleError): self.probe()

    def test_fixed_container_probe_needs_no_geometry_or_text(self):
        root = SimpleNamespace(CurrentProcessId=77, CurrentNativeWindowHandle=101,
                               CurrentClassName='mmui::MainWindow')
        pane = Node([42, 202], 'MMUIRenderSubWindowHW')
        pane.CurrentControlType = 50033
        pane.GetCurrentPattern = self.node.GetCurrentPattern
        self.pattern.GetIAccessible.return_value = None
        raw = Mock()
        raw.CreatePropertyCondition.side_effect = lambda key, value: value
        root.FindAll = Mock(side_effect=lambda scope, cls: Nodes([pane] if cls=='MMUIRenderSubWindowHW' else [self.node]))
        self.uia.TreeScope_Descendants = 4
        self.uia.UIA_ClassNamePropertyId = 30012
        self.uia.UIA_IsLegacyIAccessiblePatternAvailablePropertyId = 13
        result = msaa.collect_fixed_containers(root, raw, self.uia, FP)
        self.assertEqual(len(result['containers']), 2)
        self.assertFalse(result['normal_geometry_required'])
        self.assertEqual(self.pattern.GetIAccessible.call_count, 2)
        self.assertTrue(all(r['native_msaa']['status']=='native_interface_null' for r in result['containers']))

    def test_nonunique_fixed_container_is_not_queried(self):
        root = SimpleNamespace(CurrentProcessId=77, CurrentNativeWindowHandle=101,
                               CurrentClassName='mmui::MainWindow', FindAll=Mock(return_value=Nodes([self.node,self.node])))
        self.uia.TreeScope_Descendants = 4
        self.uia.UIA_ClassNamePropertyId = 30012
        result = msaa.collect_fixed_containers(root, Mock(), self.uia, FP)
        self.assertTrue(all(r['native_msaa']['status']=='container_not_unique_or_unavailable' for r in result['containers']))
        self.node.GetCurrentPattern.assert_not_called()


if __name__ == '__main__': unittest.main()
