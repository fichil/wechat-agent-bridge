"""Persona wiring and configuration regression tests; synthetic data only."""

import http.client
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_console import SyntheticProtector
from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.console_http import ConsoleServer
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.providers import CompatibleProvider, DEFAULT_CONFIG, validate_config


class PersonaProviderTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.socket", side_effect=AssertionError("external network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.config = {**DEFAULT_CONFIG, "provider": "compatible",
                       "base_url": "https://example.invalid/v1", "model": "synthetic"}
        self.transport = Mock(return_value={"choices": [{"message": {"content": '{"reply":"鲸鱼娘"}'}}]})

    def payload(self):
        return json.loads(self.transport.call_args.args[1])

    def test_default_card_is_loaded_and_sent_with_untrusted_context_separate(self):
        card = (Path(__file__).resolve().parents[1] / "wechat_agent_bridge" / "personas" /
                "xiaojingyu_wechat.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(validate_config({})["system_prompt"], card)
        self.assertTrue(0 < len(card) <= 10000)
        provider = CompatibleProvider(self.config, self.transport)
        self.assertEqual(provider.reply("synthetic memory", [{"text": "synthetic message"}]), "鲸鱼娘")
        messages = self.payload()["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertTrue(messages[0]["content"].startswith(card + "\n"))
        self.assertNotIn("synthetic message", messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"]), {
            "conversation_memory": "synthetic memory", "untrusted_messages": [{"text": "synthetic message"}]})

    def test_custom_prompt_overrides_default_in_request(self):
        provider = CompatibleProvider({**self.config, "system_prompt": "Synthetic custom persona"}, self.transport)
        provider.reply("", [])
        instructions = self.payload()["messages"][0]["content"]
        self.assertTrue(instructions.startswith("Synthetic custom persona\n"))
        self.assertNotIn("小鲸鱼", instructions)

    def test_silence_remains_supported_but_arrays_and_long_replies_are_rejected(self):
        provider = CompatibleProvider(self.config, self.transport)
        for reply in (None, ["first", "second"], "x" * 501):
            with self.subTest(reply_type=type(reply).__name__):
                self.transport.return_value = {"choices": [{"message": {"content": json.dumps({"reply": reply})}}]}
                if reply is None:
                    self.assertIsNone(provider.reply("", []))
                else:
                    with self.assertRaisesRegex(ConsoleError, "invalid_reply_output"):
                        provider.reply("", [])

    def test_summary_instructions_are_independent_of_persona(self):
        self.transport.return_value = {"choices": [{"message": {"content": "合成事实摘要"}}]}
        provider = CompatibleProvider(self.config, self.transport)
        self.assertEqual(provider.summarize("old facts", [{"text": "new facts"}]), "合成事实摘要")
        default_instructions = self.payload()["messages"][0]["content"]
        provider = CompatibleProvider({**self.config, "system_prompt": "Another persona"}, self.transport)
        provider.summarize("old facts", [{"text": "new facts"}])
        self.assertEqual(self.payload()["messages"][0]["content"], default_instructions)
        self.assertNotIn("小鲸鱼", default_instructions)


class PersonaConfigurationTests(unittest.TestCase):
    def test_authenticated_prompt_update_preserves_settings_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            path = Path(directory) / "synthetic.sqlite3"
            store = ConsoleStore(path, SyntheticProtector())
            cleanup.callback(store.close)
            original = validate_config({"provider": "compatible", "base_url": "https://example.invalid/v1",
                                        "api_key": "SYNTHETIC-KEY", "model": "synthetic",
                                        "system_prompt": "Existing custom persona", "max_reply_chars": 123,
                                        "capabilities": {"text": True, "vision": True, "transcription": True}})
            store.put("settings", "provider", original)
            store.put("grant", "synthetic-account", {"account_id": "synthetic-account", "grant": {
                "read": True, "upload": True, "reply": False, "paused": False,
                "destination": original["base_url"], "excluded": []}})
            store.reserve_call(10000, 120, 1000)
            store.claim("synthetic-unknown", "synthetic-account", "synthetic-chat", "synthetic text")
            store.finish("synthetic-unknown", {"status": "unknown", "reason": "synthetic", "attempted": True})
            store.put("send_block", "synthetic-block", {"reason": "unknown_send_requires_review"})
            snapshots = {table: store.db.execute("SELECT * FROM " + table).fetchall()
                         for table in ("calls", "actions", "messages", "seen", "assets")}
            grants, blocks = store.values("grant"), store.values("send_block")
            # Real provider construction is allowed; any attempted HTTP request fails the test.
            with patch.object(CompatibleProvider, "_http", side_effect=AssertionError("no model calls")) as model_http:
                adapter = MockConsoleAdapter()
                service = ConsoleService(adapter, store)
                self.assertEqual(service.config, original)
                service.paused = False
                server = ConsoleServer(service)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                def request(method, endpoint, body=None, headers=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        connection.request(method, endpoint,
                                           json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                                           {"Origin": server.origin, "Content-Type": "application/json", **(headers or {})})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        return dict(response.getheaders()), json.loads(response.read())
                    finally:
                        connection.close()

                try:
                    headers, session = request("POST", "/api/session", {"token": server.bootstrap})
                    auth = {"Cookie": headers["Set-Cookie"].split(";")[0], "X-CSRF-Token": session["csrf"]}
                    prompt = DEFAULT_CONFIG["system_prompt"]
                    _, saved = request("POST", "/api/config", {"system_prompt": prompt}, auth)
                    _, readback = request("GET", "/api/config", headers=auth)
                    self.assertEqual(saved, readback)
                    self.assertEqual(readback["system_prompt"], prompt)
                    self.assertTrue(readback["has_api_key"])
                    self.assertEqual(readback["api_key"], "")
                    self.assertTrue(service.paused)
                    expected = {**original, "system_prompt": prompt,
                                "capabilities": {"text": False, "vision": False, "transcription": False}}
                    self.assertEqual(service.config, expected)
                    self.assertEqual(service.provider.config, expected)
                    self.assertEqual(store.get("settings", "provider"), expected)
                    self.assertEqual(store.values("grant"), grants)
                    self.assertEqual(store.values("send_block"), blocks)
                    for table, rows in snapshots.items():
                        self.assertEqual(store.db.execute("SELECT * FROM " + table).fetchall(), rows)
                    self.assertEqual(adapter.sent, [])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
                    service.close()
                # Reopen the file, not just another service sharing the same connection.
                with_store = ConsoleStore(path, SyntheticProtector())
                try:
                    restarted = ConsoleService(MockConsoleAdapter(), with_store)
                    self.assertTrue(restarted.paused)
                    self.assertEqual(restarted.config, expected)
                    restarted.close()
                finally:
                    with_store.close()
                model_http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
