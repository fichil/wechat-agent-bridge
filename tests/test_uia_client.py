"""Pure client-policy tests: no COM creation, windows, WeChat, or paid API."""
import threading
import unittest
from unittest.mock import Mock

from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
from wechat_agent_bridge.console_models import ConsoleError


class FakeAutomation:
    def __init__(self, initial=1, writable=True, read_error=False, write_error=False):
        self.value = initial
        self.writable = writable
        self.read_error = read_error
        self.write_error = write_error
        self.operations = []
        self.element = object()

    @property
    def AutoSetFocus(self):
        self.operations.append("read_focus_policy")
        if self.read_error:
            raise RuntimeError("private provider text must not escape")
        return self.value

    @AutoSetFocus.setter
    def AutoSetFocus(self, value):
        self.operations.append(("set_focus_policy", value))
        if self.write_error:
            raise RuntimeError("private provider text must not escape")
        if self.writable:
            self.value = value

    def ElementFromHandle(self, hwnd):
        self.operations.append(("element_from_handle", hwnd))
        return self.element


class BackgroundUIAClientTests(unittest.TestCase):
    def test_setting_is_read_back_before_any_element_is_requested(self):
        raw = FakeAutomation()
        client = BackgroundUIAClient(lambda: raw)
        self.assertTrue(client.initial_auto_focus)
        self.assertEqual(raw.operations, ["read_focus_policy", ("set_focus_policy", 0), "read_focus_policy"])
        self.assertIs(client.element_from_handle(123), raw.element)
        self.assertEqual(raw.operations[-2:], ["read_focus_policy", ("element_from_handle", 123)])

    def test_unsupported_client_has_no_fallback(self):
        factory = Mock(side_effect=RuntimeError("unsupported"))
        with self.assertRaisesRegex(ConsoleError, "^uia_automation2_unavailable$"):
            BackgroundUIAClient(factory)
        factory.assert_called_once_with()

    def test_ignored_setting_blocks_element_access(self):
        raw = FakeAutomation(writable=False)
        with self.assertRaisesRegex(ConsoleError, "^uia_auto_focus_not_disabled$"):
            BackgroundUIAClient(lambda: raw)
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "element_from_handle" for x in raw.operations))

    def test_getter_and_setter_failures_are_sanitized(self):
        for argument, reason in (("read_error", "read_failed"), ("write_error", "disable_failed")):
            with self.subTest(argument=argument):
                raw = FakeAutomation(**{argument: True})
                with self.assertRaisesRegex(ConsoleError, "^uia_auto_focus_" + reason + "$"):
                    BackgroundUIAClient(lambda: raw)

    def test_non_bool_com_values_are_not_treated_as_disabled(self):
        for initial in (None, "false", 0.0, object()):
            raw = FakeAutomation(initial=initial)
            with self.assertRaisesRegex(ConsoleError, "^uia_auto_focus_invalid_value$"):
                BackgroundUIAClient(lambda: raw)
            self.assertEqual(raw.operations, ["read_focus_policy"])

    def test_changed_policy_blocks_action_without_restoring_it(self):
        raw = FakeAutomation()
        client = BackgroundUIAClient(lambda: raw)
        raw.value = 1
        before = list(raw.operations)
        with self.assertRaisesRegex(ConsoleError, "^uia_auto_focus_not_disabled$"):
            client.element_from_handle(123)
        self.assertEqual(raw.operations, before + ["read_focus_policy"])

    def test_client_never_moves_between_threads(self):
        raw = FakeAutomation()
        client = BackgroundUIAClient(lambda: raw)
        errors = []
        def run():
            try:
                client.element_from_handle(123)
            except ConsoleError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(1)
        self.assertEqual(errors, ["uia_client_wrong_thread"])
        self.assertEqual(len(raw.operations), 3)

    def test_invalid_or_missing_element_cannot_escape(self):
        raw = FakeAutomation(initial=0)
        client = BackgroundUIAClient(lambda: raw)
        self.assertFalse(client.initial_auto_focus)
        for hwnd in (0, -1, True, "123", 2**64):
            with self.assertRaisesRegex(ConsoleError, "^uia_invalid_window_handle$"):
                client.element_from_handle(hwnd)
        raw.element = None
        with self.assertRaisesRegex(ConsoleError, "^uia_element_unavailable$"):
            client.element_from_handle(123)


if __name__ == "__main__":
    unittest.main()
