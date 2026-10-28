"""Native-port wiring tested with synthetic process and UI metadata only."""
from dataclasses import asdict, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_console import SyntheticProtector
from wechat_agent_bridge.adapters.windows import WindowsAdapter
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from wechat_agent_bridge.console_models import Account, Conversation, ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore


class BoundWindowsConsoleTests(unittest.TestCase):
    def setUp(self):
        self.store=ConsoleStore(':memory:',SyntheticProtector())
        self.addCleanup(self.store.close)
        self.fp=Fingerprint(7,101,123,'4.1.15.56','synthetic',1000,2000,10)
        self.owner=WindowsAdapter(gate_store=self.store)
        self.owner.enrolled_account=Account('sender','synthetic','synthetic_sender','7:101',
            fingerprint=asdict(self.fp),identity_source='uia_own_profile_exact_id_user_selected_window')
        self.owner.bound_window=dict(fingerprint=self.fp)
        self.owner.native_gate=Mock()
        self.owner.native_gate.fingerprint.return_value=self.fp
        record=dict(fingerprint=asdict(self.fp))
        self.owner.gate=SimpleNamespace(state='enabled',record=record)
        self.store.put('patch','pending',record)
        self.expected=dict(identity_verified=True,target_index=0,draft_empty=True,value_readonly=False,
            input_runtime_id=[42,101,4,-10],session_runtime_id=[42,101,4,-11],group_header_runtime_id=[42,101,4,-12])
        self.chat=Conversation('sender','group','Synthetic group','group',identity_source='synthetic_exact_group')
        self.guard=Mock()
        self.owner._call=Mock(side_effect=self.operation)
        self.snap=patch('wechat_agent_bridge.adapters.background.snapshot',return_value=DesktopSnapshot(1,2,3))
        self.snap.start(); self.addCleanup(self.snap.stop)
        self.locked=patch('wechat_agent_bridge.adapters.background.desktop_locked',return_value=False)
        self.locked_mock=self.locked.start();self.addCleanup(self.locked.stop)

    def operation(self,method,*args):
        if method=='current_group_target': return dict(self.expected)
        if method=='bound_group_capture':
            return dict(rows=[dict(runtime_id=[42,101,4,-20],label='synthetic content',
                class_name='mmui::ChatTextItemView')])
        raise AssertionError(method)

    def bind(self):
        return self.owner.bound_group_console(self.chat,self.expected,scope_guard=self.guard)

    def test_bound_reader_uses_only_selected_group_and_keeps_unknown_semantics(self):
        adapter=self.bind()
        self.assertEqual(adapter.discover(),[self.owner.enrolled_account])
        self.assertEqual(adapter.conversations('sender'),[self.chat])
        page=adapter.messages('sender','group')
        self.assertEqual(page.messages[0].text,'synthetic content')
        self.assertIsNone(page.messages[0].own)
        self.assertIsNone(page.messages[0].mentions_self)
        self.assertTrue(page.messages[0].historical)
        self.assertFalse(page.complete)
        self.assertTrue(all(c.args[0] in ('current_group_target','bound_group_capture')
                            for c in self.owner._call.call_args_list))
        self.assertEqual(adapter.send('sender','group','test','request')['attempted'],False)

    def test_private_or_unidentified_group_cannot_use_group_reader(self):
        for chat in (replace(self.chat,kind='private'),replace(self.chat,identity_source='')):
            with self.assertRaises(ConsoleError):
                self.owner.bound_group_console(chat,self.expected,scope_guard=self.guard)
        self.owner._call.assert_not_called()

    def test_changed_epoch_or_patch_owner_stops_before_any_content_read(self):
        adapter=self.bind()
        self.owner.native_gate.fingerprint.return_value=replace(self.fp,created=124)
        with self.assertRaises(ConsoleError): adapter.messages('sender','group')
        self.owner._call.assert_not_called()
        self.owner.native_gate.fingerprint.return_value=self.fp
        self.store.put('patch','pending',dict(other=True))
        with self.assertRaises(ConsoleError): adapter.messages('sender','group')
        self.owner._call.assert_not_called()

    def test_changed_selected_row_stops_before_capture(self):
        adapter=self.bind()
        for key,value in (('session_runtime_id',[999]),('group_header_runtime_id',[999])):
            self.owner._call=Mock(return_value={**self.expected,key:value})
            with self.assertRaises(ConsoleError): adapter.messages('sender','group')
            self.assertEqual([c.args[0] for c in self.owner._call.call_args_list],['current_group_target'])

    def test_foreground_wechat_and_draft_do_not_block_read(self):
        adapter=self.bind()
        self.expected['draft_empty']=False
        self.owner._call=Mock(side_effect=self.operation)
        with patch('wechat_agent_bridge.adapters.background.snapshot',return_value=DesktopSnapshot(101,4,9,True)):
            self.assertEqual(len(adapter.messages('sender','group').messages),1)
        self.assertEqual(adapter.inspect('sender','group')['has_draft'],True)

    def test_main_composer_is_not_a_concurrent_send_fallback(self):
        self.owner.console_port=self.bind()
        self.owner.console_port.sender=Mock()
        self.assertFalse(self.owner.channel_status('sender','group')['send_ready'])
        self.assertFalse(self.owner.send('sender','group','synthetic','request')['attempted'])
        self.owner.console_port.sender.assert_not_called()

    def test_reader_never_samples_global_focus_but_still_stops_at_lock_screen(self):
        adapter=self.bind()
        with patch('wechat_agent_bridge.adapters.background.snapshot',side_effect=AssertionError('global focus sampled')):
            self.assertEqual(len(adapter.messages('sender','group').messages),1)
        self.locked_mock.return_value=True
        with self.assertRaisesRegex(ConsoleError,'desktop_locked'):
            adapter.messages('sender','group')

    def arm_main(self):
        self.owner.trial_send_mode='main-window'
        self.owner.trial_evidence='synthetic-proof'
        self.owner.console_group={'target':dict(self.expected)}
        self.owner.console_port=self.bind()
        return self.owner.prepare_prefix_trial('sender','group',lambda:None)

    def test_explicit_main_sender_uses_same_build_recovery_and_scoped_original_port(self):
        with patch('scripts.probe_bound_thread_hook.require_recovery') as recovery, \
                patch('wechat_agent_bridge.adapters.console_send_trial.ConsoleSendTrial') as sender:
            sender.return_value.run.return_value=dict(status='ui_confirmed',attempted=True)
            self.assertTrue(self.arm_main()['send_ready'])
            recovery.assert_called_once_with('synthetic-proof',bound_send=True)
            self.assertEqual(self.owner.send('sender','group','synthetic','r')['status'],'ui_confirmed')
            sender.return_value.run.assert_called_once_with('synthetic','r',self.owner.console_send_checkpoint)
            with self.assertRaises(ConsoleError): self.owner.send('wrong','group','synthetic','r2')
            self.assertFalse(self.owner.channel_status('sender','group')['draft_isolation_verified'])

    def test_main_mode_requires_recovery_proof_and_rejects_detached_reader(self):
        with patch('scripts.probe_bound_thread_hook.require_recovery',side_effect=ConsoleError('proof_failed')):
            with self.assertRaisesRegex(ConsoleError,'proof_failed'): self.arm_main()
        self.assertIsNone(self.owner.console_port.sender)
        self.owner.detached_read_binding={'window':102}
        with self.assertRaisesRegex(ConsoleError,'main_window_send_requires_main_reader'): self.arm_main()

    def test_main_prewrite_check_never_writes_during_draft_or_foreground_use(self):
        with patch('scripts.probe_bound_thread_hook.require_recovery'), \
                patch('wechat_agent_bridge.adapters.console_send_trial.ConsoleSendTrial') as sender:
            self.arm_main()
            self.expected['draft_empty']=False
            with self.assertRaisesRegex(ConsoleError,'existing_draft_or_unknown'):
                self.owner.prepare_send('sender','group')
            self.expected['draft_empty']=True
            with patch('wechat_agent_bridge.adapters.background.snapshot',return_value=DesktopSnapshot(101,4,9,True)):
                with self.assertRaisesRegex(ConsoleError,'human_takeover'):
                    self.owner.prepare_send('sender','group')
            sender.return_value.run.assert_not_called()

    def test_bound_detached_window_is_not_an_account_but_other_same_pid_window_is_retained(self):
        account=self.owner.enrolled_account
        rows=[asdict(account)]+[asdict(replace(account,id=str(hwnd),instance_id='7:'+str(hwnd),
            identity_certain=False,wechat_id=None)) for hwnd in (102,103)]
        self.owner._call=Mock(return_value=rows)
        self.owner.detached_read_binding=dict(pid=7,window=102)
        self.assertEqual({a.instance_id for a in self.owner.discover()},{'7:101','7:103'})
        self.owner.native_gate.fingerprint.return_value=replace(self.fp,created=124)
        self.assertIn('7:102',{a.instance_id for a in self.owner.discover()})

    def test_revocation_from_binding_owner_stops_before_native_call(self):
        adapter=self.bind()
        self.guard.side_effect=ConsoleError('scope_revoked')
        with self.assertRaisesRegex(ConsoleError,'scope_revoked'): adapter.messages('sender','group')
        self.owner._call.assert_not_called()

    def test_service_explicit_group_binding_then_read_grant_populates_console(self):
        from wechat_agent_bridge.acceptance import AcceptanceLedger,AcceptanceSession
        from wechat_agent_bridge.console_service import ConsoleService
        ledger=AcceptanceLedger(':memory:');self.addCleanup(ledger.close)
        service=ConsoleService(self.owner,self.store,acceptance=AcceptanceSession(ledger),
            test_account='synthetic_sender',test_group=self.chat.name)
        service.accounts_cache={'sender':self.owner.enrolled_account}
        result=service.bind_current_group('sender')
        self.assertTrue(result['paused']);self.assertTrue(result['read_only'])
        self.assertFalse(service.grants)
        self.assertEqual(ledger.status()['api_used'],0)
        with self.assertRaisesRegex(ConsoleError,'read_not_authorized'):
            service.conversations('sender')
        service.authorize('sender',dict(read=True,upload=False,reply=False,paused=True))
        chat=service.conversations('sender')[0]
        service.sync('sender',chat['id'])
        rows=service.read_messages('sender',chat['id'])['messages']
        self.assertEqual(rows[0]['text'],'synthetic content')
        self.assertTrue(service.paused)
        with self.assertRaisesRegex(ConsoleError,'scope_already_bound'):service.bind_current_group('sender')

    def test_browser_cannot_bind_without_launch_scope_or_for_wrong_sender(self):
        from wechat_agent_bridge.acceptance import AcceptanceLedger,AcceptanceSession
        from wechat_agent_bridge.console_service import ConsoleService
        ledger=AcceptanceLedger(':memory:');self.addCleanup(ledger.close)
        service=ConsoleService(self.owner,self.store,acceptance=AcceptanceSession(ledger))
        service.accounts_cache={'sender':self.owner.enrolled_account}
        with self.assertRaisesRegex(ConsoleError,'startup_scope_required'):service.bind_current_group('sender')
        service.test_account='other_sender';service.test_group=self.chat.name
        with self.assertRaisesRegex(ConsoleError,'account_identity_uncertain'):service.bind_current_group('sender')
        self.owner._call.assert_not_called()

    def test_selected_verified_account_can_bind_explicit_group_without_fixed_account(self):
        from wechat_agent_bridge.acceptance import AcceptanceLedger,AcceptanceSession
        from wechat_agent_bridge.console_service import ConsoleService
        ledger=AcceptanceLedger(':memory:');self.addCleanup(ledger.close)
        service=ConsoleService(self.owner,self.store,acceptance=AcceptanceSession(ledger))
        service.accounts_cache={'sender':self.owner.enrolled_account}
        result=service.bind_current_group('sender',self.chat.name)
        self.assertEqual(result['conversation']['name'],self.chat.name)
        self.assertTrue(result['read_only'])
        self.assertTrue(service.paused)
        self.assertFalse(service.grants)
        self.assertEqual(ledger.status()['api_used'],0)

    def test_page_scope_does_not_override_explicit_launch_scope(self):
        from wechat_agent_bridge.acceptance import AcceptanceLedger,AcceptanceSession
        from wechat_agent_bridge.console_service import ConsoleService
        ledger=AcceptanceLedger(':memory:');self.addCleanup(ledger.close)
        service=ConsoleService(self.owner,self.store,acceptance=AcceptanceSession(ledger),
            test_account='synthetic_sender',test_group=self.chat.name)
        service.accounts_cache={'sender':self.owner.enrolled_account}
        with self.assertRaisesRegex(ConsoleError,'target_mismatch'):
            service.bind_current_group('sender','another group')
        self.owner._call.assert_not_called()
