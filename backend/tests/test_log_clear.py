import threading
import time
from contextlib import closing
from unittest.mock import patch

from backend.app.log_clear import clear_batches
from backend.app.storage import Store
from test_roles import RoleTestCase


class LogClearTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        def clear():
            with self.store.db:
                self.store.db.execute("DELETE FROM diagnostic_logs")
        self.store.call(clear)

    def seed(self, count=401, account=0):
        aid = self.ids[account] if account is not None else None
        def write():
            with self.store.db:
                return [self.store._log_put(dict(account_id=aid, category="source", operation="synthetic"), {"index": i}) for i in range(count)]
        return self.store.call(write)

    def start_clear(self, scope="account", account=0):
        response = self.post("logs/clear-tasks", account_id=self.ids[account], scope=scope)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def task(self, task=None, scope="account", account=0):
        response = self.post("logs/clear-tasks/query", account_id=self.ids[account], scope=scope,
            **({"task_id": task["task_id"]} if task else {}))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["task"]

    def finished(self, task, **kwargs):
        for _ in range(300):
            result = self.task(task, **kwargs)
            if result["status"] in ("completed", "failed"):
                return result
            time.sleep(.01)
        self.fail("cleanup did not finish")

    def test_batches_bound_rows_and_detail_bytes(self):
        self.assertEqual([len(b) for b in clear_batches([(str(i), "now", 1) for i in range(401)])], [200, 200, 1])
        large = [(str(i), "now", 3*1024*1024) for i in range(3)]
        self.assertEqual([len(b) for b in clear_batches(large)], [1, 1, 1])
        self.assertEqual([len(b) for b in clear_batches([("large", "now", 8*1024*1024)])], [1])

    def test_background_progress_duplicate_and_business_queries_between_batches(self):
        self.seed()
        entered, release = threading.Event(), threading.Event()
        original = self.store.log_clear_batch
        def pause(task, targets):
            result = original(task, targets)
            if result["processed_count"] == 200:
                entered.set()
                self.assertTrue(release.wait(10))
            return result
        with patch.object(self.store, "log_clear_batch", side_effect=pause):
            try:
                task = self.start_clear()
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.start_clear()["task_id"], task["task_id"])
                self.assertEqual(self.task()["processed_count"], 200)
                self.assertEqual(self.task()["deleted_count"], 200)
                self.assertEqual(self.post("accounts/query").status_code, 200)
                self.assertEqual(self.post("conversation-preferences/query", account_id=self.ids[0], conversation_id=self.cids[0]).status_code, 200)
            finally:
                release.set()
            result = self.finished(task)
        self.assertEqual(result["deleted_count"], 401)
        self.assertEqual(result["status"], "completed")

    def test_snapshot_preserves_updates_new_logs_active_records_and_other_scopes(self):
        identities = self.seed(3)
        other = self.seed(1, account=1)[0]
        global_id = self.seed(1, account=None)[0]
        active = self.store.log_write(dict(account_id=self.ids[0], category="source", status="running"), {})
        task, targets = self.store.begin_log_clear(self.ids[0], "account")
        self.store.log_write(dict(log_id=identities[0], message="updated"), {})
        self.store.log_write(dict(log_id=identities[1], status="running"))
        self.store.log_write(dict(log_id=active, status="completed"))
        new_id = self.seed(1)[0]
        result = self.store.log_clear_batch(task, targets)
        self.assertEqual(result["processed_count"], 3)
        self.assertEqual(result["deleted_count"], 1)
        remaining = self.store.call(lambda: {r[0] for r in self.store.db.execute("SELECT log_id FROM diagnostic_logs")})
        self.assertTrue({identities[0], identities[1], active, other, global_id, new_id} <= remaining)
        self.assertNotIn(identities[2], remaining)
        self.assertEqual(self.store.call(lambda: self.store.db.execute("SELECT count(*) FROM diagnostic_details WHERE log_id=?", (identities[2],)).fetchone()[0]), 0)
        self.assertEqual(len(self.store.accounts()), 2)

    def test_failed_batch_rolls_back_deletion_and_progress_together(self):
        self.seed()
        save = self.store._save_log_clear
        def fail(task):
            if task["processed_count"] > 200:
                raise RuntimeError("synthetic storage failure")
            return save(task)
        with patch.object(self.store, "_save_log_clear", side_effect=fail):
            result = self.finished(self.start_clear())
        self.assertEqual((result["status"], result["deleted_count"], result["processed_count"]), ("failed", 200, 200))
        self.assertEqual(self.store.call(lambda: self.store.db.execute("SELECT count(*) FROM diagnostic_logs WHERE account_id=?", (self.ids[0],)).fetchone()[0]), 201)
        self.assertEqual(self.finished(self.start_clear())["deleted_count"], 201)

    def test_storage_recovery_reconciles_an_unsaved_failure_without_replaying(self):
        self.seed(2)
        service = self.app.state.log_clear
        with patch.object(self.store, "log_clear_batch", side_effect=RuntimeError("synthetic disk failure")), patch.object(self.store, "fail_log_clear", side_effect=RuntimeError("synthetic disk failure")):
            task = self.start_clear()
            with self.assertRaises(RuntimeError):
                service.pending[self.ids[0]][1].result(timeout=5)
        result = self.task(task)
        self.assertEqual((result["status"], result["deleted_count"]), ("failed", 0))
        self.assertEqual(self.finished(self.start_clear())["deleted_count"], 2)

    def test_restart_marks_partial_task_interrupted_without_replaying(self):
        self.seed()
        task, targets = self.store.begin_log_clear(self.ids[0], "account")
        self.store.log_clear_batch(task, targets[:200])
        self.store.close()
        with closing(Store(self.path)) as reopened:
            result = reopened.log_clear_task(self.ids[0], "account", task["task_id"])
            self.assertEqual(result["error"], "log_clear_interrupted")
            self.assertEqual(result["deleted_count"], 200)
            self.assertEqual(reopened.call(lambda: reopened.db.execute("SELECT count(*) FROM diagnostic_logs WHERE account_id=?", (self.ids[0],)).fetchone()[0]), 201)

    def test_global_deduplicates_across_accounts_and_task_queries_are_scoped(self):
        self.seed(2, account=None)
        entered, release = threading.Event(), threading.Event()
        original = self.store.log_clear_batch
        def pause(task, targets):
            entered.set()
            self.assertTrue(release.wait(10))
            return original(task, targets)
        with patch.object(self.store, "log_clear_batch", side_effect=pause):
            try:
                task = self.start_clear("global")
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.start_clear("global", account=1)["task_id"], task["task_id"])
                self.assertEqual(self.post("logs/clear-tasks/query", account_id=self.ids[1], scope="account", task_id=task["task_id"]).status_code, 404)
            finally:
                release.set()
            self.assertEqual(self.finished(task, scope="global")["deleted_count"], 2)
        account_task = self.start_clear()
        self.assertEqual(self.post("logs/clear-tasks/query", account_id=self.ids[1], scope="account", task_id=account_task["task_id"]).status_code, 404)

    def test_empty_scope_and_legacy_endpoint(self):
        self.assertIsNone(self.task())
        self.assertEqual(self.finished(self.start_clear())["deleted_count"], 0)
        self.seed(2)
        response = self.post("logs/clear", account_id=self.ids[0], scope="account")
        self.assertEqual(response.json(), {"deleted_count": 2})
        self.assertEqual(self.post("logs/clear-tasks/query", account_id=self.ids[0], scope="account", task_id="unknown").status_code, 404)

    def test_graceful_stop_interrupts_between_batches_and_rejects_new_tasks(self):
        self.seed()
        entered, release = threading.Event(), threading.Event()
        original = self.store.log_clear_batch
        service = self.app.state.log_clear
        def pause(task, targets):
            result = original(task, targets)
            entered.set()
            self.assertTrue(release.wait(10))
            return result
        with patch.object(self.store, "log_clear_batch", side_effect=pause):
            try:
                task = self.start_clear()
                self.assertTrue(entered.wait(5))
                service.stopped.set()
            finally:
                release.set()
            service.close()
        result = self.task(task)
        self.assertEqual((result["error"], result["deleted_count"]), ("log_clear_interrupted", 200))
        self.assertEqual(self.post("logs/clear-tasks", account_id=self.ids[0], scope="account").status_code, 503)
