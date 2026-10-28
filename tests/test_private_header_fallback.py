"""Synthetic current-client private title fallback, no native UIA imports."""
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock,patch

from wechat_agent_bridge.adapters.windows_uia import WindowsUIA


class PrivateHeaderFallbackTests(unittest.TestCase):
    def test_unverified_input_text_pattern_is_never_read_as_a_label(self):
        label='synthetic-private';edit=Mock()
        edit.element_info=SimpleNamespace(automation_id='chat_input_field',name='unrelated')
        edit.window_text.side_effect=AssertionError('Draft TextPattern must not be read')
        root=Mock()
        root.descendants.side_effect=lambda **q:[edit] if q==dict(control_type='Edit') else []
        result=WindowsUIA()._target_metadata(root,[label])
        self.assertFalse(result['identity_verified']);edit.window_text.assert_not_called();edit.get_value.assert_not_called()

    def test_failure_diagnostics_distinguish_name_from_text_without_authorizing_or_reading_draft(self):
        label='synthetic-private'
        edit=Mock();edit.window_text.return_value=''
        edit.element_info=SimpleNamespace(automation_id='chat_input_field')
        header=Mock();header.window_text.return_value='';header.descendants.return_value=[]
        header.element_info=SimpleNamespace(name=label)
        row=Mock();row.element_info=SimpleNamespace(automation_id='session_item_other')
        root=Mock()
        def descendants(**query):
            if query==dict(control_type='Edit'):return [edit]
            if query==dict(control_type='ListItem'):return [row]
            if query==dict(class_name='mmui::ChatTitleBarChatSingleView'):return [header]
            if query==dict(class_name='mmui::ChatTitleBarChatRoomView'):return []
            raise AssertionError('unexpected_tree_query')
        root.descendants.side_effect=descendants
        result=WindowsUIA()._target_metadata(root,[label])
        self.assertFalse(result['identity_verified'])
        self.assertEqual(result['header_matches'],[False])
        self.assertEqual(result['header_name_matches'],[True])
        self.assertEqual(result['header_class_counts'],[0,1])
        self.assertEqual(result['session_candidate_counts'],[0])
        edit.get_value.assert_not_called();row.is_selected.assert_not_called()
        self.assertNotIn(label,str(result));self.assertNotIn('other',str(result))
    def test_empty_input_label_uses_exact_single_chat_header_and_selected_row(self):
        label='synthetic-private'; seen=[]
        edit=Mock(); edit.window_text.return_value=''; edit.get_value.return_value=''
        edit.element_info=SimpleNamespace(automation_id='chat_input_field',runtime_id=[1],element=object())
        header=Mock(); header.window_text.return_value=label; header.descendants.return_value=[]
        row=Mock(); row.element_info=SimpleNamespace(automation_id='session_item_'+label,runtime_id=[2])
        row.is_selected.return_value=True
        button=Mock(); button.window_text.return_value='发送'
        container=Mock(); container.descendants.return_value=[button]
        def descendants(**query):
            seen.append(query)
            if query==dict(control_type='Edit'): return [edit]
            if query==dict(control_type='ListItem'): return [row]
            if query==dict(class_name='mmui::ChatTitleBarChatSingleView'): return [header]
            if query==dict(class_name='mmui::ChatTitleBarChatRoomView'): return []
            if query==dict(class_name='mmui::ChatInputView'): return [container]
            raise AssertionError('unexpected_tree_query')
        root=Mock(); root.descendants.side_effect=descendants
        modules={'pywinauto':SimpleNamespace(),'pywinauto.uia_defines':SimpleNamespace(
            get_elem_interface=lambda *a:SimpleNamespace(CurrentIsReadOnly=False))}
        with patch.dict(sys.modules,modules):
            result=WindowsUIA()._target_metadata(root,[label])
        self.assertTrue(result['identity_verified'])
        self.assertTrue(result['draft_empty'])
        self.assertEqual(result['target_index'],0)
        self.assertEqual(result['session_runtime_id'],[2])
        self.assertIn(dict(class_name='mmui::ChatTitleBarChatSingleView'),seen)
        edit.get_value.assert_called_once()
