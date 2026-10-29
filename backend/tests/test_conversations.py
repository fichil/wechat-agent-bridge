from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.conversation_models import Exclusions
from wechat_account_probe.models import Instance, aggregate, instance_result, ProbeError
from wechat_conversations.reader import CONTAINERS


def accounts():
    return aggregate([instance_result(Instance(100+i, 134000000000000001+i, 1, "4.1.15.56"),
                     wxid="wxid_synthetic_"+str(i), nickname="合成账号", status="complete") for i in range(2)])


def rows(count=1):
    categories = ["contact", *Exclusions.model_fields]
    return [dict(source_id=f"synthetic_{i}", name="合成联系人", category=categories[i % len(categories)],
                 is_container=categories[i % len(categories)] in {v[0] for v in CONTAINERS.values()},
                 summary="合成摘要", last_message_at="2026-10-28T00:00:00Z", unread_count=i, avatar=None)
            for i in range(count)]


class Reader:
    def __init__(self):
        self.calls = 0
        self.closed = False
        self.release = None
        self.failure = None
        self.validation_failure = None
        self.items = rows(160)
        self.active = 0
        self.peak = 0

    def read(self, account):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.release:
                self.release.wait(5)
            if self.failure:
                raise ProbeError(self.failure)
            return dict(items=self.items, issues=[], status="complete")
        finally:
            self.active -= 1

    def validate(self, account):
        if self.validation_failure:
            raise ProbeError(self.validation_failure)

    def close(self):
        self.closed = True


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.reader = Reader()
        self.app = create_app(accounts, conversation_reader=self.reader, settings_path=self.path)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        self.client.__enter__()
        scan = self.client.post("/api/v1/account-scans", json={}).json()
        for _ in range(200):
            if self.client.post('/api/v1/account-scans/query', json={'scan_id': scan['scan_id']}).json()["status"] != "running":
                break
            time.sleep(.005)
        self.ids = [a["account_id"] for a in self.client.post('/api/v1/accounts/query', json={}).json()["accounts"]]

    def tearDown(self):
        if self.reader.release:
            self.reader.release.set()
        self.client.__exit__(None, None, None)
        self.assertTrue(self.reader.closed)
        self.tmp.cleanup()

    def post(self, path, account=None, **body):
        return self.client.post("/api/v1/"+path, json=dict(account_id=account or self.ids[0], **body))

    def refresh(self, account=None):
        task = self.post("conversation-refreshes", account).json()
        for _ in range(200):
            task = self.post("conversation-refreshes/query", account, refresh_id=task["refresh_id"]).json()
            if task["status"] in ("completed", "failed"):
                return task
            time.sleep(.005)
        self.fail("refresh did not finish")

    def rules(self, **overrides):
        return dict(Exclusions().model_dump(), **overrides)

    def test_defaults_individual_filters_and_account_isolation(self):
        self.refresh()
        result = self.post("conversations/query").json()
        self.assertEqual((result["total"], result["unfiltered_total"]), (20, 160))
        self.assertEqual(result["items"][0]["category"], "contact")
        for key in Exclusions.model_fields:
            self.assertEqual(self.post("conversation-settings/update", exclusions=self.rules(**{key: False})).status_code, 200)
            result = self.post("conversations/query").json()
            self.assertEqual(result["total"], 40)
            self.assertEqual({r["category"] for r in result["items"]}, {"contact", key})
        other = self.post("conversation-settings/query", self.ids[1]).json()
        self.assertTrue(all(other["exclusions"].values()))
        self.assertFalse(self.path.exists())  # Preferences now belong to the database.

    def test_paging_is_immutable_and_cannot_cross_accounts_or_settings(self):
        self.post("conversation-settings/update", exclusions={k: False for k in Exclusions.model_fields})
        self.refresh()
        page = self.post("conversations/query").json()
        first_id = page["snapshot_id"]
        cursor = page["next_cursor"]
        self.assertEqual(self.post("conversations/query", self.ids[1], cursor=cursor).status_code, 409)
        self.reader.items = rows(2)
        self.refresh()
        ids = [r["conversation_id"] for r in page["items"]]
        while page["next_cursor"]:
            page = self.post("conversations/query", cursor=page["next_cursor"]).json()
            self.assertEqual(page["snapshot_id"], first_id)
            ids.extend(r["conversation_id"] for r in page["items"])
        self.assertEqual(len(set(ids)), 160)
        self.post("conversation-settings/update", exclusions=self.rules())
        self.assertEqual(self.post("conversations/query", cursor=cursor).json()["code"], "cursor_expired")

    def test_settings_and_ids_survive_restart(self):
        self.post("conversation-settings/update", exclusions=self.rules(wechat_pay=False))
        self.client.__exit__(None, None, None)
        with TestClient(create_app(accounts, conversation_reader=Reader(), settings_path=self.path),
                        base_url="http://127.0.0.1:8765") as client:
            task = client.post("/api/v1/account-scans", json={}).json()
            while client.post('/api/v1/account-scans/query', json={'scan_id': task['scan_id']}).json()["status"] == "running":
                time.sleep(.005)
            new_id = client.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"]
            self.assertEqual(new_id, self.ids[0])
            self.assertFalse(client.post("/api/v1/conversation-settings/query", json={"account_id": new_id}).json()["exclusions"]["wechat_pay"])

    def test_concurrent_requests_merge_and_accounts_read_serially(self):
        self.reader.release = threading.Event()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.post("conversation-refreshes").json(), range(5)))
        self.assertEqual(len({r["refresh_id"] for r in results}), 1)
        second = self.post("conversation-refreshes", self.ids[1]).json()
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.post("conversation-refreshes/query", self.ids[1], refresh_id=results[0]["refresh_id"]).status_code, 404)
        self.reader.release.set()
        for _ in range(200):
            task = self.post("conversation-refreshes/query", self.ids[1], refresh_id=second["refresh_id"]).json()
            if task["status"] == "completed":
                break
            time.sleep(.005)
        self.assertEqual(self.reader.calls, 2)
        self.assertEqual(self.reader.peak, 1)

    def test_failure_and_identity_loss_preserve_archive(self):
        self.refresh()
        self.reader.failure = "database_page_hmac_failed"
        self.assertEqual(self.refresh()["status"], "failed")
        self.assertTrue(self.post("conversations/query").json()["stale"])
        self.reader.validation_failure = "account_changed_during_read"
        self.assertEqual(self.post("conversations/query").status_code, 200)
        self.reader.failure = "process_exited"
        self.assertEqual(self.refresh()["status"], "failed")
        self.assertEqual(self.post("conversations/query").json()["source_state"], "offline")

    def test_json_validation_no_scan_and_damaged_settings_not_overwritten(self):
        self.assertEqual(self.post("conversation-settings/query").status_code, 200)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.reader.calls, 0)
        for body in ({"account_id": self.ids[0], "page_size": 0},
                     {"account_id": self.ids[0], "page_size": "50"},
                     {"account_id": self.ids[0], "secret": "private-data"}):
            response = self.client.post("/api/v1/conversations/query", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("private-data", response.text)
        self.assertEqual(self.post("conversation-settings/update", exclusions={}).status_code, 422)
        self.assertEqual(self.post("conversation-settings/update", exclusions=self.rules(wechat_pay="false")).status_code, 422)
        self.path.write_text("broken")
        self.assertEqual(self.post("conversation-settings/update", exclusions=self.rules()).status_code, 200)
        self.assertEqual(self.path.read_text(), "broken")
        incomplete = json.dumps({"schema_version": 1, "accounts": {"a"*64: {}}})
        self.path.write_text(incomplete)
        self.assertEqual(self.post("conversation-settings/query").status_code, 200)
        self.assertEqual(self.path.read_text(), incomplete)
        response = self.client.post("/api/v1/conversations/query", json={"account_id": self.ids[0]}, headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "host_not_allowed")

    def test_empty_partial_expiry_and_all_filtered_are_distinct(self):
        self.reader.items = []
        self.refresh()
        result = self.post("conversations/query").json()
        self.assertEqual((result["total"], result["unfiltered_total"]), (0, 0))
        self.reader.items = rows(2)[1:]
        self.refresh()
        result = self.post("conversations/query").json()
        self.assertEqual((result["total"], result["unfiltered_total"]), (0, 1))
        self.reader.validation_failure = "process_exited"
        self.assertEqual(self.post("conversations/query").status_code, 200)


if __name__ == "__main__":
    unittest.main()
