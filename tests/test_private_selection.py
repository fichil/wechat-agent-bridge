import contextlib
import copy
from dataclasses import asdict
import io
import json
from pathlib import Path
import platform
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import development_private_selection as select
from scripts import probe_private_selection as synthetic
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from tests.test_console import SyntheticProtector


class PrivateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.port=Mock();self.port.selected.side_effect=[False,False,True]
        self.check=Mock();self.claim=Mock()
    def trial(self,allowed=True):return select.select_transaction(self.port,self.check,self.claim,allowed=allowed)
    def test_default_entry_points_and_opt_in_have_no_client_action(self):
        for module in (select,synthetic):
            with patch.object(module,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main([]),0)
        self.assertEqual(self.trial(False)['state'],'failed');self.port.verify_row.assert_not_called()
    def test_durable_claim_precedes_one_semantic_select_and_exact_verification(self):
        events=[];self.claim.side_effect=lambda:events.append('claim');self.port.select.side_effect=lambda:events.append('select')
        result=self.trial()
        self.assertEqual(events,['claim','select']);self.port.select.assert_called_once();self.port.verify_target.assert_called_once()
        self.assertEqual(result['state'],'selected_for_development');self.assertFalse(result['background_qualified'])
    def test_wrong_row_existing_selection_ambiguity_or_claim_failure_never_select(self):
        for kind in ('row','already_selected_wrong_target','claim','changed_before_call'):
            self.setUp()
            if kind=='row':self.port.verify_row.side_effect=ConsoleError('row_changed')
            elif kind=='already_selected_wrong_target':
                self.port.selected.side_effect=[True];self.port.verify_target.side_effect=ConsoleError('wrong_target_or_draft')
            elif kind=='claim':self.claim.side_effect=OSError()
            else:self.port.selected.side_effect=[False,True]
            result=self.trial();self.assertEqual(result['state'],'failed');self.assertFalse(result['attempted'])
            self.port.select.assert_not_called()
    def test_already_selected_only_verifies_and_never_replays_an_action(self):
        self.port.selected.side_effect=[True]
        self.assertEqual(self.trial()['state'],'already_selected')
        self.claim.assert_not_called();self.port.select.assert_not_called();self.port.verify_target.assert_called_once()
    def test_timeout_missing_readback_and_existing_private_draft_remain_unknown_without_retry(self):
        for kind in ('timeout','readback','draft'):
            self.setUp()
            if kind=='timeout':self.port.select.side_effect=TimeoutError()
            elif kind=='readback':self.port.selected.side_effect=[False,False,False]
            else:self.port.verify_target.side_effect=ConsoleError('private_draft_not_empty')
            result=self.trial();self.assertEqual(result['state'],'unknown');self.assertTrue(result['attempted'])
            self.port.select.assert_called_once()
            self.assertNotIn('set_value',[c[0] for c in self.port.mock_calls])
    def test_human_takeover_after_action_prevents_further_target_operations(self):
        self.check.side_effect=[None,None,ConsoleError('human_takeover')]
        self.assertEqual(self.trial()['state'],'unknown');self.port.select.assert_called_once()
        self.port.verify_target.assert_not_called()
    def test_owned_evidence_cannot_qualify_a_live_background_capability(self):
        report=dict(kind='owned_private_selection_probe',passed=True,contains_real_client=False,
            sources=select.sources(),os=platform.version(),working_desktop_unchanged=True,sources_unchanged=True,
            uia_dll_unchanged=True,mta_verified=True,operation=dict(select_calls=1,selection_readback=True),
            native=dict(selected_index=1),cleanup=dict(terminated=False,errors=[],**{k:True for k in (
                'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent')}))
        with tempfile.TemporaryDirectory() as d,patch.object(select.original,'DIRECTORY',Path(d)):
            path=Path(d)/'owned.json';path.write_text(json.dumps(report),encoding='utf-8');select.require_synthetic(path)
            for change in ({'contains_real_client':True},{'sources':{}},{'working_desktop_unchanged':False},
                {'operation':dict(select_calls=2,selection_readback=True)},{'native':dict(selected_index=0)},
                {'cleanup':{**report['cleanup'],'owned_windows_absent':False}}):
                path.write_text(json.dumps({**report,**change}),encoding='utf-8')
                with self.assertRaisesRegex(ConsoleError,'owned_recovery_required'):select.require_synthetic(path)

    def test_recovery_requires_current_exact_card_and_rejects_transient_focus_or_cleanup_failure(self):
        recipient='wxid_synthetic_private';target=dict(input_runtime_id=[1],session_runtime_id=[2],private_header_runtime_id=[3])
        current=dict(**target,identity_verified=True,target_index=0,draft_empty=True,value_readonly=False)
        report=dict(state='selection_observed_after_unknown',attempted=False,recipient_exact_id_reverified=True,
            identity_source='current_uia_contact_card_during_selection_recovery',current_target_controls=current,
            current_contact_identity=dict(exact_id_verified=True,expected_id_sha256=select.sha(recipient),
                profile_runtime_id=[42,202],profile_hwnd=202),fingerprint=dict(hwnd=101),
            cleanup_errors=[],events=[],patch_restored=True,worker_exited=True,sources_unchanged=True)
        select.require_recovery_result(report,target,recipient)
        for change in ({'recipient_exact_id_reverified':False},{'current_contact_identity':{}},
            {'events':[{'kind':'focus','transient':True}]},{'cleanup_errors':['restore_failed']},
            {'sources_unchanged':False},{'worker_exited':False},{'patch_restored':False},
            {'current_contact_identity':{**report['current_contact_identity'],'expected_id_sha256':'wrong'}}):
            with self.assertRaises(ConsoleError):select.require_recovery_result({**report,**change},target,recipient)
        with self.assertRaises(ConsoleError):
            select.require_recovery_result({**report,'current_target_controls':{**current,'session_runtime_id':[99]}},target,recipient)


class PrivateRecoveryCoordinatorTests(unittest.TestCase):
    def execute(self,*,missing_identity=False,focus_event=False,restore_failure=False,wrong_lock=False):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            directory=Path(d);fp=Fingerprint(77,101,42,'synthetic','synthetic',4096,8192,10)
            target=dict(input_runtime_id=[1],session_runtime_id=[2],private_header_runtime_id=[3],
                identity_verified=True,target_index=0,draft_empty=True,value_readonly=False)
            evidence=directory/'contact.json';ready=directory/'ready.json'
            evidence.write_text('{}',encoding='utf-8');ready.write_text('{}',encoding='utf-8')
            sha=select.hashlib.sha256(evidence.read_bytes()).hexdigest()
            request='development-select-private-'+select.hashlib.sha256((sha+sha).encode()).hexdigest()
            lock=select.original.RunLock(directory/'autofocus-draft.lock');lock.acquire()
            pending=dict(request=request,fingerprint=asdict(fp),method='verified_private_SelectionItem_Select',
                phase='terminal_unknown',lock_token=lock.token)
            old=dict(kind='development_private_semantic_selection',request_id=request,fingerprint=asdict(fp),
                state='unknown',worker_exited=True,patch_restored=True,cleanup_errors=[],send_invoked=False,draft_written=False)
            store=ConsoleStore(directory/'gate.sqlite3',SyntheticProtector())
            store.put('binding','account',dict(fingerprint=asdict(fp)))
            store.put('development_navigation','pending',pending);store.put('development_navigation',request,old)
            baseline={'upload_eligible':False,'retained_rows':8}
            store.put('private_baselines','preserved',baseline);store.close()
            if wrong_lock:lock.path.write_text('b'*32,encoding='ascii')
            args=SimpleNamespace(inspect_after_attempt=True,development_foreground=True,allow_accessibility_patch=True,
                account='synthetic_sender',private_id='wxid_synthetic_private',private_label='same_name',
                target_evidence=str(evidence),cua_ready_evidence=str(ready),context_evidence='synthetic')
            scope=Mock();scope.resolve.return_value=target;scope.metadata.return_value={'target_kind':'private'}
            native=Mock();native.fingerprint.return_value=fp;factory=Mock(return_value=native)
            result=dict(state='selection_observed_after_unknown',attempted=False,recipient_exact_id_reverified=not missing_identity,
                identity_source='current_uia_contact_card_during_selection_recovery',current_target_controls=target,
                current_contact_identity=dict(exact_id_verified=True,expected_id_sha256=select.sha(args.private_id),
                    profile_runtime_id=[42,202],profile_hwnd=202))
            parent,child=Mock(),Mock();parent.poll.return_value=True;parent.recv.return_value=dict(kind='result',value=result)
            process=Mock(pid=333,exitcode=0);process.is_alive.return_value=False
            context=Mock();context.Pipe.return_value=(parent,child);context.Process.return_value=process
            observer=Mock();observer.__enter__=Mock(return_value=observer)
            observer.evidence=SimpleNamespace(lock=threading.RLock(),failed=False,
                events=[{'kind':'focus','transient':True}] if focus_event else [])
            class Gate:
                def __init__(self,native,*,allowed,save):self.save=save
                def enable(self,fp,check):self.save({'synthetic':True});check();return {'state':'enabled'}
                def restore(self):
                    if restore_failure:raise ConsoleError('synthetic_restore_failure')
                    self.save(None);return {'state':'restored'}
            for name,value in (
                ('scripts.probe_background_draft.DIRECTORY',directory),
                ('scripts.probe_bound_uia_context.require_context_canary',Mock()),
                ('wechat_agent_bridge.protection.DPAPIProtector',lambda:SyntheticProtector()),
                ('wechat_agent_bridge.adapters.accessibility_gate.NativeGate',factory),
                ('wechat_agent_bridge.adapters.accessibility_gate.AccessibilityGate',Gate),
                ('wechat_agent_bridge.adapters.background.snapshot',Mock(return_value=DesktopSnapshot(101,102,3,True))),
                ('wechat_agent_bridge.adapters.desktop_events.DesktopEventObserver',Mock(return_value=observer))):
                stack.enter_context(patch(name,value))
            stack.enter_context(patch.object(select,'PrivateTestScope',Mock(return_value=scope)))
            stack.enter_context(patch.object(select,'cua_minimized_case',Mock()))
            stack.enter_context(patch.object(select,'input_tick',return_value=100))
            stack.enter_context(patch.object(select.mp,'get_context',return_value=context))
            report=select.run(args)
            store=ConsoleStore(directory/'gate.sqlite3',SyntheticProtector())
            try:
                self.assertEqual(store.get('development_navigation',request),old)
                self.assertEqual(store.get('private_baselines','preserved'),baseline)
                current_pending=store.get('development_navigation','pending')
                observation=store.get('development_navigation',request+':recovery-observed')
            finally:store.close()
            return report,current_pending,observation,lock.path.exists(),factory.called

    def test_current_exact_card_observation_is_saved_without_rewriting_unknown_or_history(self):
        report,pending,observation,locked,native=self.execute()
        self.assertEqual(report['state'],'selection_observed_after_unknown')
        self.assertIsNone(pending);self.assertFalse(locked);self.assertTrue(native)
        self.assertTrue(observation['recipient_exact_id_reverified'])

    def test_missing_card_transient_focus_or_restore_failure_keeps_original_lock_and_unknown(self):
        for kwargs in ({'missing_identity':True},{'focus_event':True},{'restore_failure':True}):
            report,pending,observation,locked,_=self.execute(**kwargs)
            self.assertIn(report['state'],('failed','recovery_required'));self.assertTrue(pending)
            self.assertTrue(locked);self.assertIsNone(observation)

    def test_foreign_lock_is_rejected_before_native_client_access(self):
        report,pending,observation,locked,native=self.execute(wrong_lock=True)
        self.assertEqual(report['state'],'failed');self.assertTrue(pending);self.assertTrue(locked)
        self.assertIsNone(observation);self.assertFalse(native)
