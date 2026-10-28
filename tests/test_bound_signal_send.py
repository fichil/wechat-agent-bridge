import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from scripts import verify_bound_signal_send as trial
from scripts import verify_bound_send as generic
from tests.test_bound_fixed_send import Port
from wechat_agent_bridge.acceptance import FIXED_TEXT
from wechat_agent_bridge.console_models import ConsoleError


class BoundSignalTests(unittest.TestCase):
    def test_default_and_missing_helper_permission_cannot_run(self):
        with patch.object(trial.original_send,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(trial.main([]),0)

    def test_readonly_evidence_requires_same_process_and_full_cleanup(self):
        fp={'created':123,'pid':456}; build={'synthetic_only':False}
        proof=dict(state='preflight_verified',build=build,patch_restored=True,desktop_unchanged=True,
            events_observed=0,cleanup_errors=[],worker=dict(fingerprint=fp,runtimes=[[1],[2],[3]],inspection=dict(
                qt_method_called=False,send_invoked=False,metadata_checked=True,selected=True,gui_thread_match=True,
                cleanup=dict(owner_exitcode=0,unhooked=1,drained=1,post_unhook_delivered=True,
                    post_unhook_no_callback=True,helper_unloaded=True,mapping_handles_closed=True,errors=[]))))
        with tempfile.TemporaryDirectory() as d,patch.object(trial.hook.original,'DIRECTORY',Path(d)):
            path=Path(d)/'proof.json'; path.write_text(json.dumps(proof),encoding='utf-8')
            self.assertEqual(trial.require_readonly(path,fp,build),[[1],[2],[3]])
            with self.assertRaisesRegex(ConsoleError,'exact_process'): trial.require_readonly(path,{'created':124,'pid':456},build)
            for problem in ('events','loaded','called'):
                bad=json.loads(json.dumps(proof))
                if problem=='events': bad['events_observed']=1
                if problem=='loaded': bad['worker']['inspection']['cleanup']['helper_unloaded']=False
                if problem=='called': bad['worker']['inspection']['qt_method_called']=True
                path.write_text(json.dumps(bad),encoding='utf-8')
                with self.assertRaisesRegex(ConsoleError,'exact_process'): trial.require_readonly(path,fp,build)

    def test_numbered_new_request_does_not_match_old_unknown_echo(self):
        port=Port(); port.rows=[dict(runtime_id=[1],label=FIXED_TEXT)]
        def send():
            port.text=''; port.rows.append(dict(runtime_id=[2],label=trial.TEST_TEXT))
        port.invoke=send
        reserve,mark=Mock(),Mock()
        result=generic.fixed_transaction(port,Mock(),reserve,mark,text=trial.TEST_TEXT)
        self.assertEqual(result['state'],'message_echo_observed')
        self.assertEqual(result['echo_runtime_id'],[2])
        reserve.assert_called_once(); mark.assert_called_once()

    def test_numbered_cleanup_cannot_erase_other_draft(self):
        port=Port(); port.text=FIXED_TEXT
        result=generic.clear_test_draft(port,Mock(),Mock(),text=trial.TEST_TEXT)
        self.assertEqual(result['reason'],'non_test_draft_preserved')
        self.assertEqual(port.text,FIXED_TEXT)

    def test_runtime_id_rebuild_blocks_dispatch_without_silent_rebind(self):
        port=object.__new__(trial.HookPort)
        port.fp={}; port.hook_evidence='canary'; port.readonly_evidence='proof'
        port.desktop_scope='separated_desktops'
        port.expected={'input_runtime_id':[1],'session_runtime_id':[3]}
        port.send_bundle=(None,None,None,[2])
        with patch.object(trial.hook,'require_recovery',return_value={}),patch.object(trial,'require_readonly',return_value=[[1],[2],[4]]):
            with self.assertRaisesRegex(ConsoleError,'target_changed'): port.require_send_capability()

    def test_memory_fixture_is_fixed_separately_budgeted_and_survives_restart(self):
        from wechat_agent_bridge.acceptance import AcceptanceLedger
        original=trial.Candidate('canary','proof')
        candidate=trial.Candidate('canary','proof',case='memory-003',desktop_scope='current_desktop_background')
        self.assertNotEqual(original.slot,candidate.slot)
        self.assertEqual(candidate.worker.keywords['text'],trial.MEMORY_TEXT)
        self.assertEqual(candidate.worker.keywords['port_factory'].keywords['case'],'memory-003')
        with self.assertRaisesRegex(ConsoleError,'fixed_case_required'):
            trial.Candidate('canary','proof',case='arbitrary')
        with tempfile.TemporaryDirectory() as d:
            database=Path(d)/'quota.sqlite3'
            ledger=AcceptanceLedger(database)
            try:
                ledger.reserve('send','old-unknown','fixed')
                original.grant(ledger); ledger.reserve('send','old-002',original.slot)
                candidate.grant(ledger); ledger.reserve('send','new-003',candidate.slot)
                before=ledger.status()
                self.assertEqual(before['send_used'],3)
                self.assertEqual(before['send_limit'],5)
            finally: ledger.close()
            ledger=AcceptanceLedger(database)
            try:
                candidate.grant(ledger)
                self.assertEqual(ledger.status(),before)
                with self.assertRaises(ConsoleError): ledger.reserve('send','retry',candidate.slot)
            finally: ledger.close()

    def test_current_mode_requires_a_proof_from_the_same_desktop_scope(self):
        with tempfile.TemporaryDirectory() as d,patch.object(trial.hook.original,'DIRECTORY',Path(d)):
            path=Path(d)/'proof.json'
            # Even otherwise valid legacy evidence must not be promoted to the new scope.
            proof=dict(state='preflight_verified',build={},patch_restored=True,desktop_unchanged=True,
                events_observed=0,cleanup_errors=[],worker=dict(fingerprint={},runtimes=[],inspection=dict(
                    qt_method_called=False,send_invoked=False,metadata_checked=True,selected=True,gui_thread_match=True,
                    cleanup=dict(owner_exitcode=0,unhooked=1,drained=1,post_unhook_delivered=True,
                        post_unhook_no_callback=True,helper_unloaded=True,mapping_handles_closed=True,errors=[]))))
            path.write_text(json.dumps(proof),encoding='utf-8')
            with self.assertRaisesRegex(ConsoleError,'exact_process'):
                trial.require_readonly(path,{}, {},desktop_scope='current_desktop_background')
            proof['desktop_scope']='current_desktop_background'
            path.write_text(json.dumps(proof),encoding='utf-8')
            self.assertEqual(trial.require_readonly(path,{}, {},desktop_scope='current_desktop_background'),[])
            with self.assertRaisesRegex(ConsoleError,'exact_process'): trial.require_readonly(path,{}, {})

    def test_private_case_needs_private_scope_and_has_an_independent_fixed_text(self):
        from scripts.private_test_scope import PrivateTestScope
        scope=PrivateTestScope('wxid_synthetic_private','synthetic')
        with self.assertRaisesRegex(ConsoleError,'scope_mismatch'): trial.Candidate('canary','proof',case='private-004')
        with self.assertRaisesRegex(ConsoleError,'scope_mismatch'): trial.Candidate('canary','proof',target_scope=scope)
        candidate=trial.Candidate('canary','proof',case='private-004',target_scope=scope)
        self.assertEqual(candidate.target_kind,'private')
        self.assertEqual(candidate.worker.keywords['text'],trial.PRIVATE_TEXT)
        self.assertNotEqual(candidate.slot,trial.Candidate('canary','proof',case='memory-003').slot)
        self.assertEqual(candidate.worker.keywords['port_factory'].keywords['recipient_id_sha256'],scope.metadata()['recipient_id_sha256'])

    def test_group_mode_zero_proof_cannot_authorize_a_private_send(self):
        proof=dict(state='preflight_verified',build={},patch_restored=True,desktop_unchanged=True,
            events_observed=0,cleanup_errors=[],worker=dict(fingerprint={},runtimes=[],inspection=dict(
                qt_method_called=False,send_invoked=False,metadata_checked=True,selected=True,gui_thread_match=True,
                cleanup=dict(owner_exitcode=0,unhooked=1,drained=1,post_unhook_delivered=True,
                    post_unhook_no_callback=True,helper_unloaded=True,mapping_handles_closed=True,errors=[]))))
        with tempfile.TemporaryDirectory() as d,patch.object(trial.hook.original,'DIRECTORY',Path(d)):
            path=Path(d)/'proof.json';path.write_text(json.dumps(proof),encoding='utf-8')
            with self.assertRaisesRegex(ConsoleError,'exact_process'):
                trial.require_readonly(path,{}, {},target_kind='private',recipient_id_sha256='a'*64)
            proof.update(target_kind='private',recipient_id_sha256='a'*64,worker_exited=True,source_unchanged=True)
            path.write_text(json.dumps(proof),encoding='utf-8')
            self.assertEqual(trial.require_readonly(path,{}, {},target_kind='private',recipient_id_sha256='a'*64),[])
            with self.assertRaisesRegex(ConsoleError,'exact_process'):
                trial.require_readonly(path,{}, {},target_kind='private',recipient_id_sha256='b'*64)
            with self.assertRaisesRegex(ConsoleError,'exact_process'): trial.require_readonly(path,{}, {})
