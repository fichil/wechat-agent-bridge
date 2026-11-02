"""The compatibility release must never transmit a future-format run."""
from contextlib import closing
import json
import sqlite3
from unittest.mock import patch
from test_roles import RoleTestCase
from test_delivery import DeliveryTests
from backend.app.storage import Store


class MediaRollbackTests(RoleTestCase):
    configured = DeliveryTests.configured
    delta = DeliveryTests.delta
    start = DeliveryTests.start
    wait_run = DeliveryTests.wait_run

    def test_rollback_preserves_v11_and_holds_entire_future_run(self):
        self.configured()
        self.delta()
        run = self.wait_run(self.start())
        rid = run['run_id']
        def future():
            with self.store.db:
                value = json.loads(self.store.db.execute('SELECT payload FROM ai_runs WHERE run_id=?', (rid,)).fetchone()[0])
                value['reply_format_version'] = 2
                self.store.db.execute('UPDATE ai_runs SET payload=? WHERE run_id=?', (json.dumps(value), rid))
                self.store.db.execute('PRAGMA user_version=11')
        self.store.call(future)
        with patch('backend.app.delivery_storage.REPLY_FORMAT_VERSION', 1):
            self.assertEqual(self.store.delivery_candidates(), [])
            self.assertIsNone(self.store.claim_delivery(rid, 0))
        before = self.store.ai_run(self.ids[0], self.cids[0], rid)['delivery']
        self.store.close()
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.call(lambda: reopened.db.execute('PRAGMA user_version').fetchone()[0]), 11)
            self.assertEqual(reopened.ai_run(self.ids[0], self.cids[0], rid)['delivery'], before)
        finally:
            reopened.close()
