import time
import threading
import unittest
from unittest.mock import Mock
import test_conversations as fixtures
from wechat_account_probe.models import ProbeError


def message(index=1):
    return dict(_key=[index, 1780000000, "message/message_0.db", index],
        _ref=dict(shard="message/message_0.db", local_id=index, server_id=str(9007199254740993+index)),
        type="text", sent_at="2026-10-28T00:00:00Z", content=dict(text="合成历史"),
        sender=dict(nickname="合成昵称", remark="备注", wxid="wxid_synthetic_0", wechat_id=None, status="complete"),
        is_self=True, issues=[])


class MessageApiTests(unittest.TestCase):
    post = fixtures.ConversationApiTests.post
    refresh = fixtures.ConversationApiTests.refresh
    tearDown = fixtures.ConversationApiTests.tearDown
    def setUp(self):
        fixtures.ConversationApiTests.setUp(self)
        self.refresh()
        self.conversation = self.post("conversations/query").json()["items"][0]["conversation_id"]
        self.reader.messages = Mock(return_value=dict(items=[message()], has_more=True, issues=[], status="complete"))
        self.reader.message_image = Mock(return_value=dict(status="unavailable", reason="image_not_downloaded"))

    def history(self, **kwargs):
        return self.post("message-reads", conversation_id=self.conversation, **kwargs)

    def completed(self, response):
        task_id = response.json()["task_id"]
        for _ in range(200):
            response = self.post("message-read-tasks/query", task_id=task_id)
            if response.status_code != 200 or response.json()["status"] not in ("queued", "running"):
                return response
            time.sleep(.005)
        self.fail("message task timeout")

    def test_history_json_contract_cursor_scope_and_large_ids(self):
        response = self.history()
        self.assertEqual(response.status_code, 202)
        final = self.completed(response)
        page = final.json()["page"]
        self.assertEqual(final.headers["cache-control"], "no-store")
        self.assertEqual(page["items"][0]["sender"]["nickname"], "合成昵称")
        self.assertTrue(page["items"][0]["is_self"])
        self.assertNotIn("message_0.db", final.text)
        self.assertNotIn("9007199254740994", final.text)
        self.assertEqual(self.history(cursor="invented").status_code, 409)
        other = self.post("message-read-tasks/query", self.ids[1], task_id=final.json()["task_id"])
        self.assertEqual(other.status_code, 404)
        second = self.completed(self.history(cursor=page["older_cursor"]))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.reader.messages.call_args.args[1]["direction"], "older")

    def test_image_reference_scope_and_identity_revalidation(self):
        final = self.completed(self.history()).json()
        mid = final["page"]["items"][0]["message_id"]
        image = self.completed(self.post("message-image-reads", conversation_id=self.conversation, message_id=mid))
        self.assertEqual(image.json()["image"]["reason"], "image_not_downloaded")
        self.assertEqual(self.post("message-image-reads", conversation_id=self.conversation, message_id="fake").status_code, 404)
        self.reader.validation_failure = "account_changed_during_read"
        response = self.post("message-read-tasks/query", task_id=final["task_id"])
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["page"])

    def test_pending_reuse_health_and_failure_not_empty_page(self):
        release = threading.Event()
        def slow(*args):
            release.wait(2)
            raise ProbeError("message_schema_unsupported")
        self.reader.messages.side_effect = slow
        first, second = self.history(), self.history()
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        release.set()
        result = self.completed(first).json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "message_schema_unsupported")
        self.assertIsNone(result["page"])

    def test_required_json_and_parameter_validation(self):
        self.assertEqual(self.client.post("/api/v1/message-reads", content="{}").status_code, 415)
        for value in (0, 101, True, "50"):
            self.assertEqual(self.history(page_size=value).status_code, 422)
        self.assertEqual(self.history(unexpected="private-path").status_code, 422)

    def test_cached_query_never_calls_the_source(self):
        self.reader.messages.return_value.update(has_more=False)
        first = self.completed(self.history()).json()["page"]
        self.reader.messages.reset_mock()
        response = self.post("messages/query", conversation_id=self.conversation)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], first["items"])
        self.reader.messages.assert_not_called()

    def test_expired_and_cross_account_cursor_and_reference_rejected(self):
        page = self.completed(self.history()).json()["page"]
        self.refresh(self.ids[1])
        other = self.post("conversations/query", self.ids[1]).json()["items"][0]["conversation_id"]
        self.assertEqual(self.post("message-reads", self.ids[1], conversation_id=other, cursor=page["older_cursor"]).status_code, 409)
        self.assertEqual(self.post("message-image-reads", self.ids[1], conversation_id=other, message_id=page["items"][0]["message_id"]).status_code, 404)
        def expire():
            with self.app.state.store.db:
                self.app.state.store.db.execute("UPDATE cursors SET expires=0")
        self.app.state.store.call(expire)
        self.assertEqual(self.history(cursor=page["older_cursor"]).json()["code"], "cursor_expired")

    def test_failed_shards_cannot_claim_complete_history(self):
        self.reader.messages.return_value.update(issues=["message_shard_missing"], status="partial", retry_required=True)
        page = self.completed(self.history()).json()["page"]
        self.assertTrue(page["needs_sync"])
        self.assertNotEqual(page["coverage"], "complete")
