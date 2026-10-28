"""Pure context admission tests; no native memory or COM calls."""
import unittest
from scripts.uia_client_bootstrap import require_safe_context
from wechat_agent_bridge.console_models import ConsoleError


class BootstrapTests(unittest.TestCase):
    def test_only_stable_same_false_client_association_is_admitted(self):
        value=dict(stable=True,association='present',requested_auto_focus=False,
            associated_auto_focus=False,matches_requested_client=True,matches_requested_context_body=True)
        self.assertTrue(require_safe_context(value))
        for change in ({'stable':False},{'association':'null'},{'requested_auto_focus':True},
                       {'associated_auto_focus':True},{'matches_requested_client':False},
                       {'matches_requested_context_body':False}):
            with self.subTest(change=change),self.assertRaises(ConsoleError):
                require_safe_context(dict(value,**change))

    def test_missing_unknown_or_bool_like_values_fail_closed(self):
        for value in (None,{},dict(stable=1),dict(association='unknown')):
            with self.subTest(value=value),self.assertRaises(ConsoleError):
                require_safe_context(value)
