from contextlib import closing
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from uuid import uuid4

from backend.app.ai_input_recovery import recover
from backend.app.storage import Store
from backend.app.conversations import ConversationError
from test_roles import RoleTestCase
import test_group_scheduling as groups


class InputRecoveryTests(RoleTestCase):
    configured = groups.GroupSchedulingTests.configured
    ingest = groups.GroupSchedulingTests.ingest
    delta = groups.GroupSchedulingTests.delta
    wait_run = groups.GroupSchedulingTests.wait_run
    group = groups.GroupSchedulingTests.group
    settle = groups.GroupSchedulingTests.settle
    start = groups.GroupSchedulingTests.start

    def failed_archive(self):
        self.group(1)
        self.delta(1, mention_status='known', mentioned_wxids=['wxid_synthetic_0'])
        self.delta(2)
        self.delta(3, type='voice', content={'transcript': None})
        self.delta(4, type='file', content={'text': 'unsupported'})
        with patch('backend.app.ai_execution.build_request', side_effect=ConversationError('ai_input_too_large', 'synthetic local limit', 422)):
            failed = self.start()
        self.assertEqual(failed['status'], 'failed')
        self.copy = Path(self.temp.name) / 'recovery.sqlite3'
        def copy():
            with closing(sqlite3.connect(self.copy)) as db:
                self.store.db.backup(db)
        self.store.call(copy)
        self.target = (self.ids[0], self.cids[0], failed['run_id'])
        return failed

    def dump_copy(self):
        with closing(sqlite3.connect(self.copy)) as db:
            return '\n'.join(db.iterdump())

    def test_preview_lock_and_state_guards_do_not_mutate(self):
        self.failed_archive()
        before = self.dump_copy()
        preview = recover(self.copy, *self.target)
        self.assertEqual(preview['status'], 'preview')
        self.assertEqual(preview['pending_count'], 2)
        self.assertEqual(preview['waiting_count'], 2)
        self.assertEqual(self.dump_copy(), before)
        self.assertFalse((self.copy.parent / 'maintenance-backups').exists())
        with self.assertRaisesRegex(ValueError, '正常停止'):
            recover(self.path, *self.target, apply=True)
        with self.assertRaisesRegex(ValueError, '状态已变化'):
            recover(self.copy, *self.target[:2], 'wrong-run', apply=True)
        with closing(sqlite3.connect(self.copy)) as db, db:
            db.execute("UPDATE ai_runs SET status='queued',payload=json_set(payload,'$.status','queued') WHERE run_id=?", (self.target[2],))
        changed = self.dump_copy()
        with self.assertRaises(ValueError):
            recover(self.copy, *self.target, apply=True)
        self.assertEqual(self.dump_copy(), changed)

    def test_recovery_is_atomic_and_backup_preserves_original(self):
        self.failed_archive()
        before = self.dump_copy()
        with patch.object(Store, '_delivery_enable', side_effect=ValueError('synthetic transaction failure')):
            with self.assertRaisesRegex(ValueError, 'transaction failure'):
                recover(self.copy, *self.target, apply=True)
        self.assertEqual(self.dump_copy(), before)
        result = recover(self.copy, *self.target, apply=True)
        with closing(sqlite3.connect(result['backup'])) as db:
            self.assertEqual('\n'.join(db.iterdump()), before)
        after = self.dump_copy()
        self.assertEqual(recover(self.copy, *self.target, apply=True)['status'], 'already_applied')
        self.assertEqual(self.dump_copy(), after)

    def test_old_backlog_only_memorizes_and_new_mentions_still_reply(self):
        failed = self.failed_archive()
        aid, cid, _ = self.target
        with closing(sqlite3.connect(self.copy)) as db:
            prefs_before = db.execute('SELECT * FROM conversation_preferences ORDER BY account_id,conversation_id').fetchall()
            cards_before = db.execute('SELECT * FROM role_cards ORDER BY card_code').fetchall()
            other_before = db.execute('SELECT * FROM ai_sessions WHERE account_id<>?', (aid,)).fetchall()
        receipt = recover(self.copy, *self.target, apply=True)
        with closing(sqlite3.connect(self.copy)) as db:
            self.assertEqual(db.execute('SELECT * FROM conversation_preferences ORDER BY account_id,conversation_id').fetchall(), prefs_before)
            self.assertEqual(db.execute('SELECT * FROM role_cards ORDER BY card_code').fetchall(), cards_before)
            self.assertEqual(db.execute('SELECT * FROM ai_sessions WHERE account_id<>?', (aid,)).fetchall(), other_before)
            self.assertFalse(db.execute('SELECT 1 FROM reply_triggers WHERE account_id=? AND conversation_id=? AND consumed_run_id IS NULL', (aid, cid)).fetchone())
            self.assertEqual(db.execute('SELECT paused FROM ai_sessions WHERE account_id=? AND conversation_id=?', (aid, cid)).fetchone()[0], 0)
        with closing(Store(self.copy)) as store:
            run = store.start_ai_run(aid, cid, uuid4().hex, 'auto')
            snapshot = store.claim_ai_run(aid, cid, run['run_id'])
            envelope = snapshot['envelope']
            self.assertEqual(envelope['processing_type'], 'memory_backfill')
            self.assertFalse(envelope['reply_allowed'])
            self.assertFalse(envelope['role_card_edit_allowed'])
            self.assertEqual(envelope['reply_target_ids'], [])
            self.assertEqual(len(envelope['messages']), receipt['pending_count'])
            old_store = self.store
            self.store = store
            try:
                self.delta(5, sent_at='2020-01-01T00:00:00Z', mention_status='known', mentioned_wxids=['wxid_synthetic_0'])
                self.delta(6, mention_status='known', mentioned_wxids=['wxid_synthetic_0'])
            finally:
                self.store = old_store
            store.finish_ai_run(aid, cid, run['run_id'], result=dict(memory='旧积压已整理', reply='不能发送的历史回复'))
            self.assertEqual(store.ai_run(aid, cid, run['run_id'])['delivery']['status'], 'not_required')
            self.assertEqual(store.ai_run(aid, cid, run['run_id'])['reply'], '')
            next_run = store.start_ai_run(aid, cid, uuid4().hex, 'auto')
            fresh = store.claim_ai_run(aid, cid, next_run['run_id'])['envelope']
            self.assertEqual(fresh['processing_type'], 'realtime_reply')
            self.assertTrue(fresh['reply_required'])
            self.assertEqual(len(fresh['reply_target_ids']), 1)
            old_ids = {m['message_id'] for m in envelope['messages']}
            self.assertFalse(old_ids & set(fresh['reply_target_ids']))
            store.finish_ai_run(aid, cid, next_run['run_id'], result=dict(memory='新消息正常处理', reply='新消息的回复'))
            self.assertEqual(store.ai_run(aid, cid, next_run['run_id'])['delivery']['status'], 'queued')
            self.assertEqual(store.ai_run(aid, cid, failed['run_id'])['status'], 'failed')
            self.assertEqual(store.ai_runs(aid, cid)['waiting_count'], 2)
