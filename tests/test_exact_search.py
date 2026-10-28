import contextlib
import io
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import Mock,patch

from scripts import development_exact_search as exact
from scripts import probe_exact_search as synthetic
from scripts.contact_profile_identity import sha
from wechat_agent_bridge.console_models import ConsoleError


class ExactSearchTests(unittest.TestCase):
    def setUp(self):
        self.port=Mock();self.port.value.side_effect=['prior','prior','exact']
        self.check=Mock();self.claim=Mock()
    def trial(self,allowed=True):
        return exact.exact_search_transaction(self.port,self.check,self.claim,'prior','exact',allowed=allowed)
    def test_defaults_and_missing_opt_in_never_update_anything(self):
        for module in (exact,synthetic):
            with patch.object(module,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main([]),0)
        self.assertEqual(self.trial(False)['state'],'failed');self.port.verify.assert_not_called()
    def test_one_exact_update_requires_durable_claim_safe_context_and_full_readback(self):
        events=[];self.claim.side_effect=lambda:events.append('claim');self.port.set_value.side_effect=lambda s:events.append(s)
        result=self.trial()
        self.assertEqual(events,['claim','exact']);self.port.set_value.assert_called_once_with('exact')
        self.assertEqual(result['state'],'exact_search_updated');self.assertTrue(result['setter_returned'])
        self.assertGreaterEqual(self.port.guard_context.call_count,2)
    def test_changed_user_query_wrong_field_unsafe_context_and_failed_claim_never_set(self):
        for case in ('query','field','context','claim','changed_after_claim'):
            self.setUp()
            if case=='query':self.port.value.side_effect=['user search']
            elif case=='field':self.port.verify.side_effect=ConsoleError('wrong_field')
            elif case=='context':self.port.guard_context.side_effect=ConsoleError('unsafe_context')
            elif case=='claim':self.claim.side_effect=OSError()
            else:self.port.value.side_effect=['prior','changed']
            result=self.trial();self.assertEqual(result['state'],'failed');self.assertFalse(result['action_executed'])
            self.port.set_value.assert_not_called()
    def test_already_exact_never_claims_or_updates(self):
        self.port.value.side_effect=['exact']
        self.assertEqual(self.trial()['state'],'already_exact_search')
        self.claim.assert_not_called();self.port.set_value.assert_not_called()
    def test_timeout_mismatch_and_focus_or_human_change_are_unknown_without_retry(self):
        for case in ('timeout','readback','focus'):
            self.setUp()
            if case=='timeout':self.port.set_value.side_effect=TimeoutError()
            elif case=='readback':self.port.value.side_effect=['prior','prior','other']
            else:self.check.side_effect=[None,None,ConsoleError('human_takeover')]
            result=self.trial();self.assertEqual(result['state'],'unknown');self.port.set_value.assert_called_once()
            self.assertEqual(result['setter_returned'],case!='timeout')
    def test_inspection_proof_requires_exact_scope_sources_readonly_mode_and_runtime(self):
        fp=dict(pid=7,hwnd=101);label='synthetic-label';recipient='wxid_synthetic'
        proof=dict(kind='bound_search_control_readonly',state='search_control_inspected',fingerprint=fp,
            recipient_id_sha256=sha(recipient),target_label_sha256=sha(label),sources_unchanged=True,sources=exact.readonly.sources(),
            patch_restored=True,worker_exited=True,cleanup_errors=[],events=[],selection_unknown_preserved=True,
            action_executed=False,screenshot_captured=False,message_text_read=False,chat_draft_read=False,
            worker=dict(search_class=exact.readonly.SEARCH_CLASS,readonly=False,query_kind='display_label',search_runtime_id=[42,101,9]))
        self.assertEqual(exact.validate_inspection(proof,fp,label,recipient),[42,101,9])
        for change in ({'fingerprint':dict(pid=8,hwnd=101)},{'recipient_id_sha256':'wrong'},{'sources':{}},
            {'events':[{}]},{'action_executed':True},{'message_text_read':True}):
            with self.assertRaisesRegex(ConsoleError,'verified_inspection_required'):
                exact.validate_inspection({**proof,**change},fp,label,recipient)
        with self.assertRaisesRegex(ConsoleError,'runtime_id_invalid'):
            exact.validate_inspection({**proof,'worker':{**proof['worker'],'search_runtime_id':[]}},fp,label,recipient)
    def test_spawned_owned_child_receives_exact_search_phase_not_original_three_phase_loop(self):
        def inspect(pipe,desktop):
            self.assertEqual(synthetic.base.PHASES,('exact_search',))
            self.assertIs(synthetic.base.phase,synthetic.phase)
            self.assertEqual(synthetic.base.TEXT,{'exact_search':synthetic.TOKEN})
        with patch.object(synthetic,'ORIGINAL_CHILD',side_effect=inspect) as child:
            synthetic.child(object(),'synthetic-desktop');child.assert_called_once()
    def test_native_qualification_rejects_old_sources_multiple_calls_and_incomplete_cleanup(self):
        report=dict(kind='owned_exact_search_probe',passed=True,contains_real_client=False,os=platform.version(),
            sources=exact.sources(),sources_unchanged=True,working_desktop_unchanged=True,uia_dll_unchanged=True,mta_verified=True,
            operation=dict(state='exact_search_updated',setter_returned=True,set_calls=1,native_full_readback_equal=True,
                context_verified=True,focus_unchanged=True),cleanup=dict(errors=[],terminated=False,**{k:True for k in (
                    'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent')}))
        with tempfile.TemporaryDirectory() as d,patch.object(exact.original,'DIRECTORY',Path(d)):
            path=Path(d)/'proof.json';path.write_text(json.dumps(report),encoding='utf-8');exact.require_synthetic(path)
            for change in ({'sources':{}},{'contains_real_client':True},{'operation':{**report['operation'],'set_calls':2}},
                {'cleanup':{**report['cleanup'],'native_cleanup_confirmed':False}}):
                path.write_text(json.dumps({**report,**change}),encoding='utf-8')
                with self.assertRaisesRegex(ConsoleError,'owned_evidence_required'):exact.require_synthetic(path)
