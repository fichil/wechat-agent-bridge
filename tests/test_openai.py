"""OpenAI profile contract tests: synthetic keys, injected HTTP, no paid API."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.console import main
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.openai_config import configure_openai, load_openai_configuration, OPENAI_TEST_MODEL
from wechat_agent_bridge.providers import CompatibleProvider, DEFAULT_CONFIG, MockProvider
from tests.test_console import SyntheticProtector


SYNTHETIC_KEY = "sk-SYNTHETIC-TEST-ONLY"


class ImportTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.socket", side_effect=AssertionError("external network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/".env.local"

    def write(self, value):
        self.path.write_text(value, encoding="utf-8")

    def test_literal_import_ignores_process_key_and_unrelated_settings(self):
        for value in (SYNTHETIC_KEY, '"'+SYNTHETIC_KEY+'"', "'"+SYNTHETIC_KEY+"'"):
            with self.subTest(quoted=value != SYNTHETIC_KEY):
                self.write("\ufeff# synthetic fixture\nOPENAI_BASE_URL=https://example.invalid\nexport OPENAI_API_KEY = "+value+"\n")
                with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-UNRELATED"}):
                    config = load_openai_configuration(self.path)
                    self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-UNRELATED")
                self.assertEqual(config["api_key"], SYNTHETIC_KEY)
                self.assertEqual(config["base_url"], "https://api.openai.com/v1")
                self.assertEqual(config["model"], OPENAI_TEST_MODEL)
                self.assertEqual((config["hourly_limit"], config["daily_limit"]), (2, 2))
                self.assertFalse(any(config["capabilities"].values()))
                self.assertEqual(config["vision_model"], "")
                self.assertEqual(config["transcription_model"], "")

    def test_missing_key_never_falls_back_to_process_environment(self):
        self.write("OTHER=value\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY": SYNTHETIC_KEY}):
            with self.assertRaisesRegex(ConsoleError, "provider_api_key_missing"):
                load_openai_configuration(self.path)

    def test_invalid_duplicate_empty_or_evaluated_keys_are_rejected_without_echo(self):
        for assignment in ("", "'"+SYNTHETIC_KEY, "$(Write-Output secret)", "${OPENAI_API_KEY}",
                           SYNTHETIC_KEY+"\nOPENAI_API_KEY="+SYNTHETIC_KEY, "x"*2049):
            self.write("OPENAI_API_KEY="+assignment)
            with self.assertRaises(ConsoleError) as caught:
                load_openai_configuration(self.path)
            self.assertEqual(str(caught.exception), "openai_env_key_invalid")
            self.assertNotIn(SYNTHETIC_KEY, str(caught.exception))

    def test_missing_oversized_and_non_utf8_files_are_safe_errors(self):
        with self.assertRaisesRegex(ConsoleError, "openai_env_file_unavailable"):
            load_openai_configuration(self.path)
        self.write("x"*65537)
        with self.assertRaisesRegex(ConsoleError, "openai_env_file_invalid"):
            load_openai_configuration(self.path)
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(ConsoleError, "openai_env_file_unavailable"):
            load_openai_configuration(self.path)

    def test_import_pauses_masks_key_and_preserves_existing_call_count(self):
        self.write("OPENAI_API_KEY="+SYNTHETIC_KEY)
        store = ConsoleStore(":memory:", SyntheticProtector())
        self.addCleanup(store.close)
        service = ConsoleService(MockConsoleAdapter(), store, provider_factory=lambda c: MockProvider(), clock=lambda: 100)
        service.resume()
        configure_openai(service, self.path)
        self.assertTrue(service.paused)
        self.assertFalse(service.grants)
        self.assertTrue(service.configuration()["has_api_key"])
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(service.configuration()))
        self.assertEqual(store.get("settings", "provider")["api_key"], SYNTHETIC_KEY)
        service.test_provider("text")
        configure_openai(service, self.path)
        self.assertTrue(service.config["capabilities"]["text"])
        self.assertEqual(store.usage(100)["day"], 1)
        service.adapter.simulation_only = False
        with self.assertRaisesRegex(ConsoleError, "openai_test_requires_mock"):
            configure_openai(service, self.path)

    def test_cli_rejects_missing_file_or_windows_before_opening_store(self):
        for args in (["--openai-env-file", str(self.path)],
                     ["--adapter", "windows", "--openai-env-file", str(self.path)]):
            with patch("wechat_agent_bridge.console.ConsoleStore") as store, redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code, 2)
                store.assert_not_called()
                self.assertNotIn(SYNTHETIC_KEY, output.getvalue())


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = {**DEFAULT_CONFIG, "provider": "compatible", "base_url": "https://api.openai.com/v1",
                       "model": OPENAI_TEST_MODEL, "api_key": SYNTHETIC_KEY}
        guard = patch("socket.socket", side_effect=AssertionError("external network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    @staticmethod
    def response(content, finish="stop"):
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}

    def test_text_check_generates_validated_reply_in_one_bounded_request(self):
        transport = Mock(return_value=self.response('{"reply":"测试成功"}'))
        self.assertEqual(CompatibleProvider(self.config, transport).test(), "测试成功")
        transport.assert_called_once()
        path, body, mime = transport.call_args.args
        payload = json.loads(body)
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(mime, "application/json")
        self.assertEqual(payload["max_tokens"], 128)
        self.assertEqual(payload["model"], OPENAI_TEST_MODEL)
        self.assertFalse(payload["store"])
        self.assertFalse(payload["stream"])
        self.assertIn("合成测试", payload["messages"][1]["content"])
        self.assertNotIn(SYNTHETIC_KEY, body.decode())

    def test_invalid_silent_refused_or_truncated_output_is_not_success(self):
        for content in ("OK", '{"reply":null}', '{"reply":""}', '{"reply":1}',
                        '{"reply":"   "}', '{"shell":"do something"}', "[]", None,
                        json.dumps({"reply": "x"*501})):
            transport = Mock(return_value=self.response(content))
            with self.assertRaises(ConsoleError):
                CompatibleProvider(self.config, transport).test()
            transport.assert_called_once()
        transport = Mock(return_value=self.response('{"reply":"test"}', finish="length"))
        with self.assertRaisesRegex(ConsoleError, "provider_invalid_response"):
            CompatibleProvider(self.config, transport).test()

    def test_missing_official_key_is_rejected_before_http(self):
        with self.assertRaisesRegex(ConsoleError, "provider_api_key_missing"):
            CompatibleProvider({**self.config, "api_key": ""})

    def test_http_errors_are_redacted_without_retry(self):
        for status, error_body, reason in (
            (401, {}, "provider_auth_failed"), (403, {}, "provider_forbidden"),
            (429, {"error": {"code": "insufficient_quota", "message": SYNTHETIC_KEY}}, "provider_quota_exhausted"),
            (429, {"error": {"code": "rate_limit_exceeded"}}, "provider_rate_limited"),
            (429, {"error": None}, "provider_rate_limited"), (500, {}, "provider_http_failed"),
        ):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", status,
                                                            SYNTHETIC_KEY, {}, io.BytesIO(json.dumps(error_body).encode()))
            with patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ConsoleError) as caught:
                    CompatibleProvider(self.config)._http_direct("/chat/completions", b"{}")
            self.assertEqual(str(caught.exception), reason)
            opener.open.assert_called_once()
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

    def test_timeout_terminates_worker_and_never_retries(self):
        context, parent, child, process = Mock(), Mock(), Mock(), Mock()
        context.Pipe.return_value = (parent, child)
        context.Process.return_value = process
        parent.poll.return_value = False
        process.is_alive.return_value = True
        with patch("wechat_agent_bridge.providers.multiprocessing.get_context", return_value=context):
            with self.assertRaisesRegex(ConsoleError, "provider_unreachable_or_timeout"):
                CompatibleProvider(self.config).test()
        parent.poll.assert_called_once_with(30)
        process.start.assert_called_once()
        process.terminate.assert_called_once()
        parent.close.assert_called_once()

    def test_failed_checks_consume_budget_and_keep_console_paused(self):
        store = ConsoleStore(":memory:", SyntheticProtector())
        self.addCleanup(store.close)
        transport = Mock(side_effect=ConsoleError("provider_unreachable_or_timeout"))
        service = ConsoleService(MockConsoleAdapter(), store,
                                 provider_factory=lambda c: CompatibleProvider(c, transport) if c["provider"] != "mock" else MockProvider(),
                                 clock=lambda: 100)
        service.save_configuration({**self.config, "hourly_limit": 2, "daily_limit": 2})
        service.resume()
        for _ in range(2):
            with self.assertRaisesRegex(ConsoleError, "provider_unreachable_or_timeout"):
                service.test_provider("text")
            self.assertTrue(service.paused)
            self.assertFalse(service.config["capabilities"]["text"])
        with self.assertRaisesRegex(ConsoleError, "provider_budget_exhausted"):
            service.test_provider("text")
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(store.usage(100)["day"], 2)
        self.assertEqual(service.adapter.sent, [])


if __name__ == "__main__":
    unittest.main()
