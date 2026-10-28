"""Synthetic metadata and event evidence; never opens a client."""
import unittest
from unittest.mock import Mock

from wechat_agent_bridge.adapters.semantic_probe import inspect_patterns, candidate_decision, inspect_legacy_origin
from wechat_agent_bridge.adapters.desktop_events import EventEvidence, DesktopEventObserver
from wechat_agent_bridge.adapters.background import BackgroundSender
from wechat_agent_bridge.console_models import ConsoleError


class SemanticTests(unittest.TestCase):
    def test_absent_patterns_never_query_or_write(self):
        element = Mock()
        element.GetCurrentPropertyValue.return_value = False
        report = inspect_patterns(element)
        self.assertEqual(candidate_decision(report), "no_exposed_alternative")
        element.GetCurrentPattern.assert_not_called()

    def test_present_does_not_grant_write(self):
        element = Mock()
        element.GetCurrentPropertyValue.return_value = True
        report = inspect_patterns(element)
        self.assertEqual(candidate_decision(report), "needs_independent_documented_implementation_evidence")
        self.assertTrue(all(r["write"] == "unverified" for r in report.values()))
        element.SetValue.assert_not_called()
        element.GetUnderlyingObjectModel.assert_not_called()

    def test_exception_and_unknown_property_are_inconclusive(self):
        element = Mock()
        for value in (object(), None):
            element.GetCurrentPropertyValue.return_value = value
            self.assertEqual(candidate_decision(inspect_patterns(element)), "probe_inconclusive")
        element.GetCurrentPropertyValue.side_effect = RuntimeError("private text")
        self.assertNotIn("private text", str(inspect_patterns(element)))

    def test_advertised_but_missing_interface_is_failed(self):
        element = Mock()
        element.GetCurrentPropertyValue.return_value = True
        element.GetCurrentPattern.return_value = None
        self.assertEqual(candidate_decision(inspect_patterns(element)), "probe_inconclusive")

    def test_transient_focus_return_is_still_failure(self):
        evidence = EventEvidence()
        evidence.record("foreground", True)
        evidence.record("foreground", False)
        with self.assertRaisesRegex(ConsoleError, "change_observed"):
            evidence.check()
        self.assertEqual(len(evidence.events), 2)

    def test_takeover_and_listener_failure_fail_closed(self):
        evidence = EventEvidence()
        evidence.record("focus", True)
        with self.assertRaises(ConsoleError): evidence.check()
        evidence = EventEvidence(); evidence.failed = True
        with self.assertRaisesRegex(ConsoleError, "observation_failed"): evidence.check()
        with self.assertRaises(ConsoleError): DesktopEventObserver(1).check()

    def test_event_overflow_fails_closed(self):
        evidence = EventEvidence()
        for _ in range(130): evidence.record("focus", False)
        self.assertEqual(len(evidence.events), 128)
        self.assertTrue(evidence.failed)

    def test_live_port_requires_observer(self):
        port = Mock(simulation_only=False)
        result = BackgroundSender(port).run("a", "c", "text", "r")
        self.assertEqual(result["reason"], "desktop_event_observation_required")
        port.set_value.assert_not_called()

    def test_event_failure_prevents_write(self):
        port = Mock(simulation_only=False)
        observer = Mock()
        observer.check.side_effect = ConsoleError("desktop_event_change_observed")
        result = BackgroundSender(port, observer).run("a", "c", "text", "r")
        self.assertFalse(result["attempted"])
        port.set_value.assert_not_called()

    def test_null_native_accessible_does_not_allow_new_write(self):
        interface = Mock()
        interface.GetIAccessible.return_value = None
        origin = inspect_legacy_origin(interface)
        patterns = {"LegacyIAccessible": {"status": "available"}, "ObjectModel": {"status": "unsupported"}}
        self.assertEqual(candidate_decision(patterns, origin), "blocked_no_independent_native_write_evidence")
        interface.SetValue.assert_not_called()

    def test_native_pointer_does_not_prove_independent_write(self):
        interface = Mock()
        self.assertEqual(inspect_legacy_origin(interface)["independent_write_path"], "unverified")
        interface.GetIAccessible.side_effect = RuntimeError("secret")
        self.assertEqual(inspect_legacy_origin(interface)["status"], "probe_failed")

    def test_event_after_invoke_is_unknown_and_never_clears(self):
        from test_background import FakeSemantic
        port = FakeSemantic()
        evidence = EventEvidence()
        observer = Mock()
        observer.check.side_effect = evidence.check
        port.invoke_send = Mock(side_effect=lambda: evidence.record("foreground", True))
        result = BackgroundSender(port, observer).run("synthetic-account", "synthetic-chat", "synthetic reply", "r")
        self.assertEqual(result["status"], "unknown")
