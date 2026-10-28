import contextlib
import copy
import io
import json
import hashlib
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import Mock,patch

from scripts import development_window_restore as restore
from scripts import probe_window_restore as synthetic
from scripts.probe_background_draft import RunLock
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore


class Port:
    def __init__(self):self.value=dict(visible=False,iconic=False);self.requests=0
    def state(self):return dict(self.value)
    def request(self):self.requests+=1;self.value=dict(visible=True,iconic=True);return True


class WindowRestoreTests(unittest.TestCase):
    @staticmethod
    def cua_ready_fixture():
        fp=dict(hwnd=101,created=7001,pid=55)
        report=dict(kind='development_cua_window_ready',binding_reference=fp,main_window_id=101,
            final_state='minimized_confirmed_by_computer_use',sender_visual_id_match=True,
            sender_id_sha256=hashlib.sha256(b'synthetic-sender').hexdigest(),private_label_visible=True,
            private_draft_visible_empty_before_minimize=True,own_profile_collapsed=True,
            later_window_list_included_target=True,launch_calls=0,text_input_calls=0,
            unexpected_preview=dict(opened=False),
            **{k:False for k in ('native_uia_used','send_invoked','api_called','process_patch_applied',
                'helper_loaded','account_binding_modified','background_qualified','production_enabled',
                'unrelated_conversation_intentionally_opened')})
        return fp,report
    def test_cua_transition_receipt_cannot_reopen_an_old_case_by_renaming_file(self):
        fp,report=self.cua_ready_fixture()
        payload=json.dumps(report).encode();digest=hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as d,patch.object(restore.original,'DIRECTORY',Path(d)):
            keys=[]
            for name in ('original.json','copied.json'):
                path=Path(d)/name;path.write_bytes(payload)
                loaded,proof_hash=restore.load_local_hashed(path)
                keys.append(restore.cua_minimized_case(loaded,fp,'synthetic-sender',proof_hash))
            self.assertEqual(keys[0],keys[1])
            self.assertTrue(keys[0].endswith(digest))
            self.assertNotEqual(keys[0],'normal-private-'+str(fp['created']))
    def test_cua_transition_requires_matching_identity_and_no_intervening_input(self):
        fp,report=self.cua_ready_fixture();digest='a'*64
        for change in ({'binding_reference':{**fp,'created':7002}},{'main_window_id':102},
            {'sender_id_sha256':'b'*64},{'final_state':'hidden'},{'private_label_visible':False},
            {'private_draft_visible_empty_before_minimize':False},{'own_profile_collapsed':False},
            {'send_invoked':True},{'text_input_calls':1},{'launch_calls':True},{'native_uia_used':True},
            {'background_qualified':True},{'later_window_list_included_target':False}):
            with self.subTest(change=change),self.assertRaisesRegex(ConsoleError,'cua_minimized_receipt_required'):
                restore.cua_minimized_case({**report,**change},fp,'synthetic-sender',digest)
    def test_cua_preview_must_be_closed_before_preparing_window(self):
        fp,report=self.cua_ready_fixture()
        report['unexpected_preview']=dict(opened=True,closed=False)
        with self.assertRaisesRegex(ConsoleError,'preview_cleanup_required'):
            restore.cua_minimized_case(report,fp,'synthetic-sender','a'*64)
        report['unexpected_preview']['closed']=True
        self.assertTrue(restore.cua_minimized_case(report,fp,'synthetic-sender','a'*64))
    def test_cua_hashed_receipt_rejects_external_path(self):
        with tempfile.TemporaryDirectory() as d,patch.object(restore.original,'DIRECTORY',Path(d)/'proofs'):
            path=Path(d)/'outside.json';path.write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ConsoleError,'local_evidence_required'):restore.load_local_hashed(path)
    def test_default_modes_never_access_windows(self):
        for module in (restore,synthetic):
            with patch.object(module,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main([]),0)
    def test_explicit_permission_and_hidden_state_required(self):
        for allowed,value in ((False,dict(visible=False,iconic=False)),(True,dict(visible=True,iconic=False))):
            port=Port();port.value=value;claim=Mock()
            result=restore.restore_transaction(port,Mock(),claim,allowed=allowed)
            self.assertEqual(result['state'],'failed');self.assertFalse(result['attempted'])
            claim.assert_not_called();self.assertEqual(port.requests,0)
    def test_one_request_after_durable_claim_and_full_state_readback(self):
        port=Port();events=[];action=port.request
        port.request=lambda:events.append('request') or action()
        result=restore.restore_transaction(port,Mock(),lambda before:events.append('claim'),allowed=True)
        self.assertEqual(events,['claim','request'])
        self.assertEqual(result['state'],'restored_minimized_for_development')
        self.assertFalse(result['background_qualified']);self.assertEqual(port.requests,1)
    def test_already_minimized_never_requests_again(self):
        port=Port();port.value=dict(visible=True,iconic=True);claim=Mock()
        self.assertEqual(restore.restore_transaction(port,Mock(),claim,allowed=True)['state'],'already_minimized')
        claim.assert_not_called();self.assertEqual(port.requests,0)
    def test_draft_unrelated_window_state_change_before_request_stops(self):
        port=Port()
        result=restore.restore_transaction(port,Mock(),lambda before:port.value.update(visible=True),allowed=True)
        self.assertEqual(result['state'],'failed');self.assertFalse(result['attempted']);self.assertEqual(port.requests,0)
    def test_request_not_started_or_timeout_remains_unknown_without_repeat(self):
        for mode in ('not_started','timeout','exception'):
            port=Port()
            def action():
                port.requests+=1
                if mode=='exception':raise OSError('synthetic')
                return mode!='not_started'
            port.request=action;clock=iter((0,11))
            result=restore.restore_transaction(port,Mock(),Mock(),allowed=True,clock=lambda:next(clock),sleep=lambda s:None)
            self.assertEqual(result['state'],'unknown');self.assertTrue(result['attempted']);self.assertEqual(port.requests,1)
    def test_takeover_focus_or_clipboard_change_after_request_discards_success(self):
        for reason in ('human_takeover','background_focus_changed','background_clipboard_changed'):
            port=Port();checkpoint=Mock(side_effect=[None,None,ConsoleError(reason)])
            result=restore.restore_transaction(port,checkpoint,Mock(),allowed=True)
            self.assertEqual(result['state'],'unknown');self.assertEqual(port.requests,1)
    def test_failed_claim_cannot_post_request(self):
        port=Port()
        result=restore.restore_transaction(port,Mock(),Mock(side_effect=OSError()),allowed=True)
        self.assertEqual(result['state'],'failed');self.assertFalse(result['attempted']);self.assertEqual(port.requests,0)
        checkpoint=Mock(side_effect=[None,ConsoleError('development_restore_identity_changed')])
        result=restore.restore_transaction(port,checkpoint,Mock(),allowed=True)
        self.assertEqual(result['state'],'failed');self.assertFalse(result['attempted']);self.assertEqual(port.requests,0)
    def test_native_port_binds_only_the_nonactivating_minimized_flag(self):
        class Function:
            def __init__(self,fn):self.fn=fn
            def __call__(self,*args):return self.fn(*args)
        calls=[]
        api=Mock()
        api.IsWindow=Function(lambda h:True);api.IsWindowVisible=Function(lambda h:False);api.IsIconic=Function(lambda h:False)
        api.ShowWindowAsync=Function(lambda *args:calls.append(args) or 1)
        port=restore.WindowPort(101,api)
        self.assertEqual(port.state(),dict(visible=False,iconic=False));self.assertTrue(port.request())
        self.assertEqual(calls,[(101,7)])
        self.assertTrue(restore.WindowPort(101,api,normal=True).request())
        self.assertEqual(calls[-1],(101,4))
    def test_normal_case_requires_a_minimized_window_and_uses_a_distinct_result(self):
        port=Port();claim=Mock()
        result=restore.restore_transaction(port,Mock(),claim,allowed=True,normal=True)
        self.assertEqual(result['state'],'failed');claim.assert_not_called();self.assertEqual(port.requests,0)
        port.value=dict(visible=True,iconic=True)
        def show():port.requests+=1;port.value=dict(visible=True,iconic=False);return True
        port.request=show
        result=restore.restore_transaction(port,Mock(),claim,allowed=True,normal=True)
        self.assertEqual(result['state'],'shown_normal_for_development');self.assertEqual(port.requests,1)
    def test_unknown_journal_keeps_global_lock_and_original_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            store=ConsoleStore(':memory:',SyntheticProtector())
            try:
                lock=RunLock(Path(d)/'lock');lock.acquire()
                pending=dict(key='one',lock_token=lock.token,phase='request_unknown')
                store.put('development_window_restore','pending',pending)
                report=dict(state='unknown',attempted=True,cleanup_errors=[])
                restore.finalize_journal(store,lock,'one',report,claimed=True,pending=None,inspecting=False)
                self.assertTrue(lock.path.exists())
                self.assertEqual(store.get('development_window_trials','one'),report)
                self.assertEqual(store.get('development_window_restore','pending')['phase'],'terminal_unknown')
                with self.assertRaises(ConsoleError):RunLock(lock.path).acquire()
            finally:store.close()
    def test_readonly_reconciliation_preserves_unknown_and_checks_own_lock_token(self):
        with tempfile.TemporaryDirectory() as d:
            store=ConsoleStore(':memory:',SyntheticProtector())
            try:
                lock=RunLock(Path(d)/'lock');lock.acquire()
                pending=dict(key='one',lock_token=lock.token,phase='terminal_unknown')
                store.put('development_window_restore','pending',pending)
                original=dict(state='unknown',attempted=True);store.put('development_window_trials','one',original)
                followup=dict(state='minimized_observed_after_unknown',cleanup_errors=[])
                recovered=RunLock(lock.path)
                with self.assertRaisesRegex(ConsoleError,'recovery_lock_changed'):
                    restore.finalize_journal(store,recovered,'one',followup,claimed=False,
                        pending={**pending,'lock_token':'wrong'},inspecting=True)
                self.assertTrue(lock.path.exists());self.assertEqual(store.get('development_window_restore','pending'),pending)
                restore.finalize_journal(store,recovered,'one',followup,claimed=False,pending=pending,inspecting=True)
                self.assertFalse(lock.path.exists());self.assertIsNone(store.get('development_window_restore','pending'))
                self.assertEqual(store.get('development_window_trials','one'),original)
            finally:store.close()
    def test_normal_unknown_reconciliation_only_releases_its_own_journal(self):
        with tempfile.TemporaryDirectory() as d:
            store=ConsoleStore(':memory:',SyntheticProtector())
            try:
                lock=RunLock(Path(d)/'lock');lock.acquire()
                pending=dict(key='normal-one',lock_token=lock.token,phase='terminal_unknown')
                store.put('development_window_restore','pending',pending)
                original=dict(state='unknown',attempted=True)
                store.put('development_window_trials','normal-one',original)
                restore.finalize_journal(store,RunLock(lock.path),'normal-one',
                    dict(state='normal_observed_after_unknown',cleanup_errors=[]),
                    claimed=False,pending=pending,inspecting=True)
                self.assertFalse(lock.path.exists())
                self.assertIsNone(store.get('development_window_restore','pending'))
                self.assertEqual(store.get('development_window_trials','normal-one'),original)
            finally:store.close()
    def test_synthetic_evidence_requires_current_sources_state_and_complete_cleanup(self):
        report=dict(kind='synthetic_window_restore',passed=True,contains_real_client=False,sources=restore.synthetic_sources(),
            sources_unchanged=True,os=platform.version(),result=dict(state='restored_minimized_for_development'),request_count=1,
            checkpoint_count=4,
            normal_result=dict(state='shown_normal_for_development'),normal_request_count=1,normal_visibility_verified=True,
            requester_owner_match=True,requester_distinct_process=True,requester_focus_unchanged=True,
            working_desktop_unchanged=True,local_focus_unchanged=True,cleanup=dict(terminated=False,child_exitcode=0,
                requester_exitcode=0,**{k:True for k in ('child_exited','window_destroyed','thread_desktop_restored','child_desktop_closed',
                    'parent_desktop_closed','desktop_absent','requester_exited','requester_thread_desktop_restored','requester_desktop_closed')}))
        with tempfile.TemporaryDirectory() as d,patch.object(restore.original,'DIRECTORY',Path(d)):
            path=Path(d)/'synthetic.json';path.write_text(json.dumps(report),encoding='utf-8')
            restore.require_synthetic(path)
            for change in ({'request_count':2},{'sources':{}},{'working_desktop_unchanged':False},
                           {'normal_request_count':0},{'normal_visibility_verified':False},
                           {'contains_real_client':True},{'cleanup':{**report['cleanup'],'window_destroyed':False}}):
                path.write_text(json.dumps({**report,**change}),encoding='utf-8')
                with self.assertRaisesRegex(ConsoleError,'synthetic_evidence_required'):restore.require_synthetic(path)
