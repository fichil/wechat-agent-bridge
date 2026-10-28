import contextlib
import io
import unittest
from unittest.mock import Mock,patch
from scripts import development_navigation as nav
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.desktop_events import EventEvidence


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.port=Mock(); self.port.empty.return_value=True; self.port.active.side_effect=[False,True]
        self.check,self.claim=Mock(),Mock()
    def run_trial(self,allowed=True):
        return nav.activate_transaction(self.port,self.check,self.claim,development_foreground=allowed)
    def test_default_disabled_and_missing_development_opt_in(self):
        with patch.object(nav,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()): self.assertEqual(nav.main([]),0)
        self.assertEqual(self.run_trial(False)['state'],'failed'); self.port.verify_target.assert_not_called()
    def test_com_bool_integer_zero_is_writable_but_unknown_types_are_not(self):
        for readonly,accepted in ((False,True),(0,True),(True,False),(1,False),(-1,False),(None,False),('0',False),(0.0,False)):
            pattern=Mock(CurrentIsReadOnly=readonly,CurrentValue='')
            self.assertEqual(nav.empty_draft(pattern),accepted)
            pattern.CurrentValue='synthetic user draft'
            self.assertFalse(nav.empty_draft(pattern))
    def test_one_explicit_focus_is_never_background_qualification(self):
        result=self.run_trial(); self.assertEqual(result['state'],'activated_for_development')
        self.assertFalse(result['background_qualified']); self.port.focus_bound_input.assert_called_once(); self.claim.assert_called_once()
    def test_existing_draft_and_identity_mismatch_stop_without_action(self):
        for case in ('draft','identity'):
            self.setUp()
            if case=='draft': self.port.empty.return_value=False
            else: self.port.verify_target.side_effect=ConsoleError('target_mismatch')
            self.assertEqual(self.run_trial()['state'],'failed'); self.claim.assert_not_called(); self.port.focus_bound_input.assert_not_called()
    def test_claim_failure_and_target_change_before_call_do_not_focus(self):
        self.claim.side_effect=OSError('synthetic journal failure')
        self.assertFalse(self.run_trial()['attempted']); self.port.focus_bound_input.assert_not_called()
        self.setUp(); self.port.verify_target.side_effect=[None,ConsoleError('changed')]
        self.assertFalse(self.run_trial()['attempted']); self.port.focus_bound_input.assert_not_called()
    def test_already_active_needs_no_action(self):
        self.port.active.side_effect=None; self.port.active.return_value=True
        self.assertEqual(self.run_trial()['state'],'already_active'); self.claim.assert_not_called(); self.port.focus_bound_input.assert_not_called()
    def test_timeout_or_unobserved_activation_is_unknown_without_retry(self):
        self.port.focus_bound_input.side_effect=TimeoutError()
        self.assertEqual(self.run_trial()['state'],'unknown'); self.port.focus_bound_input.assert_called_once()
        self.setUp(); self.port.active.side_effect=[False,False]
        self.assertEqual(self.run_trial()['state'],'unknown'); self.port.focus_bound_input.assert_called_once()
    def test_human_takeover_after_call_keeps_unknown(self):
        self.check.side_effect=[None,None,ConsoleError('human_takeover')]
        self.assertEqual(self.run_trial()['state'],'unknown'); self.port.focus_bound_input.assert_called_once()


class NavigationCleanupTests(unittest.TestCase):
    def setUp(self):
        self.report=dict(state='unknown',cleanup_errors=[])
        self.process=Mock(pid=123,exitcode=0); self.process.is_alive.return_value=False
        self.gate=Mock(); self.gate.restore.return_value={'state':'restored'}
        self.observer=Mock(evidence=EventEvidence()); self.resource=Mock()
    def finish(self):
        nav.cleanup_resources(self.report,process=self.process,gate=self.gate,observer=self.observer,
                              resources=(('port',self.resource),))
    def test_normal_cleanup_preserves_unknown_and_closes_process_handle(self):
        self.finish()
        self.assertTrue(self.report['patch_restored']); self.assertTrue(self.report['worker_exited'])
        self.assertEqual(self.report['state'],'unknown'); self.assertEqual(self.report['cleanup_errors'],[])
        self.process.close.assert_called_once(); self.resource.close.assert_called_once()
    def test_failure_at_each_stage_does_not_skip_other_cleanup(self):
        for label in ('process','gate','observer'):
            self.setUp()
            action={'process':self.process.join,'gate':self.gate.restore,'observer':self.observer.close}[label]
            action.side_effect=OSError('synthetic cleanup failure')
            self.finish()
            self.assertEqual(self.report['state'],'recovery_required')
            self.gate.restore.assert_called_once(); self.observer.close.assert_called_once()
            self.resource.close.assert_called_once()
    def test_only_owned_worker_is_terminated_and_still_alive_is_recovery(self):
        self.process.is_alive.return_value=True
        self.finish()
        self.process.terminate.assert_called_once(); self.process.close.assert_not_called()
        self.assertFalse(self.report['worker_exited']); self.assertTrue(self.report['patch_restored'])
        self.assertEqual(self.report['state'],'recovery_required')
    def test_observer_failure_and_process_exit_are_separately_recorded(self):
        self.gate.restore.return_value={'state':'process_exited'}
        self.observer.evidence.failed=True
        self.finish()
        self.assertTrue(self.report['patch_restored'])
        self.assertIn('development_observer_failed',self.report['cleanup_errors'])
        self.assertEqual(self.report['state'],'recovery_required')
