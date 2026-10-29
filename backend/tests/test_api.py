import copy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.agents import UnavailableAgent
from wechat_account_probe.models import Instance, aggregate, failure_result, instance_result

FILETIME = 134000000000000001


def sample():
    instances = [Instance(100, FILETIME, 1, "4.1.15.56"), Instance(200, FILETIME + 1, 2, "4.1.15.56")]
    result = aggregate([instance_result(i, wxid="wxid_synthetic", nickname="合成账号", status="complete",
                                       avatar_status="missing", avatar_reason="avatar_not_found") for i in instances])
    result["client_instances"] = [vars(i) for i in instances]
    result["skipped_instances"] = [instance_result(Instance(300, FILETIME + 2, 3, "4.1.15.56"), status="not_logged_in")]
    return result


def wait_task(client, task):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.post('/api/v1/account-scans/query', json={'scan_id': task['scan_id']}).json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Synthetic probe timed out")


class ApiTests(unittest.TestCase):
    def client(self, probe):
        return TestClient(create_app(probe), base_url="http://127.0.0.1:8765")

    def scan(self, client):
        response = client.post("/api/v1/account-scans", json={})
        self.assertEqual(response.status_code, 202)
        return wait_task(client, response.json())

    def test_health_openapi_and_reads_never_scan(self):
        probe = Mock(return_value=sample())
        with self.client(probe) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            self.assertEqual(client.post('/api/v1/accounts/query', json={}).status_code, 200)
            self.assertEqual(client.post('/api/v1/account-scans/query', json={'scan_id': 'unknown'}).status_code, 404)
            schema = client.get("/openapi.json").json()
            self.assertEqual(schema["components"]["schemas"]["Instance"]["properties"]["created"]["type"], "string")
        probe.assert_not_called()

    def test_exported_openapi_matches_current_routes_and_models(self):
        saved = json.loads((Path(__file__).resolve().parents[1] / "openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, create_app().openapi())

    def test_contract_merge_precision_missing_avatar_and_opaque_ids(self):
        source = sample()
        original = copy.deepcopy(source)
        with self.client(lambda: source) as client:
            task = self.scan(client)
            self.assertEqual(task["status"], "completed")
            response = client.post('/api/v1/accounts/query', json={})
            self.assertEqual(response.headers["cache-control"], "no-store")
            result = response.json()
            self.assertEqual(len(result["accounts"]), 1)
            account = result["accounts"][0]
            self.assertEqual(len(account["instances"]), 2)
            self.assertEqual(account["instances"][0]["created"], str(FILETIME))
            self.assertEqual(result["client_instances"][1]["created"], str(FILETIME + 1))
            self.assertEqual(result["skipped_instances"][0]["instances"][0]["created"], str(FILETIME + 2))
            self.assertIsNone(account["avatar"])
            self.assertIsNone(account["wechat_id"])
            self.assertNotIn("wxid", account["account_id"])
            self.assertEqual(client.post('/api/v1/accounts/get', json={'account_id': account['account_id']}).json(), account)
            self.scan(client)
            self.assertEqual(client.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"], account["account_id"])
        self.assertEqual(source, original)

    def test_concurrent_posts_share_running_task_and_health_remains_responsive(self):
        release = threading.Event()
        probe = Mock(side_effect=lambda: (release.wait(5), sample())[1])
        with self.client(probe) as client:
            try:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(pool.map(lambda _: client.post("/api/v1/account-scans", json={}).json(), range(6)))
                self.assertEqual(len({item["scan_id"] for item in results}), 1)
                self.assertEqual(client.get("/api/v1/health").status_code, 200)
                self.assertEqual(probe.call_count, 1)
            finally:
                release.set()
            wait_task(client, results[0])

    def test_partial_is_completed_and_unknown_identity_is_not_selectable(self):
        result = aggregate([instance_result(Instance(1, FILETIME, 1, "unknown"))],
                           [dict(reason="process_metadata_access_denied", pid=1)])
        with self.client(lambda: result) as client:
            task = self.scan(client)
            self.assertEqual((task["status"], task["exit_code"]), ("completed", 2))
            snapshot = client.post('/api/v1/accounts/query', json={}).json()
            self.assertFalse(snapshot["summary"]["coverage_complete"])
            self.assertIsNone(snapshot["accounts"][0]["account_id"])

    def test_partial_confirmed_identity_is_selectable(self):
        result = aggregate([instance_result(Instance(1, FILETIME, 1, "4.1.15.56"),
                                           wxid="wxid_synthetic", status="partial")])
        with self.client(lambda: result) as client:
            self.scan(client)
            self.assertTrue(client.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"])

    def test_failure_and_empty_success_keep_archived_accounts(self):
        probe = Mock(side_effect=[sample(), failure_result("probe_dependency_missing"), aggregate([])])
        with self.client(probe) as client:
            self.scan(client)
            old = client.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"]
            failed = self.scan(client)
            self.assertEqual((failed["status"], failed["exit_code"]), ("failed", 1))
            self.assertEqual(client.post('/api/v1/accounts/get', json={'account_id': old}).json()['source_state'], 'unknown')
            empty = self.scan(client)
            self.assertEqual((empty["status"], empty["exit_code"]), ("completed", 0))
            self.assertTrue(empty["summary"]["coverage_complete"])

    def test_exception_and_validation_details_do_not_escape(self):
        with self.client(Mock(side_effect=RuntimeError("private-account-data"))) as client:
            failed = self.scan(client)
            self.assertEqual(failed["error"], "probe_failed")
            self.assertNotIn("private-account-data", str(failed))
            response = client.post("/api/v1/account-scans", json={"secret": "private-account-data"})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("private-account-data", response.text)

    def test_local_boundary_rejects_foreign_host_origin_and_simple_post(self):
        probe = Mock(return_value=sample())
        with self.client(probe) as client:
            self.assertEqual(client.get("/api/v1/health", headers={"Host": "attacker.example"}).status_code, 400)
            self.assertEqual(client.post('/api/v1/accounts/query', json={}, headers={'Origin': 'https://attacker.example'}).status_code, 403)
            self.assertEqual(client.post("/api/v1/account-scans", content="{}").status_code, 415)
            self.assertEqual(client.post("/api/v1/account-scans", json={}, headers={"Origin": "null"}).status_code, 403)
            self.assertEqual(client.get("/api/v1/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
            probe.assert_not_called()
            self.assertEqual(client.post("/api/v1/account-scans", json={}, headers={"Origin": "http://127.0.0.1:5173"}).status_code, 202)

    def test_separate_in_memory_databases_are_isolated(self):
        with self.client(sample) as first:
            old_task = self.scan(first)
            old_id = first.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"]
            self.scan(first)
            self.assertEqual(first.post('/api/v1/account-scans/query', json={'scan_id': old_task['scan_id']}).status_code, 200)
        with self.client(sample) as second:
            self.scan(second)
            self.assertNotEqual(second.post('/api/v1/accounts/query', json={}).json()["accounts"][0]["account_id"], old_id)
            self.assertEqual(second.post('/api/v1/accounts/get', json={'account_id': old_id}).status_code, 404)


class AgentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_is_unavailable_without_calling_any_provider(self):
        self.assertFalse(await UnavailableAgent().available())


if __name__ == "__main__":
    unittest.main()
