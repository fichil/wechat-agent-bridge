import io
import queue
import time
import unittest
from unittest.mock import Mock, patch

from backend.app.models import Account, Instance as ApiInstance
from wechat_account_probe.models import Instance, ProbeError
from wechat_conversations.client import LocalConversationReader, Worker
from wechat_conversations.reader import SessionReader


def account():
    return Account(account_id="synthetic", wxid="wxid_synthetic", status="complete", avatar_status="missing",
                   instances=[ApiInstance(pid=123, created="134000000000000001", session_id=1, version="4.1.15.56")])


class WorkerTests(unittest.TestCase):
    def test_worker_timeout_and_disconnect_return_constant_errors(self):
        worker = Worker.__new__(Worker)
        worker.instance = Instance(123, 134000000000000001, 1, "4.1.15.56")
        worker.wxid = "wxid_synthetic"
        worker.process = Mock(stdin=io.BytesIO())
        worker.responses = queue.Queue()
        with self.assertRaisesRegex(ProbeError, "conversation_timeout"):
            worker.call("read", .001)
        worker.responses.put(None)
        with self.assertRaisesRegex(ProbeError, "conversation_worker_disconnected"):
            worker.call("read", .001)

    def test_account_bound_reuse_failure_cleanup_idle_cleanup_and_shutdown(self):
        created = []

        def factory(instance, wxid):
            worker = Mock(instance=instance, wxid=wxid, used_at=time.monotonic())
            worker.process.poll.return_value = None
            worker.call.return_value = {"items": [], "issues": [], "status": "complete"}
            created.append(worker)
            return worker

        with patch("wechat_conversations.client.Worker", side_effect=factory):
            reader = LocalConversationReader()
            try:
                target = account()
                reader.read(target); reader.read(target)
                self.assertEqual(len(created), 1)
                target.instances[0].created = "134000000000000002"
                reader.read(target)
                created[0].close.assert_called_once()
                self.assertEqual(len(created), 2)
                created[1].call.side_effect = ProbeError("conversation_timeout")
                with self.assertRaises(ProbeError):
                    reader.read(target)
                created[1].close.assert_called_once()
                self.assertFalse(reader._workers)
                reader.read(target)
                created[2].used_at = time.monotonic() - 61
                reader._reap_idle()
                created[2].close.assert_called_once()
                self.assertFalse(reader._workers)
                reader.read(target)
            finally:
                reader.close()
            created[3].close.assert_called_once()

    def test_cached_reader_rechecks_current_identity_and_never_accepts_switched_account(self):
        reader = SessionReader(Instance(123, 100, 1, "4.1.15.56"), "wxid_synthetic")
        reader.memory = Mock()
        with patch("wechat_account_probe.memory.current_account", return_value="wxid_switched"):
            with self.assertRaisesRegex(ProbeError, "account_changed_during_read"):
                reader.bind(time.monotonic()+10)

    def test_no_ui_or_network_calls_in_conversation_runtime(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "wechat_conversations"
        source = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
        for forbidden in ("SetForegroundWindow", "SetCursorPos", "SendInput", "PostMessage", "SendMessage",
                          "SetClipboardData", "WriteProcessMemory", "CreateRemoteThread", "pyautogui",
                          "uiautomation", "requests.", "httpx.", "message_0.db"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
