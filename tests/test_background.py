"""Synthetic native ports only: no live processes, UI input or paid APIs."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch
import time

from wechat_agent_bridge.adapters.accessibility_gate import AccessibilityGate, Fingerprint, VERSION, DLL_SHA256, FLAG_RVA
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.background import BackgroundSender, DesktopSnapshot
from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
from wechat_agent_bridge.console_models import Account
from wechat_agent_bridge.acceptance import FIXED_TEXT


class FakeNative:
    def __init__(self):
        self.fp = Fingerprint(7, 8, 12345, VERSION, DLL_SHA256, 0x100000000, FLAG_RVA+4096, FLAG_RVA)
        self.value, self.running, self.writes = 0, True, []
        self.fail_restore = self.bad_readback = False
    def fingerprint(self): return self.fp
    def alive(self): return self.running
    def validate_region(self): pass
    def read(self): return self.value
    def write(self, value):
        self.writes.append(value)
        if value == 0 and self.fail_restore:
            raise ConsoleError("synthetic_restore_failed")
        if not self.bad_readback:
            self.value = value


class GateTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.journal = []
        self.gate = AccessibilityGate(self.native, allowed=True, save=self.journal.append)

    def test_opt_in_required(self):
        self.gate.allowed = False
        with self.assertRaisesRegex(ConsoleError, "not_enabled"):
            self.gate.enable(self.native.fp, lambda: True)
        self.assertEqual(self.native.writes, [])

    def test_wrong_version_hash_address_or_bounds_never_write(self):
        for changes in ({"version":"4.1.15.53"}, {"dll_sha256":"wrong"}, {"flag_rva":FLAG_RVA+1}, {"module_size":2}):
            native = FakeNative(); native.fp = replace(native.fp, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ConsoleError, "fingerprint_mismatch"):
                AccessibilityGate(native, allowed=True).enable(native.fp, lambda: True)
            self.assertEqual(native.writes, [])

    def test_identity_changed_before_enable(self):
        old = self.native.fp
        self.native.fp = replace(old, created=999)
        with self.assertRaisesRegex(ConsoleError, "identity_changed"):
            self.gate.enable(old, lambda: True)
        self.assertEqual(self.native.writes, [])

    def test_journal_precedes_write_and_restore(self):
        original = self.native.write
        def write(value):
            self.assertIsNotNone(self.journal[-1])
            original(value)
        self.native.write = write
        self.gate.enable(self.native.fp, lambda: True)
        self.assertEqual(self.gate.state, "enabled")
        self.gate.restore()
        self.assertEqual(self.native.writes, [1, 0])
        self.assertIsNone(self.journal[-1])

    def test_control_probe_failure_and_timeout_restore(self):
        for probe in (lambda: False, lambda: (_ for _ in ()).throw(TimeoutError())):
            with self.subTest(probe=probe), self.assertRaises((ConsoleError, TimeoutError)):
                self.gate.enable(self.native.fp, probe)
            self.assertEqual(self.native.value, 0)
            self.assertIsNone(self.gate.record)

    def test_readback_failure_restores_and_does_not_claim_support(self):
        self.native.bad_readback = True
        with self.assertRaisesRegex(ConsoleError, "readback_failed"):
            self.gate.enable(self.native.fp, lambda: True)
        self.assertEqual(self.gate.state, "restored")

    def test_restore_failure_keeps_recovery_record(self):
        self.gate.enable(self.native.fp, lambda: True)
        self.native.fail_restore = True
        with self.assertRaises(ConsoleError): self.gate.restore()
        self.assertEqual(self.gate.state, "restore_failed")
        self.assertIsNotNone(self.gate.record)
        self.assertIsNotNone(self.journal[-1])

    def test_reused_pid_never_written_and_journal_retained(self):
        self.gate.enable(self.native.fp, lambda: True)
        self.native.fp = replace(self.native.fp, created=999)
        with self.assertRaisesRegex(ConsoleError, "identity_mismatch"): self.gate.restore()
        self.assertEqual(self.native.writes, [1])

    def test_exited_process_not_touched(self):
        self.gate.enable(self.native.fp, lambda: True)
        self.native.running = False
        self.gate.restore()
        self.assertEqual(self.native.writes, [1])
        self.assertEqual(self.gate.state, "process_exited")

    def test_already_enabled_not_owned(self):
        self.native.value = 1
        self.gate.enable(self.native.fp, lambda: True)
        self.gate.restore()
        self.assertEqual(self.native.writes, [])

    def test_changed_byte_not_overwritten(self):
        self.gate.enable(self.native.fp, lambda: True)
        self.native.value = 2
        with self.assertRaisesRegex(ConsoleError, "value_changed"): self.gate.restore()
        self.assertEqual(self.native.writes, [1])

    def test_crash_recovery_uses_exact_fingerprint(self):
        self.gate.enable(self.native.fp, lambda: True)
        record = self.gate.record
        recovered = AccessibilityGate(self.native, allowed=False, save=self.journal.append)
        recovered.recover(record)
        self.assertEqual(self.native.value, 0)
        self.assertIsNone(self.journal[-1])

    def test_journal_failure_never_mutates_process(self):
        def fail(record): raise OSError("synthetic disk failure")
        self.gate.save = fail
        with self.assertRaises(OSError): self.gate.enable(self.native.fp, lambda: True)
        self.assertEqual(self.native.writes, [])

    def test_worker_timeout_restores_parent_owned_gate(self):
        from wechat_agent_bridge.adapters.windows import WindowsAdapter
        adapter = WindowsAdapter(timeout=0.01)
        adapter.gate = self.gate
        self.gate.enable(self.native.fp, lambda: True)
        adapter.process, adapter.connection = Mock(), Mock()
        adapter.process.is_alive.return_value = True
        adapter.connection.poll.return_value = False
        with self.assertRaisesRegex(ConsoleError, "windows_operation_timeout"):
            adapter.discover()
        self.assertEqual(self.native.value, 0)

    def test_recorded_focus_failure_prevents_worker_send(self):
        from wechat_agent_bridge.adapters.windows import WindowsAdapter
        store = Mock(); store.get.return_value = {"set_value":"blocked_focus_changed"}
        adapter = WindowsAdapter(gate_store=store)
        adapter._call = Mock(side_effect=AssertionError("must not reach live client"))
        self.assertFalse(adapter.send("synthetic", "synthetic-chat", "text", "request")["attempted"])
        adapter._call.assert_not_called()


class FakeSemantic:
    simulation_only = True
    def __init__(self):
        self.current = ("synthetic-account", "synthetic-chat")
        self.desktop = DesktopSnapshot(1, 2, 3)
        self.text, self.calls, self.supported, self.confirmed = "", [], True, True
        self.after_set = lambda: None
        self.after_invoke = lambda: None
    def capable(self): return self.supported
    def identity(self): return self.current
    def snapshot(self): return self.desktop
    def draft(self): return self.text
    def own_message_ids(self): return {"old-own-message"}
    def set_value(self, text):
        self.calls.append(("set", text)); self.text = text; self.after_set()
    def invoke_send(self):
        self.calls.append(("invoke",)); self.after_invoke()
    def confirm_own_message(self, text, prior): return self.confirmed


class BackgroundTests(unittest.TestCase):
    def setUp(self): self.port = FakeSemantic()
    def run_send(self, checkpoint=lambda: None):
        return BackgroundSender(self.port).run("synthetic-account", "synthetic-chat", "synthetic reply", "request-1", checkpoint)

    def test_success_requires_own_message_readback(self):
        self.assertEqual(self.run_send()["status"], "ui_confirmed")
        self.assertEqual(self.port.calls, [("set", "synthetic reply"), ("invoke",)])

    def test_target_mismatch_draft_and_unsupported_have_no_ui_calls(self):
        for field, value in (("current", ("wrong", "synthetic-chat")), ("text", "user draft"), ("supported", False)):
            self.port = FakeSemantic(); setattr(self.port, field, value)
            self.assertFalse(self.run_send()["attempted"])
            self.assertEqual(self.port.calls, [])

    def test_foreground_wechat_and_lock_pause_without_touching_draft(self):
        for change in ({"target_active":True}, {"locked":True}):
            self.port.desktop = replace(DesktopSnapshot(1, 2, 3), **change)
            self.assertFalse(self.run_send()["attempted"])
            self.assertEqual(self.port.calls, [])

    def test_focus_clipboard_or_takeover_changes_prevent_invoke_and_cleanup(self):
        for change in ({"foreground":9}, {"focus":9}, {"clipboard_sequence":9}, {"target_active":True}):
            self.port = FakeSemantic()
            self.port.after_set = lambda: setattr(self.port, "desktop", replace(self.port.desktop, **change))
            result = self.run_send()
            self.assertFalse(result["attempted"])
            self.assertEqual(self.port.calls, [("set", "synthetic reply")])

    def test_human_draft_change_preserved(self):
        self.port.after_set = lambda: setattr(self.port, "text", "user replacement")
        self.assertFalse(self.run_send()["attempted"])
        self.assertEqual(self.port.text, "user replacement")

    def test_setvalue_noop_never_invokes(self):
        self.port.after_set = lambda: setattr(self.port, "text", "")
        self.assertFalse(self.run_send()["attempted"])
        self.assertEqual(self.port.calls, [("set", "synthetic reply")])

    def test_invoke_noop_timeout_or_focus_change_is_unknown(self):
        for action in (lambda: None, lambda: (_ for _ in ()).throw(TimeoutError()),
                       lambda: setattr(self.port, "desktop", replace(self.port.desktop, foreground=9))):
            self.port = FakeSemantic(); self.port.confirmed = False; self.port.after_invoke = action
            result = self.run_send()
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(result["attempted"])
            self.assertEqual(len(self.port.calls), 2)

    def test_pause_after_model_result_prevents_any_write(self):
        def cancelled(): raise ConsoleError("operation_cancelled")
        self.assertFalse(self.run_send(cancelled)["attempted"])
        self.assertEqual(self.port.calls, [])


class DraftProbeTests(unittest.TestCase):
    def setUp(self):
        self.backend = WindowsUIA()
        self.backend.profile_verified_at = time.monotonic()
        self.backend.accounts["synthetic"] = Account("synthetic", "test", instance_id="7:8")
        self.value = ""
        self.edit = Mock()
        self.edit.element_info.automation_id = "chat_input_field"
        self.edit.get_value.side_effect = lambda: self.value
        self.edit.iface_value.SetValue.side_effect = lambda v: setattr(self, "value", v)
        root = Mock(); root.descendants.return_value = [self.edit]
        self.backend.instances["7:8"] = (root, VERSION)
        self.backend._target_metadata = Mock(return_value={"identity_verified": True, "target_index": 0,
            "draft_empty": True, "value_readonly": False, "session_runtime_id": [1], "input_runtime_id": [2]})

    def probe(self):
        with patch("wechat_agent_bridge.adapters.background.snapshot", return_value=DesktopSnapshot(1, 2, 3)):
            return self.backend.probe_background_draft("synthetic", ["group", "private"], FIXED_TEXT)

    def test_exact_set_and_cleanup_never_calls_invoke(self):
        result = self.probe()
        self.assertEqual(result["set_value"], "passed")
        self.assertTrue(result["draft_restored"])
        self.assertFalse(result["send_invoked"])
        self.assertEqual(self.value, "")
        self.edit.iface_invoke.Invoke.assert_not_called()

    def test_noop_set_value_is_explicitly_unsupported(self):
        self.edit.iface_value.SetValue.side_effect = lambda v: None
        result = self.probe()
        self.assertEqual(result["set_value"], "unsupported_no_exact_readback")
        self.assertTrue(result["draft_restored"])

    def test_existing_draft_and_stale_profile_block(self):
        self.value = "user draft"
        result = self.probe()
        self.assertIn("draft", result["reason"])
        self.edit.iface_value.SetValue.assert_not_called()
        self.backend.profile_verified_at = time.monotonic()-61
        with self.assertRaisesRegex(ConsoleError, "fresh_profile"):
            self.probe()
