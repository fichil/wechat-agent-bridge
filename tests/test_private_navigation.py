from unittest import TestCase
from unittest.mock import Mock
from wechat_agent_bridge.adapters.private_navigation import navigate_once
from wechat_agent_bridge.console_models import ConsoleError


class NavigationTests(TestCase):
    def port(self):
        p=Mock();p.destination.return_value={'private_header':True,'selected_row':True};return p

    def test_claim_precedes_single_invoke_and_does_not_qualify_identity_or_send(self):
        p=self.port();claimed=[];p.invoke.side_effect=lambda:self.assertEqual(claimed,[True])
        r=navigate_once(p,lambda:None,lambda:claimed.append(True))
        self.assertEqual(r['state'],'private_selected');p.invoke.assert_called_once()
        self.assertFalse(r['identity_verified']);self.assertFalse(r['send_verified'])

    def test_draft_or_changed_source_stops_before_claim(self):
        p=self.port();p.verify_source.side_effect=ConsoleError('existing_draft_or_unknown');claim=Mock()
        r=navigate_once(p,lambda:None,claim)
        self.assertEqual(r['state'],'failed');p.invoke.assert_not_called();claim.assert_not_called()

    def test_no_readback_and_post_invoke_focus_change_are_unknown_without_retry(self):
        p=self.port();p.destination.return_value={'private_header':False,'selected_row':False}
        r=navigate_once(p,lambda:None,lambda:None);self.assertEqual(r['state'],'unknown');p.invoke.assert_called_once()
        p=self.port();check=Mock(side_effect=[None,None,None,ConsoleError('focus_changed')])
        r=navigate_once(p,check,lambda:None);self.assertEqual(r['state'],'unknown');p.invoke.assert_called_once()

    def test_claim_failure_never_calls_ui(self):
        p=self.port();claim=Mock(side_effect=RuntimeError('disk'))
        self.assertEqual(navigate_once(p,lambda:None,claim)['state'],'failed');p.invoke.assert_not_called()
