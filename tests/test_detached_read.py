import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from tests import test_bound_windows_console as fixture_module
from wechat_agent_bridge.adapters.detached_read import require_selection, check_metadata, metadata
from wechat_agent_bridge.console_models import ConsoleError


class DetachedValidationTests(unittest.TestCase):
    def test_exact_root_id_and_title_not_generic_window_name_control_identity(self):
        uia=SimpleNamespace(TreeScope_Descendants=4,UIA_AutomationIdPropertyId=1,UIA_ControlTypePropertyId=2,
                            UIA_EditControlTypeId=3,UIA_ClassNamePropertyId=4)
        edit=SimpleNamespace(CurrentProcessId=7,GetRuntimeId=lambda:[42,102,4,-1])
        header=SimpleNamespace(CurrentProcessId=7,GetRuntimeId=lambda:[42,102,4,-2])
        title=SimpleNamespace(CurrentProcessId=7,CurrentName='synthetic')
        elements=iter([edit,header,title])
        def find(*args):
            item=next(elements);return SimpleNamespace(Length=1,GetElement=lambda i:item)
        root=SimpleNamespace(CurrentProcessId=7,CurrentNativeWindowHandle=102,
            CurrentAutomationId='ChatSingleWindow12345678@chatroom',CurrentName='generic window name',
            GetRuntimeId=lambda:[42,102],FindAll=find)
        client=Mock();client.element_from_handle.return_value=root
        value=metadata(client,uia,7,102,root.CurrentAutomationId,'synthetic')
        self.assertEqual(value['input_runtime_id'],[42,102,4,-1])
        client.require_background_mode.assert_called_once()
        elements=iter([edit,header,title]);title.CurrentName='different'
        with self.assertRaisesRegex(ConsoleError,'title_changed'):
            metadata(client,uia,7,102,root.CurrentAutomationId,'synthetic')
        root.CurrentAutomationId='ChatSingleWindow87654321@chatroom'
        with self.assertRaisesRegex(ConsoleError,'automationid_changed'):
            metadata(client,uia,7,102,'ChatSingleWindow12345678@chatroom','synthetic')

    def test_selection_requires_different_window_and_exact_client_group_root(self):
        require_selection(101,102,'ChatSingleWindow12345678@chatroom')
        for hwnd,root in ((101,'ChatSingleWindow12345678@chatroom'),(True,'ChatSingleWindow12345678@chatroom'),
                          (102,'Synthetic group'),(102,'ChatSingleWindowwxid_synthetic'),(0,'ChatSingleWindow12345678@chatroom')):
            with self.assertRaises(ConsoleError):require_selection(101,hwnd,root)

    def test_every_identity_component_must_stay_bound(self):
        data=dict(window=102,pid=7,automation_id='ChatSingleWindow12345678@chatroom',root_runtime_id=[1],
                  input_runtime_id=[2],header_runtime_id=[3])
        check_metadata(data,data)
        for key in data:
            with self.assertRaises(ConsoleError):check_metadata(data,{**data,key:None})

    def test_detached_read_is_independent_of_source_chat_selection(self):
        # Reuse only the fixture setup, not inherited tests.
        fixture=fixture_module.BoundWindowsConsoleTests();fixture.setUp()
        try:
            owner=fixture.owner;owner.console_port=fixture.bind()
            meta=dict(window=102,pid=7,automation_id='ChatSingleWindow12345678@chatroom',root_runtime_id=[42,102],
                      input_runtime_id=[42,102,4,-21],header_runtime_id=[42,102,4,-22])
            selected=[True]
            def call(method,*args):
                if method=='current_group_target':
                    if not selected[0]:raise AssertionError('source chat was revisited')
                    return dict(fixture.expected)
                if method=='detached_group_metadata':return dict(meta)
                if method=='detached_group_capture':return dict(list_runtime_id=[42,102,4,-23],rows=[
                    dict(runtime_id=[42,102,4,-24],label='synthetic detached text',class_name='mmui::ChatTextItemView')])
                raise AssertionError(method)
            owner._call=Mock(side_effect=call)
            owner.bind_read_channel('sender','group',102,meta['automation_id'])
            selected[0]=False
            self.assertEqual(owner.messages('sender','group').messages[0].text,'synthetic detached text')
            self.assertTrue(owner.channel_status('sender','group')['independent_read'])
            self.assertFalse(owner.channel_status('sender','group')['send_ready'])
            self.assertFalse(owner.send('sender','group','test','r')['attempted'])
            meta['input_runtime_id']=[999]
            with self.assertRaises(ConsoleError):owner.messages('sender','group')
        finally:fixture.doCleanups()
