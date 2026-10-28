from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts import own_navigation_metadata as metadata
from wechat_agent_bridge.console_models import ConsoleError


UIA = SimpleNamespace(TreeScope_Descendants=4, TreeScope_Children=2, UIA_ClassNamePropertyId=10,
                      UIA_IsInvokePatternAvailablePropertyId=11, UIA_IsSelectionItemPatternAvailablePropertyId=12,
                      UIA_IsLegacyIAccessiblePatternAvailablePropertyId=13,
                      UIA_ControlTypePropertyId=14, UIA_ButtonControlTypeId=50000, UIA_ImageControlTypeId=50006)
FP = dict(pid=77, hwnd=101)


class Nodes:
    def __init__(self, values): self.values, self.Length = values, len(values)
    def GetElement(self, index): return self.values[index]


class Node:
    def __init__(self, rid, cls='synthetic::Widget', children=(), pid=77):
        self.rid, self.children = rid, list(children)
        self.CurrentProcessId, self.CurrentClassName, self.CurrentControlType = pid, cls, 50033
        self.CurrentIsEnabled, self.CurrentIsOffscreen = True, False
        self.pattern_value, self.child_reads = True, 0
    def GetRuntimeId(self): return self.rid
    def GetCurrentPropertyValue(self, prop):
        if prop not in (11, 12, 13): raise AssertionError('Non-pattern property read')
        return self.pattern_value
    def FindAll(self, scope, condition):
        if scope != UIA.TreeScope_Children or condition != 'true': raise AssertionError('Unexpected traversal')
        self.child_reads += 1
        return Nodes(self.children)
    @property
    def CurrentName(self): raise AssertionError('Name must never be read')
    @property
    def CurrentAutomationId(self): raise AssertionError('AutomationId must never be read')


class OwnNavigationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.child = Node([2])
        self.nav = Node([1], metadata.NAV, [self.child])
        self.root = SimpleNamespace(CurrentProcessId=77, CurrentNativeWindowHandle=101,
                                    CurrentClassName='mmui::MainWindow', FindAll=Mock(return_value=Nodes([self.nav])))
        self.raw = Mock()
        self.raw.CreateTrueCondition.return_value = 'true'
    def collect(self): return metadata.collect(self.root, self.raw, UIA, FP)

    def test_only_fixed_navigation_structure_is_collected_without_text_or_actions(self):
        report = self.collect()
        self.assertEqual([n['path'] for n in report['nodes']], [[], [0]])
        self.assertIs(report['names_or_text_or_values_read'], False)
        self.assertIs(report['action_executed'], False)
        self.assertIs(report['own_avatar_identity_verified'], False)
        self.raw.CreatePropertyCondition.assert_called_once_with(UIA.UIA_ClassNamePropertyId, metadata.NAV)
    def test_no_descendants_below_two_levels_are_touched(self):
        bomb = Node([4])
        bomb.GetRuntimeId = Mock(side_effect=AssertionError('Too deep'))
        depth_two = Node([3], children=[bomb])
        self.child.children = [depth_two]
        self.assertEqual(len(self.collect()['nodes']), 3)
        self.assertEqual(depth_two.child_reads, 0)
    def test_unknown_root_or_ambiguous_navigation_is_rejected(self):
        self.root.CurrentProcessId = 99
        with self.assertRaises(ConsoleError): self.collect()
        self.root.CurrentProcessId = 77
        self.root.FindAll.return_value = Nodes([self.nav, self.nav])
        with self.assertRaises(ConsoleError): self.collect()
    def test_cycle_and_foreign_process_child_are_rejected(self):
        self.child.children = [self.nav]
        with self.assertRaises(ConsoleError): self.collect()
        self.child.children = []
        self.child.CurrentProcessId = 99
        with self.assertRaises(ConsoleError): self.collect()
    def test_child_limits_and_runtime_changes_are_rejected(self):
        self.nav.children = [Node([i+10]) for i in range(33)]
        with self.assertRaises(ConsoleError): self.collect()
        self.nav.children = [self.child]
        self.child.GetRuntimeId = Mock(side_effect=[[2], [99]])
        with self.assertRaises(ConsoleError): self.collect()
    def test_unknown_pattern_status_does_not_claim_a_capability(self):
        self.child.pattern_value = 2
        report = self.collect()
        self.assertEqual(report['nodes'][1]['patterns'], {'Invoke': None, 'SelectionItem': None, 'LegacyIAccessible': None})

    def test_main_view_inspects_only_direct_child_metadata(self):
        child = Node([10], 'mmui::XAvatarImage', [Node([11])])
        view = Node([9], metadata.MAIN_VIEW, [child])
        self.root.FindAll.return_value = Nodes([view])
        result = metadata.main_view_children(self.root, self.raw, UIA, FP)
        self.assertEqual(result['children'][0]['class_name'], 'mmui::XAvatarImage')
        self.assertFalse(result['avatar_identity_verified'])
        self.assertEqual(child.child_reads, 0)

    def test_absent_main_view_is_no_avatar_identity_proof(self):
        self.root.FindAll.return_value = Nodes([])
        result = metadata.main_view_children(self.root, self.raw, UIA, FP)
        self.assertEqual(result['main_view_count'], 0)
        self.assertFalse(result['avatar_identity_verified'])

    def test_ambiguous_or_changed_main_view_is_rejected(self):
        view = Node([9], metadata.MAIN_VIEW, pid=99)
        self.root.FindAll.return_value = Nodes([view])
        with self.assertRaises(ConsoleError): metadata.main_view_children(self.root, self.raw, UIA, FP)
        self.root.FindAll.return_value = Nodes([view, view])
        with self.assertRaises(ConsoleError): metadata.main_view_children(self.root, self.raw, UIA, FP)


if __name__ == '__main__': unittest.main()
