import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import verify_contact_profile as probe
from scripts.contact_profile_identity import sha
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore


class ContactProbeTests(unittest.TestCase):
    def test_old_epoch_baseline_blocks_recapture_without_mutation(self):
        from scripts.recover_exited_navigation import content_hash
        store=ConsoleStore(':memory:',SyntheticProtector())
        self.addCleanup(store.close)
        account=dict(wechat_id='synthetic_sender',identity_source='uia_own_profile_exact_id_user_selected_window')
        old=dict(account=account,fingerprint={'created':41})
        pointer=content_hash(old['fingerprint'])
        current=dict(account=account,fingerprint={'created':42},previous_fingerprint_sha256=pointer)
        store.put('binding_epochs',pointer,old)
        key=probe.baseline_key(old['fingerprint'],'synthetic_peer')
        baseline={'historical':True,'upload_eligible':False,'protected_sha256':'unchanged'}
        store.put('private_baselines',key,baseline)
        self.assertEqual(probe.existing_epoch_baseline(store,current,'synthetic_peer'),(key,baseline))
        self.assertEqual(store.get('private_baselines',key),baseline)
        self.assertIsNone(probe.existing_epoch_baseline(store,current,'different_peer'))

    def test_invalid_epoch_lineage_never_allows_capture(self):
        from scripts.recover_exited_navigation import content_hash
        for problem in ('missing','wrong_sender','cycle'):
            with self.subTest(problem=problem):
                store=ConsoleStore(':memory:',SyntheticProtector())
                try:
                    account=dict(wechat_id='synthetic_sender',identity_source='uia_own_profile_exact_id_user_selected_window')
                    old=dict(account=account,fingerprint={'created':41})
                    pointer=content_hash(old['fingerprint'])
                    current=dict(account=account,fingerprint={'created':42},previous_fingerprint_sha256=pointer)
                    if problem=='wrong_sender': old['account']={**account,'wechat_id':'other'}
                    if problem=='cycle': old['previous_fingerprint_sha256']=pointer
                    if problem!='missing': store.put('binding_epochs',pointer,old)
                    with self.assertRaisesRegex(ConsoleError,'lineage_invalid'):
                        probe.existing_epoch_baseline(store,current,'synthetic_peer')
                finally: store.close()

    def test_default_does_not_touch_client(self):
        with patch.object(probe,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]),0)

    def execute(self,*,restore_failure=False,worker_failure=False,timeout=False,wrong_sender=False,visual_mismatch=False,capture=False,prior_epoch=False):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            directory=Path(d); fp=Fingerprint(77,101,42,'synthetic','synthetic',4096,8192,10)
            binding=dict(account=dict(wechat_id='synthetic_sender',identity_source='uia_own_profile_exact_id_user_selected_window'),
                fingerprint=asdict(fp))
            if wrong_sender: binding['account']['wechat_id']='other_sender'
            store=ConsoleStore(directory/'gate.sqlite3',SyntheticProtector())
            if prior_epoch:
                from scripts.recover_exited_navigation import content_hash
                old={**binding,'fingerprint':{**binding['fingerprint'],'created':41}}
                pointer=content_hash(old['fingerprint'])
                binding['previous_fingerprint_sha256']=pointer
                store.put('binding_epochs',pointer,old)
                store.put('private_baselines',probe.baseline_key(old['fingerprint'],'wxid_synthetic_private'),
                    {'historical':True,'upload_eligible':False})
            store.put('binding','account',binding); store.close()
            expected='wxid_synthetic_private'; label='synthetic_label'
            evidence=dict(kind='development_cua_private_identity',identity_verified=True,
                source='computer_use_profile_card',window_id=101,binding_reference=asdict(fp),
                expected_id_sha256=sha(expected),label_sha256=sha(label),send_invoked=False,api_called=False)
            if visual_mismatch: evidence['binding_reference']['created']=43
            path=directory/'visual.json'; path.write_text(json.dumps(evidence),encoding='utf-8')
            args=SimpleNamespace(allow_accessibility_patch=True,account='synthetic_sender',private_id=expected,
                private_label=label,cua_evidence=str(path),synthetic_evidence='synthetic',capture_private_baseline=capture)
            native=Mock(); native.fingerprint.return_value=fp
            observer=Mock(); observer.__enter__=Mock(return_value=observer)
            observer.evidence=SimpleNamespace(events=[],failed=False)
            parent,child=Mock(),Mock()
            parent.poll.return_value=True
            parent.recv.return_value=(dict(ok=False,reason='contact_target_or_empty_draft_unverified') if worker_failure else
                dict(ok=True,result=dict(state='contact_profile_verified',identity=dict(expected_id_sha256=sha(expected)))))
            process=Mock(pid=333,exitcode=0); process.is_alive.return_value=False
            context=Mock(); context.Pipe.return_value=parent,child; context.Process.return_value=process
            gates=[]
            class Gate:
                def __init__(self,native,*,allowed,save):
                    self.save,self.restored=save,False; gates.append(self)
                def enable(self,fp,check): self.save({'synthetic':True}); check(); return {'state':'enabled'}
                def restore(self):
                    self.restored=True
                    if restore_failure: raise ConsoleError('synthetic_restore_failure')
                    self.save(None); return {'state':'restored'}
            for target,value in (
                ('scripts.probe_background_draft.DIRECTORY',directory),
                ('wechat_agent_bridge.protection.DPAPIProtector',lambda:SyntheticProtector()),
                ('scripts.probe_bound_uia_context.require_context_canary',Mock()),
                ('wechat_agent_bridge.adapters.accessibility_gate.NativeGate',Mock(return_value=native)),
                ('wechat_agent_bridge.adapters.accessibility_gate.AccessibilityGate',Gate),
                ('wechat_agent_bridge.adapters.background.snapshot',Mock(return_value=DesktopSnapshot(101,102,3,True))),
                ('wechat_agent_bridge.adapters.desktop_events.DesktopEventObserver',Mock(return_value=observer))):
                stack.enter_context(patch(target,value))
            stack.enter_context(patch.object(probe.mp,'get_context',return_value=context))
            if timeout: stack.enter_context(patch.object(probe.time,'monotonic',side_effect=[0,0,26,27]))
            result=probe.run(args)
            store=ConsoleStore(directory/'gate.sqlite3',SyntheticProtector())
            try:
                persisted=store.values('verified_contacts')
                self.assertEqual(store.get('binding','account'),binding)
                pending=store.get('patch','pending')
            finally: store.close()
            lock_present=(directory/'autofocus-draft.lock').exists()
            return result,persisted,gates,pending,lock_present,native,parent,child,process

    def test_verified_contact_is_separate_from_sender_and_saved_only_after_restore(self):
        result,persisted,gates,pending,locked,native,parent,child,process=self.execute()
        self.assertEqual(result['state'],'contact_profile_verified')
        self.assertEqual(len(persisted),1)
        self.assertTrue(persisted[0]['patch_restored'])
        self.assertTrue(gates[0].restored)
        self.assertFalse(result['messages_read'])
        self.assertFalse(result['background_qualified'])
        self.assertFalse(result['account_binding_modified'])
        self.assertFalse(pending); self.assertFalse(locked)
        native.close.assert_called_once(); parent.close.assert_called_once(); self.assertTrue(child.close.called)
        process.close.assert_called_once()

    def test_prior_epoch_capture_is_rejected_before_native_or_worker_access(self):
        result,persisted,gates,pending,locked,native,parent,child,process=self.execute(capture=True,prior_epoch=True)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['reason'],'private_baseline_already_bound')
        self.assertEqual(persisted,[])
        self.assertEqual(gates,[])
        native.fingerprint.assert_not_called()
        parent.recv.assert_not_called()
        process.start.assert_not_called()
        self.assertFalse(pending)
        self.assertFalse(locked)

    def test_restore_failure_preserves_pending_and_lock_and_never_saves_identity(self):
        result,persisted,_gates,pending,locked,native,parent,child,_=self.execute(restore_failure=True)
        self.assertEqual(result['state'],'recovery_required')
        self.assertEqual(persisted,[]); self.assertTrue(pending); self.assertTrue(locked)
        native.close.assert_called_once(); parent.close.assert_called_once(); self.assertTrue(child.close.called)

    def test_worker_rejection_restores_without_identity_grant(self):
        result,persisted,_gates,pending,locked,*_=self.execute(worker_failure=True)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['reason'],'contact_target_or_empty_draft_unverified')
        self.assertEqual(persisted,[]); self.assertFalse(pending); self.assertFalse(locked)

    def test_timeout_restores_and_closes_spawned_worker_resources(self):
        result,persisted,gates,pending,locked,_native,parent,child,process=self.execute(timeout=True)
        self.assertEqual(result['reason'],'contact_readonly_timeout')
        self.assertEqual(persisted,[]); self.assertTrue(gates[0].restored)
        self.assertFalse(pending); self.assertFalse(locked)
        process.join.assert_called(); parent.close.assert_called_once(); self.assertTrue(child.close.called)
        process.close.assert_called_once()

    def test_visual_process_mismatch_or_wrong_sender_never_enables_gate(self):
        for kwargs in ({'wrong_sender':True},{'visual_mismatch':True}):
            result,persisted,gates,pending,locked,*_=self.execute(**kwargs)
            self.assertEqual(result['state'],'failed')
            self.assertEqual(gates,[]); self.assertEqual(persisted,[])
            self.assertFalse(pending); self.assertFalse(locked)

    def test_missing_baseline_result_never_grants_and_read_attempt_stays_unknown(self):
        result,persisted,gates,pending,locked,*_=self.execute(capture=True)
        self.assertEqual(result['reason'],'private_baseline_result_unverified')
        self.assertTrue(result['message_read_attempted'])
        self.assertIsNone(result['messages_read'])
        self.assertEqual(persisted,[]); self.assertTrue(gates[0].restored)
        self.assertFalse(pending); self.assertFalse(locked)

    def test_identity_refresh_preserves_immutable_private_history_baseline(self):
        expected='wxid_synthetic_private'
        store=ConsoleStore(':memory:',SyntheticProtector())
        self.addCleanup(store.close)
        baseline=dict(upload_eligible=False,reverified_before_save=True,protected_sha256='original',
            baseline_event_hashes=['synthetic-old'])
        report=dict(state='contact_profile_verified',patch_restored=True,worker_exited=True,events_observed=0,
            sources_unchanged=True,cleanup_errors=[],fingerprint={'created':42},
            worker=dict(identity=dict(expected_id_sha256=sha(expected)),private_baseline=baseline))
        probe.save_verified_result(store,expected,report)
        key=probe.baseline_key(report['fingerprint'],expected)
        refresh={**report,'worker':dict(identity=dict(expected_id_sha256=sha(expected)))}
        probe.save_verified_result(store,expected,refresh)
        self.assertEqual(store.get('private_baselines',key),report)
        self.assertEqual(store.get('verified_contacts',sha(expected)),refresh)
        changed={**report,'worker':{**report['worker'],'private_baseline':{**baseline,'protected_sha256':'new'}}}
        with self.assertRaisesRegex(ConsoleError,'baseline_immutable'): probe.save_verified_result(store,expected,changed)
        self.assertEqual(store.get('private_baselines',key),report)
        self.assertNotEqual(key,probe.baseline_key({'created':43},expected))

    def test_incomplete_cleanup_cannot_save_identity_or_baseline(self):
        store=ConsoleStore(':memory:',SyntheticProtector())
        self.addCleanup(store.close)
        with self.assertRaisesRegex(ConsoleError,'complete_result_required'):
            probe.save_verified_result(store,'wxid_synthetic_private',dict(state='contact_profile_verified'))
        self.assertEqual(store.values('private_baselines'),[])
        self.assertEqual(store.values('verified_contacts'),[])
