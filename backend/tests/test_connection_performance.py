import json
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

from backend.app.storage import Store
from backend.app.performance import PerformanceLog
import test_archive as archive
from test_message_api import message
from auth_support import TestClient, create_app
from test_conversations import accounts, Reader


class MetadataPerformanceTests(unittest.TestCase):
    setUp = archive.ArchiveTests.setUp
    tearDown = archive.ArchiveTests.tearDown

    def test_metadata_and_capacity_plans_are_covering_on_reopen(self):
        queries = [
            ("SELECT digest,mime_type FROM media_cache INDEXED BY media_cache_metadata WHERE cache_key=? AND account_id=?", ('image:synthetic', self.aid)),
            ("SELECT COALESCE(SUM(size),0) FROM media_cache INDEXED BY media_cache_capacity WHERE kind='image'", ()),
            ("SELECT cache_key,size FROM media_cache INDEXED BY media_cache_capacity WHERE kind='image' ORDER BY used_at", ()),
        ]
        for reopen in (False, True):
            if reopen:
                self.store.close()
                self.store = Store(self.path)
            def check():
                self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 12)
                for sql, args in queries:
                    plan = [r[3] for r in self.store.db.execute('EXPLAIN QUERY PLAN '+sql, args)]
                    self.assertTrue(any('COVERING INDEX' in line for line in plan), plan)
                self.assertEqual(self.store._account(self.aid)['account_id'], self.aid)
            self.store.call(check)

    def test_batched_counts_match_individual_states_and_do_not_query_per_message(self):
        items = [message(i) for i in range(1, 51)]
        kinds = ('text', 'image', 'sticker', 'voice', 'file')
        for i, item in enumerate(items):
            item['type'] = kinds[i % len(kinds)]
            item['content'] = dict(text='合成正文', transcript='转写' if i % 2 else None)
        self.store.save_history(self.aid, self.cid, dict(items=items, has_more=False, issues=[], status='complete'), {})
        def check():
            db = self.store.db
            rows = db.execute('SELECT * FROM messages WHERE conversation_id=?', (self.cid,)).fetchall()
            with db:
                for i, row in enumerate(rows):
                    if i % 2:
                        db.execute("INSERT INTO media_cache VALUES(?,?,'image','image/png',?,8,'synthetic-digest',NULL,0)",
                                   ('image:'+row['message_id'], self.aid, b'synthetic'))
            for group in (False, True):
                with db:
                    db.execute('UPDATE conversations SET source_id=? WHERE conversation_id=?',
                               ('synthetic@chatroom' if group else 'synthetic', self.cid))
                for vision in (False, True):
                    with patch.object(self.store, '_ai_vision', return_value=vision):
                        with db:
                            for i, row in enumerate(rows):
                                state = self.store._ai_item(row, vision)
                                db.execute('INSERT OR REPLACE INTO ai_message_state VALUES(?,?,?,?,?,?,?)',
                                           (row['message_id'], state[1] if i % 3 else 'obsolete', state[2],
                                            ('memorized', 'processing', 'failed')[i % 3], 'synthetic-run', 'attempted', 'memorized'))
                        expected = dict(pending_count=0, waiting_count=0)
                        for row in rows:
                            status = self.store._ai_item(row, vision)[3]['status']
                            expected['pending_count' if status in ('pending', 'failed', 'processing') else 'waiting_count'] += int(status != 'memorized')
                        statements = []
                        db.set_trace_callback(statements.append)
                        try: actual = self.store._ai_counts(self.aid, self.cid)
                        finally: db.set_trace_callback(None)
                        self.assertEqual(actual, expected)
                        self.assertEqual(len(statements), 2, statements)
                        if group:
                            self.assertFalse(any('media_cache' in sql for sql in statements))
                        else:
                            self.assertTrue(any('INDEXED BY media_cache_metadata' in sql for sql in statements))
        self.store.call(check)

    def test_group_placeholder_does_not_read_media_but_explicit_media_still_loads(self):
        item = {**message(), 'type': 'image', 'content': dict(description='合成图片')}
        self.store.save_history(self.aid, self.cid, dict(items=[item], has_more=False, issues=[], status='complete'), {})
        def check():
            with self.store.db:
                self.store.db.execute("UPDATE conversations SET source_id='synthetic@chatroom' WHERE conversation_id=?", (self.cid,))
            row = self.store.db.execute('SELECT * FROM messages WHERE conversation_id=?', (self.cid,)).fetchone()
            with self.store.db:
                self.store.db.execute("INSERT INTO media_cache VALUES(?,?,'image','image/png',?,9,'digest',NULL,0)",
                                      ('image:'+row['message_id'], self.aid, b'synthetic'))
            statements = []
            self.store.db.set_trace_callback(statements.append)
            try: value = self.store._ai_item(row, False)
            finally: self.store.db.set_trace_callback(None)
            self.assertFalse(any('media_cache' in sql for sql in statements))
            loaded = self.store._ai_item(row, True, include_media=True)
            self.assertEqual(value[1:3], loaded[1:3])
            self.assertEqual(loaded[0]['content']['_image_bytes'], b'synthetic')
        self.store.call(check)


class SlowOperationTests(unittest.TestCase):
    def test_slow_http_and_database_records_exclude_arguments_and_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(probe=accounts, conversation_reader=Reader(), monitor=False,
                             database_path=Path(directory)/'archive.sqlite3')
            with TestClient(app, base_url='http://127.0.0.1:8765') as client:
                store = app.state.store
                def slow_query(private_value):
                    time.sleep(1.02)
                    return []
                with patch.object(store, 'accounts', side_effect=lambda: store.call(slow_query, 'synthetic-private-argument')):
                    response = client.post('/api/v1/accounts/query?secret=synthetic-query', json={},
                                           headers={'X-Private-Test': 'synthetic-header'})
                self.assertEqual(response.status_code, 200)
                records = [json.loads(line) for line in store.performance.path.read_text().splitlines()]
                self.assertTrue(any(r['kind']=='database' and r['execution_ms']>1000 for r in records))
                self.assertTrue(any(r['kind']=='http' and r['operation']=='/api/v1/accounts/query' for r in records))
                text = json.dumps(records)
                for private in ('synthetic-private-argument', 'synthetic-query', 'synthetic-header'):
                    self.assertNotIn(private, text)

    def test_threshold_rotation_and_only_structured_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            log = PerformanceLog(Path(directory)/'archive.sqlite3')
            log.record('database', 'Store.accounts', execution_ms=1000)
            self.assertFalse(log.path.exists())
            log.record('database', 'Store.accounts', queue_ms=1001, execution_ms=2)
            first = json.loads(log.path.read_text())
            self.assertEqual(set(first), {'at', 'kind', 'operation', 'queue_ms', 'execution_ms'})
            log.handler.maxBytes = 200
            for _ in range(10): log.record('http', '/api/v1/accounts/query', execution_ms=1100, status=200)
            log.close()
            self.assertLessEqual(len(list(log.path.parent.glob('performance.jsonl*'))), 3)
            self.assertEqual(json.loads(log.path.read_text())['status'], 200)

    def test_recording_failure_and_disabled_memory_store_are_harmless(self):
        log = PerformanceLog(':memory:')
        log.record('database', 'Store.accounts', execution_ms=2000)
        self.assertIsNone(log.handler)
        with tempfile.TemporaryDirectory() as directory:
            log = PerformanceLog(Path(directory)/'archive.sqlite3')
            with patch('backend.app.performance.RotatingFileHandler', side_effect=OSError('unavailable')):
                log.record('database', 'Store.accounts', execution_ms=2000)
