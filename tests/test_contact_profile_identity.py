import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts import contact_profile_identity as identity
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from wechat_agent_bridge.console_models import ConsoleError


class Node:
    def __init__(self,rid,text='',children=(),pid=77):
        self.handle=202
        self.element_info=SimpleNamespace(runtime_id=[rid],process_id=pid)
        self._text,self._children=text,list(children)
    def window_text(self): return self._text
    def children(self): return self._children


class ContactIdentityTests(unittest.TestCase):
    expected='wxid_synthetic_private'
    def backend(self):
        header=Node(3); header.element_info.class_name=identity.PRIVATE_HEADER
        row=Node(4);row.element_info.automation_id='session_item_synthetic';row.is_selected=lambda:True
        root=Mock();root.handle=101;root.element_info=SimpleNamespace(process_id=77,class_name='mmui::MainWindow')
        root.descendants.side_effect=lambda **q:([header] if q.get('class_name')==identity.PRIVATE_HEADER else
                                                [row] if q.get('control_type')=='ListItem' else [])
        backend=Mock(); backend.bind_window.return_value='bound'; backend.instances={'bound':(root,'synthetic')}
        backend.accounts={'unchanged-sender':object()}
        backend._own_profile_popup.return_value=Node(1,children=[Node(2,'微信号：'+self.expected)])
        backend.target_control_probe.return_value=dict(identity_verified=True,target_index=0,
            selected_row_verified=True,draft_empty=True,value_readonly=False,input_runtime_id=[1],session_runtime_id=[4])
        return backend
    def test_labeled_exact_id_is_required_not_nickname_or_embedded_text(self):
        for values in ([self.expected],['昵称：'+self.expected],['备注 微信号：'+self.expected],
                       ['微信号：different_account'],['微信号：'+self.expected,'微信号：different_account']):
            with self.assertRaises(ConsoleError): identity.labeled_identity(values,self.expected)
        identity.labeled_identity(['微信号：'+self.expected],self.expected)
        identity.labeled_identity(['微信号：',self.expected],self.expected)
        with self.assertRaises(ConsoleError): identity.labeled_identity(['微信号：'],self.expected)
    def test_private_profile_never_replaces_sender_or_reads_messages(self):
        backend=self.backend(); original=backend.accounts.copy()
        result=identity.inspect(backend,77,101,'synthetic',self.expected)
        self.assertEqual(result['state'],'contact_profile_verified')
        self.assertEqual(backend.accounts,original)
        backend.verify_own_profile.assert_not_called()
        backend.messages.assert_not_called()
        self.assertFalse(result['account_binding_modified'])
        self.assertFalse(result['persistent_conversation_id_verified'])
        self.assertNotIn(self.expected,str(result))
    def test_invalid_full_target_or_draft_stops_after_exact_card_without_messages(self):
        for change in ({'draft_empty':False},{'identity_verified':False},{'selected_row_verified':False},
                       {'target_index':1},{'value_readonly':True},{'session_runtime_id':[]}):
            backend=self.backend(); backend.target_control_probe.return_value.update(change)
            with self.assertRaises(ConsoleError): identity.inspect(backend,77,101,'synthetic',self.expected)
            self.assertTrue(backend._own_profile_popup.called)
            backend.messages.assert_not_called()
        backend=self.backend(); backend.instances['bound'][0].descendants.side_effect=None
        backend.instances['bound'][0].descendants.return_value=[Mock()]
        with self.assertRaisesRegex(ConsoleError,'private_header'): identity.inspect(backend,77,101,'synthetic',self.expected)
    def test_foreign_process_cycle_and_limits_stop(self):
        for card in (Node(1,'微信号：'+self.expected,pid=88),Node(1,'x'*4097),
                     Node(1,children=[Node(i+2) for i in range(31)])):
            with self.assertRaises(ConsoleError): identity.read_card(card,77,self.expected)
        card=Node(1); card._children=[card]
        with self.assertRaisesRegex(ConsoleError,'identity_changed'): identity.read_card(card,77,self.expected)
    def test_target_change_between_reads_is_rejected(self):
        backend=self.backend(); before=backend.target_control_probe.return_value
        backend.target_control_probe.side_effect=[before,{**before,'session_runtime_id':[3]}]
        with self.assertRaisesRegex(ConsoleError,'profile_or_target_changed'):
            identity.inspect(backend,77,101,'synthetic',self.expected)
    def test_wrong_or_rebuilt_private_header_is_rejected(self):
        for change in ({'process_id':88},{'class_name':'mmui::ChatTitleBarContactView'},{'runtime_id':[]}):
            backend=self.backend(); root=backend.instances['bound'][0]
            header=root.descendants(class_name=identity.PRIVATE_HEADER)[0]
            for name,value in change.items(): setattr(header.element_info,name,value)
            with self.assertRaisesRegex(ConsoleError,'header_identity_changed'):
                identity.inspect(backend,77,101,'synthetic',self.expected)
        backend=self.backend(); root=backend.instances['bound'][0]; calls=[0]
        row=root.descendants(control_type='ListItem')[0]
        def headers(**q):
            if q.get('control_type')=='ListItem':return [row]
            if q.get('class_name')!=identity.PRIVATE_HEADER: return []
            calls[0]+=1; header=Node(3 if calls[0]==1 else 4)
            header.element_info.class_name=identity.PRIVATE_HEADER
            return [header]
        root.descendants.side_effect=headers
        with self.assertRaisesRegex(ConsoleError,'profile_or_target_changed'):
            identity.inspect(backend,77,101,'synthetic',self.expected)

    def test_wrong_exact_id_or_incomplete_card_never_reads_draft(self):
        for card in (Node(1,'微信号：another_account'),Node(1,'微信号：')):
            backend=self.backend();backend._own_profile_popup.return_value=card
            backend.target_control_probe.side_effect=AssertionError('draft_read_before_exact_id')
            with self.assertRaises(ConsoleError):identity.inspect(backend,77,101,'synthetic',self.expected)
            backend.target_control_probe.assert_not_called()

    def test_input_after_card_read_stops_before_draft(self):
        backend=self.backend()
        check=Mock(side_effect=[None,ConsoleError('contact_human_takeover')])
        with self.assertRaisesRegex(ConsoleError,'human_takeover'):
            identity.inspect(backend,77,101,'synthetic',self.expected,checkpoint=check)
        backend.target_control_probe.assert_not_called()

    def test_two_exact_card_reads_precede_any_draft_metadata(self):
        backend=self.backend();order=[];card=backend._own_profile_popup.return_value
        original=card.window_text
        card.window_text=lambda:order.append('card') or original()
        target=backend.target_control_probe.return_value
        backend.target_control_probe.side_effect=lambda *a:order.append('draft') or target
        identity.inspect(backend,77,101,'synthetic',self.expected)
        self.assertEqual(order,['card','card','draft','draft','card'])

    def test_wrong_selected_row_never_reads_card_or_draft(self):
        backend=self.backend();row=backend.instances['bound'][0].descendants(control_type='ListItem')[0]
        row.is_selected=lambda:False
        with self.assertRaisesRegex(ConsoleError,'selection_unverified'):
            identity.inspect(backend,77,101,'synthetic',self.expected)
        backend._own_profile_popup.assert_not_called()
        backend.target_control_probe.assert_not_called()
    def test_popup_replacement_is_rejected(self):
        backend=self.backend(); first=backend._own_profile_popup.return_value
        backend._own_profile_popup.side_effect=[first,Node(99,'微信号：'+self.expected)]
        with self.assertRaisesRegex(ConsoleError,'profile_or_target_changed'):
            identity.inspect(backend,77,101,'synthetic',self.expected)
    def test_visual_scope_cannot_bind_another_process_or_contact(self):
        fp={'hwnd':101,'created':42}
        proof=dict(kind='development_cua_private_identity',identity_verified=True,source='computer_use_profile_card',
            window_id=101,binding_reference=fp,expected_id_sha256=identity.sha(self.expected),
            label_sha256=identity.sha('synthetic'),send_invoked=False,api_called=False)
        identity.validate_visual_evidence(proof,fp,self.expected,'synthetic')
        for change in ({'window_id':102},{'identity_verified':False},{'send_invoked':True},
                       {'binding_reference':{'hwnd':101,'created':43}},{'expected_id_sha256':'wrong'}):
            with self.assertRaises(ConsoleError): identity.validate_visual_evidence({**proof,**change},fp,self.expected,'synthetic')
    def test_private_baseline_needs_exact_identity_and_rechecks_before_protection(self):
        verified=identity.inspect(self.backend(),77,101,'synthetic',self.expected)
        value=dict(historical=True,coverage='accessible_loaded_tail_only',
            rows=[dict(runtime_id=[42,101,4,-100],label='synthetic old message')])
        collect=Mock(return_value=value); protect=Mock(return_value=dict(protected_capture='synthetic.dpapi'))
        for bad in ({**verified,'state':'failed'},{**verified,'identity':{'exact_id_verified':False}},
                    {**verified,'account_binding_modified':True}):
            with self.assertRaisesRegex(ConsoleError,'exact_identity_required'):
                identity.private_baseline(bad,collect,protect,lambda:verified)
        collect.assert_not_called(); protect.assert_not_called()
        with self.assertRaisesRegex(ConsoleError,'identity_changed'):
            identity.private_baseline(verified,collect,protect,lambda:{**verified,'label_sha256':'changed'})
        protect.assert_not_called()
        result=identity.private_baseline(verified,collect,protect,lambda:verified)
        self.assertFalse(result['upload_eligible']); self.assertTrue(result['reverified_before_save'])
        self.assertEqual(len(result['baseline_event_hashes']),1)
        self.assertNotIn('synthetic old message',str(result))
        with self.assertRaisesRegex(ConsoleError,'coverage_unknown'):
            identity.private_baseline(verified,lambda:{**value,'historical':False},protect,lambda:verified)
    def test_readonly_foreground_allowed_but_focus_clipboard_or_lock_change_is_not(self):
        from dataclasses import replace
        before=DesktopSnapshot(101,102,3,target_active=True)
        identity.unchanged_readonly(before,before)
        for change in ({'foreground':103},{'focus':103},{'clipboard_sequence':4},{'locked':True},{'target_active':False}):
            with self.assertRaises(ConsoleError): identity.unchanged_readonly(before,replace(before,**change))

    def recovery_backend(self):
        backend=self.backend();root=backend.instances['bound'][0]
        root.handle=101;root.element_info=SimpleNamespace(process_id=77,class_name='mmui::MainWindow')
        expected=dict(input_runtime_id=[1],session_runtime_id=[4],private_header_runtime_id=[3])
        return backend,expected

    def test_recovery_wrong_exact_id_never_reads_draft_even_if_old_runtime_ids_match(self):
        backend,expected=self.recovery_backend()
        backend._own_profile_popup.return_value=Node(1,children=[Node(2,'微信号：different_account')])
        with self.assertRaisesRegex(ConsoleError,'id_mismatch'):
            identity.inspect_recovery_card(backend,77,101,'synthetic',self.expected,expected,Mock())
        backend.target_control_probe.assert_not_called()

    def test_recovery_current_card_precedes_draft_read_and_does_not_capture_history(self):
        backend,expected=self.recovery_backend();order=[]
        card=backend._own_profile_popup.return_value
        original_read=card.window_text
        card.window_text=lambda:order.append('card') or original_read()
        current=backend.target_control_probe.return_value
        backend.target_control_probe.side_effect=lambda *args:order.append('draft_metadata') or current
        result=identity.inspect_recovery_card(backend,77,101,'synthetic',self.expected,expected,Mock())
        self.assertEqual(order,['card','draft_metadata','card'])
        self.assertTrue(result['recipient_exact_id_reverified']);self.assertNotIn(self.expected,str(result))
        backend.messages.assert_not_called();backend.verify_own_profile.assert_not_called()

    def test_recovery_replaced_card_or_changed_header_or_takeover_cannot_complete(self):
        backend,expected=self.recovery_backend();card=backend._own_profile_popup.return_value
        backend._own_profile_popup.side_effect=[card,Node(9,'微信号：'+self.expected)]
        with self.assertRaisesRegex(ConsoleError,'card_changed'):
            identity.inspect_recovery_card(backend,77,101,'synthetic',self.expected,expected,Mock())
        backend,expected=self.recovery_backend();expected['private_header_runtime_id']=[8]
        with self.assertRaisesRegex(ConsoleError,'header_changed'):
            identity.inspect_recovery_card(backend,77,101,'synthetic',self.expected,expected,Mock())
        backend.target_control_probe.assert_not_called();backend._own_profile_popup.assert_not_called()
        backend,expected=self.recovery_backend();check=Mock(side_effect=[None,ConsoleError('human_takeover')])
        with self.assertRaisesRegex(ConsoleError,'human_takeover'):
            identity.inspect_recovery_card(backend,77,101,'synthetic',self.expected,expected,check)
        backend.target_control_probe.assert_not_called()
