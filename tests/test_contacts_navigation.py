import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import inspect_contacts_navigation as probe
from wechat_agent_bridge.console_models import ConsoleError


class ContactsNavigationTests(unittest.TestCase):
    def setUp(self):
        self.query=dict(query_kind='recipient_id',search_runtime_id=[42,101,9])
        self.node=Mock(CurrentProcessId=77,CurrentClassName='QRadioButton',CurrentControlType=50013,
            CurrentIsEnabled=True,CurrentIsOffscreen=False,CurrentAcceleratorKey='Ctrl+2',CurrentAccessKey='')
        self.node.GetRuntimeId.return_value=[42,101,8];self.node.GetCurrentPropertyValue.return_value=0
        matches=Mock(Length=1);matches.GetElement.return_value=self.node
        self.nav=Mock(CurrentProcessId=77,CurrentClassName=probe.NAV_CLASS);self.nav.GetRuntimeId.return_value=[42,101,7]
        self.nav.FindAll.return_value=matches
        roots=Mock(Length=1);roots.GetElement.return_value=self.nav
        self.root=Mock(CurrentProcessId=77,CurrentNativeWindowHandle=101,CurrentClassName='mmui::MainWindow');self.root.FindAll.return_value=roots
        self.raw=Mock();self.raw.CreatePropertyCondition.side_effect=lambda prop,value:value
        self.uia=SimpleNamespace(TreeScope_Descendants=4,UIA_ClassNamePropertyId=1,UIA_NamePropertyId=2,
            **{'UIA_Is'+name+'PatternAvailablePropertyId':10+i for i,name in enumerate(('Invoke','SelectionItem','Toggle','LegacyIAccessible'))})
    def collect(self):
        with patch.object(probe.readonly,'inspect_control',side_effect=[self.query,dict(self.query)]):
            return probe.collect(self.root,self.raw,self.uia,dict(pid=77,hwnd=101),'synthetic','wxid_synthetic')
    def test_defaults_are_disabled(self):
        with patch.object(probe.readonly,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(probe.main([]),0)
    def test_lookup_is_fixed_to_app_navigation_label_and_never_invokes(self):
        result=self.collect()
        self.assertEqual(self.root.FindAll.call_args.args[1],probe.NAV_CLASS)
        self.assertEqual(self.nav.FindAll.call_args.args[1],'通讯录')
        self.assertEqual(result['contacts_items'][0]['accelerator'],'Ctrl+2')
        self.assertFalse(result['action_executed']);self.assertFalse(result['navigation_verified'])
        self.node.GetCurrentPattern.assert_not_called();self.node.SetFocus.assert_not_called()
    def test_shortcut_data_never_admits_windows_keys_commands_or_unclassified_text(self):
        for value in ('Win+2','Meta+2','Ctrl+Enter','powershell','Ctrl+Alt+Delete','example secret',None):
            self.assertIsNone(probe.safe_shortcut(value))
        self.assertEqual(probe.safe_shortcut('Ctrl+Shift+2'),'Ctrl+Shift+2')
    def test_physical_geometry_converts_only_points_inside_the_known_left_navigation(self):
        point=probe.logical_navigation_point([144,152,1108,1138],[156,344,210,386],[144,184,229,1138],128)
        self.assertEqual(point['x'],29.25);self.assertEqual(point['y'],159.75)
        for item,nav,dpi in (([400,344,450,386],[144,184,229,1138],128),
            ([156,344,210,386],[144,184,900,1138],128),([156,344,210,386],[144,184,229,1138],0)):
            with self.assertRaises(ConsoleError):probe.logical_navigation_point([144,152,1108,1138],item,nav,dpi)
    def test_foreign_process_duplicate_container_or_rebuilt_node_stops(self):
        self.nav.CurrentProcessId=88
        with self.assertRaisesRegex(ConsoleError,'container_changed'):self.collect()
        self.setUp();self.root.FindAll.return_value.Length=2
        with self.assertRaisesRegex(ConsoleError,'container_ambiguous'):self.collect()
        self.setUp();self.node.GetRuntimeId.side_effect=[[1],[2]]
        with self.assertRaisesRegex(ConsoleError,'item_changed'):self.collect()
    def test_worker_dpi_context_restored_even_on_probe_failure(self):
        user=Mock();user.SetThreadDpiAwarenessContext.side_effect=[17,23]
        user.AreDpiAwarenessContextsEqual.return_value=True
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with probe.physical_coordinate_context(user):raise RuntimeError('synthetic')
        self.assertEqual([c.args[0] for c in user.SetThreadDpiAwarenessContext.call_args_list],[-4,17])
    def test_unavailable_or_unconfirmed_dpi_context_never_yields(self):
        user=Mock();user.SetThreadDpiAwarenessContext.return_value=0
        with self.assertRaisesRegex(ConsoleError,'unavailable'):
            with probe.physical_coordinate_context(user):self.fail('entered')
        user=Mock();user.SetThreadDpiAwarenessContext.side_effect=[17,23]
        user.AreDpiAwarenessContextsEqual.side_effect=[False,True]
        with self.assertRaisesRegex(ConsoleError,'unconfirmed'):
            with probe.physical_coordinate_context(user):self.fail('entered')
    def test_dpi_restore_failure_is_reported(self):
        user=Mock();user.SetThreadDpiAwarenessContext.side_effect=[17,0]
        user.AreDpiAwarenessContextsEqual.return_value=True
        with self.assertRaisesRegex(ConsoleError,'restore_failed'):
            with probe.physical_coordinate_context(user):pass
    def test_page_structure_uses_only_fixed_classes_and_never_requests_contact_values(self):
        self.node.CurrentClassName=probe.PAGE_CLASSES[0]
        roots=Mock(Length=1);roots.GetElement.return_value=self.node
        empty=Mock(Length=0)
        self.root.FindAll.side_effect=[roots,empty,empty]
        result=probe.page_structure(self.root,self.raw,self.uia,77)
        self.assertEqual(result[probe.PAGE_CLASSES[0]],dict(count=1,onscreen_count=1))
        self.assertEqual([x.args[1] for x in self.root.FindAll.call_args_list],list(probe.PAGE_CLASSES))
        self.node.GetCurrentPattern.assert_not_called()
