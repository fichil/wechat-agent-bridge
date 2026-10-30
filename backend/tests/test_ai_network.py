import json
import unittest
from unittest.mock import patch

import httpx

from backend.app.ai_provider import Provider
from backend.app.conversations import ConversationError


class AiNetworkTests(unittest.TestCase):
    def generate_after(self, provider, elapsed):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={
                "status": "completed", "output": [{"type": "message", "content": [{
                    "type": "output_text", "text": json.dumps({"memory": "updated", "reply": ""})}]}]})

        profile = dict(provider=provider, protocol="openai_responses", base_url="https://synthetic.invalid/v1", model="synthetic")
        envelope = dict(instructions="json", messages=[], memory="")
        with patch("backend.app.ai_provider.time") as clock:
            clock.monotonic.side_effect = [0, elapsed]
            result = Provider(httpx.MockTransport(respond)).generate(profile, "synthetic-secret", envelope, "schema")
        return result, calls

    def test_grok_accepts_response_after_old_ninety_second_limit(self):
        result, calls = self.generate_after("grok", 120)
        self.assertEqual(result["memory"], "updated")
        self.assertEqual(len(calls), 1)
        timeout = calls[0].extensions["timeout"]
        self.assertEqual(timeout["read"], 900)
        self.assertEqual(timeout["connect"], 10)
        self.assertEqual(timeout["write"], 90)

    def test_other_providers_keep_existing_response_budget(self):
        with self.assertRaises(ConversationError) as error:
            self.generate_after("openai", 120)
        self.assertEqual(error.exception.code, "ai_network_error")
        self.assertIn("90 秒", error.exception.message)

    def test_grok_still_rejects_response_beyond_its_budget(self):
        with self.assertRaises(ConversationError) as error:
            self.generate_after("grok", 901)
        self.assertEqual(error.exception.code, "ai_network_error")
        self.assertIn("900 秒", error.exception.message)

    def test_network_errors_are_specific_redacted_and_never_replayed(self):
        cases = [(httpx.ConnectTimeout, "连接建立超时"), (httpx.ReadTimeout, "读取上限 900 秒"),
                 (httpx.WriteTimeout, "发送 AI 请求超时"), (httpx.PoolTimeout, "连接资源超时"),
                 (httpx.ProxyError, "代理连接失败"), (httpx.ConnectError, "TLS 连接失败"),
                 (httpx.RemoteProtocolError, "响应协议异常"), (OSError, "系统调用失败")]
        profile = dict(provider="grok", protocol="openai_responses", base_url="https://synthetic.invalid/v1", model="synthetic")
        for exception, expected in cases:
            with self.subTest(exception=exception.__name__):
                calls = []

                def fail(request):
                    calls.append(request)
                    raise exception("private-error-secret")

                with self.assertRaises(ConversationError) as error:
                    Provider(httpx.MockTransport(fail)).generate(profile, "private-error-secret",
                        dict(instructions="json", messages=[], memory=""), "schema")
                self.assertEqual(error.exception.code, "ai_network_error")
                self.assertIn(expected, error.exception.message)
                self.assertNotIn("private-error-secret", str(error.exception))
                self.assertEqual(len(calls), 1)
