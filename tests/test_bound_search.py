import contextlib
import copy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import inspect_bound_search as search
from wechat_agent_bridge.console_models import ConsoleError


class NoTextProperties(Mock):
    @property
    def CurrentName(self):raise AssertionError('Name would expose unrelated text')
    @property
    def CurrentHelpText(self):raise AssertionError('HelpText would expose unrelated text')


class SearchOnlyTests(unittest.TestCase):
    def setUp(self):
        self.fp=dict(pid=77,hwnd=101)
        self.pattern=Mock(CurrentValue='synthetic-label',CurrentIsReadOnly=False)
        self.node=NoTextProperties(CurrentProcessId=77,CurrentClassName=search.SEARCH_CLASS,
            CurrentControlType=50004,CurrentAutomationId='')
        self.node.GetRuntimeId.return_value=[42,101,9]
        self.node.GetCurrentPattern.return_value.QueryInterface.return_value=self.pattern
        self.matches=Mock(Length=1);self.matches.GetElement.return_value=self.node
        self.root=NoTextProperties(CurrentProcessId=77,CurrentNativeWindowHandle=101,CurrentClassName='mmui::MainWindow')
        self.root.FindAll.return_value=self.matches
        self.raw=Mock()
        self.uia=SimpleNamespace(UIA_ClassNamePropertyId=1,UIA_ControlTypePropertyId=2,
            UIA_EditControlTypeId=50004,TreeScope_Descendants=4,UIA_ValuePatternId=10002,IUIAutomationValuePattern=object())
    def inspect(self):return search.inspect_control(self.root,self.raw,self.uia,self.fp,'synthetic-label','wxid_synthetic')
    def test_default_entry_does_not_capture_or_connect_to_windows(self):
        with patch.object(search,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(search.main([]),0)
    def test_query_values_are_classified_and_never_exported(self):
        for value,kind in (('synthetic-label','display_label'),('wxid_synthetic','recipient_id'),('','empty'),('unrelated-query','other')):
            self.pattern.CurrentValue=value
            result=self.inspect()
            self.assertEqual(result['query_kind'],kind);self.assertEqual(result['query_length'],len(value))
            self.assertNotIn('CurrentValue',result);self.assertNotIn('unrelated-query',str(result))
            self.assertFalse(result['message_text_read']);self.assertFalse(result['chat_draft_read'])
            self.assertFalse(result['action_executed']);self.assertFalse(result['write_capability_verified'])
        self.pattern.SetValue.assert_not_called();self.node.SetFocus.assert_not_called()
    def test_ambiguous_control_or_wrong_root_never_requests_a_pattern(self):
        for count in (0,2,True):
            self.matches.Length=count
            with self.assertRaisesRegex(ConsoleError,'search_control_ambiguous'):self.inspect()
            self.node.GetCurrentPattern.assert_not_called()
        self.matches.Length=1;self.root.CurrentProcessId=78
        with self.assertRaisesRegex(ConsoleError,'root_identity_changed'):self.inspect()
        self.node.GetCurrentPattern.assert_not_called()
        self.root.CurrentProcessId=77;self.root.CurrentClassName='synthetic-login-dialog'
        with self.assertRaisesRegex(ConsoleError,'root_identity_changed'):self.inspect()
        self.node.GetCurrentPattern.assert_not_called()
    def test_chat_field_disguised_control_and_stale_runtime_are_rejected_before_value_read(self):
        for changes in (dict(CurrentAutomationId='chat_input_field'),dict(CurrentProcessId=88),
            dict(CurrentClassName='mmui::ChatInputField'),dict(CurrentControlType=50000)):
            self.setUp()
            for k,v in changes.items():setattr(self.node,k,v)
            with self.assertRaisesRegex(ConsoleError,'control_identity_changed'):self.inspect()
            self.node.GetCurrentPattern.assert_not_called()
        self.setUp();self.node.GetRuntimeId.side_effect=[[42,101,9],[42,101,10]]
        with self.assertRaisesRegex(ConsoleError,'control_identity_changed'):self.inspect()
        self.node.GetCurrentPattern.assert_not_called()
    def test_name_text_and_other_controls_are_never_requested(self):
        self.inspect()
        self.node.GetCurrentPattern.assert_called_once_with(10002)
        self.matches.GetElement.assert_called_once_with(0)
        self.assertNotIn('CurrentName',[c[0] for c in self.node.mock_calls])
        self.node.GetCurrentPropertyValue.assert_not_called();self.root.GetCurrentPattern.assert_not_called()
    def test_large_values_unknown_readonly_and_changed_runtime_fail_without_action(self):
        self.pattern.CurrentValue='q'*129
        with self.assertRaisesRegex(ConsoleError,'too_long'):self.inspect()
        self.setUp();self.pattern.CurrentIsReadOnly=None
        with self.assertRaisesRegex(ConsoleError,'readonly_state_unknown'):self.inspect()
        self.setUp();self.node.GetRuntimeId.side_effect=[[42,101,9],[42,101,9],[42,101,10]]
        with self.assertRaisesRegex(ConsoleError,'control_identity_changed'):self.inspect()
        self.pattern.SetValue.assert_not_called()
    def test_only_original_terminal_selection_and_its_lock_can_allow_recovery_inspection(self):
        token='a'*32
        pending=dict(fingerprint=self.fp,method='verified_private_SelectionItem_Select',phase='terminal_unknown',
            request='original',lock_token=token)
        attempt=dict(kind='development_private_semantic_selection',request_id='original',fingerprint=self.fp,
            state='unknown',worker_exited=True,patch_restored=True,cleanup_errors=[],send_invoked=False,draft_written=False)
        search.require_terminal_selection(pending,attempt,self.fp,token)
        for changed in ({'phase':'request_unknown'},{'method':'other'},{'lock_token':'b'*32},{'fingerprint':dict(pid=78,hwnd=101)}):
            with self.assertRaisesRegex(ConsoleError,'terminal_selection_lock_required'):
                search.require_terminal_selection({**pending,**changed},attempt,self.fp,token)
        for changed in ({'worker_exited':False},{'patch_restored':False},{'state':'failed'},
            {'draft_written':True},{'send_invoked':True},{'request_id':'different'}):
            with self.assertRaisesRegex(ConsoleError,'terminal_selection_lock_required'):
                search.require_terminal_selection(pending,{**attempt,**changed},self.fp,token)
