import contextlib
import io
from unittest import TestCase
from unittest.mock import Mock,patch
from scripts import development_contacts_navigation as probe
from wechat_agent_bridge.console_models import ConsoleError


class ContactsNavigationActionTests(TestCase):
    def test_default_cli_never_touches_client(self):
        with patch.object(probe,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]),0)
    def test_missing_opt_in_preserves_state(self):
        port=Mock();claim=Mock()
        result=probe.navigation_transaction(port,Mock(),claim)
        self.assertEqual(result['state'],'failed');claim.assert_not_called();port.invoke.assert_not_called()
    def test_claim_precedes_single_invoke_and_return_is_not_navigation_or_identity_proof(self):
        events=[];port=Mock();port.invoke.side_effect=lambda:events.append('invoke')
        port.page_structure.side_effect=[{'x':0},{'x':1}]
        result=probe.navigation_transaction(port,Mock(),lambda:events.append('persist'),allowed=True)
        self.assertEqual(events,['persist','invoke']);self.assertEqual(result['state'],'invoke_returned')
        self.assertTrue(result['structure_changed']);self.assertFalse(result['navigation_verified'])
    def test_identity_mismatch_never_invokes(self):
        port=Mock();port.verify.side_effect=ConsoleError('mismatch');claim=Mock()
        result=probe.navigation_transaction(port,Mock(),claim,allowed=True)
        self.assertEqual(result['state'],'failed');claim.assert_not_called();port.invoke.assert_not_called()
    def test_unknown_after_call_is_never_retried(self):
        port=Mock();port.invoke.side_effect=RuntimeError('synthetic')
        result=probe.navigation_transaction(port,Mock(),Mock(),allowed=True)
        self.assertEqual(result['state'],'unknown');self.assertTrue(result['action_executed']);port.invoke.assert_called_once()
    def test_human_takeover_after_claim_prevents_call(self):
        port=Mock();check=Mock(side_effect=[None,ConsoleError('human_takeover')])
        result=probe.navigation_transaction(port,check,Mock(),allowed=True)
        self.assertEqual(result['state'],'failed');port.invoke.assert_not_called()
    def test_wrong_or_unqualified_inspection_rejected(self):
        for value in ({},{'kind':'bound_contacts_navigation_metadata_readonly','state':'contacts_navigation_inspected'}):
            with self.assertRaises(ConsoleError):probe.require_inspection(value,{})
    def test_post_call_timeout_retains_the_fact_invoke_returned(self):
        port=Mock();check=Mock(side_effect=[None,None,ConsoleError('timeout')])
        result=probe.navigation_transaction(port,check,Mock(),allowed=True)
        self.assertEqual(result['state'],'unknown');self.assertTrue(result['invoke_returned']);port.invoke.assert_called_once()
