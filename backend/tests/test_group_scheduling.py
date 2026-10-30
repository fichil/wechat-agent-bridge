from contextlib import closing
import json
import sqlite3
from unittest.mock import patch
from uuid import uuid4

from backend.app.conversations import ConversationError
from backend.app.group_round_storage import GROUP_SCHEMA
from backend.app.storage import Store
from migration_helpers import remove_v8
from test_conversations import rows
import test_delivery as delivery
from test_roles import RoleTestCase


class GroupSchedulingTests(RoleTestCase):
    configured = delivery.DeliveryTests.configured
    ingest = delivery.DeliveryTests.ingest
    delta = delivery.DeliveryTests.delta
    wait_run = delivery.DeliveryTests.wait_run
    finish = delivery.DeliveryTests.finish

    def group(self, threshold=0):
        aid = self.ids[0]
        group = {**rows(1)[0], 'source_id': 'synthetic@chatroom', 'category': 'group'}
        self.store.save_conversations(aid, dict(items=[group], status='complete', issues=[]))
        self.cids[0] = self.store.call(lambda: self.store.db.execute('SELECT conversation_id FROM conversations WHERE account_id=? AND source_id=?', (aid, group['source_id'])).fetchone()[0])
        self.configured()
        self.assertEqual(self.update(self.preferences()['revision'], group_proactive_message_threshold=threshold).status_code, 200)

    def ready(self):
        return self.store.call(lambda: self.store._group_ready(self.ids[0], self.cids[0]))

    def settle(self):
        def write():
            with self.store.db:
                self.store.db.execute('UPDATE delivery_sessions SET last_trigger_at=0')
                self.store.db.execute('UPDATE group_message_eligibility SET observed_at=0')
        self.store.call(write)

    def start(self):
        self.settle()
        run = self.app.state.ai.start(self.ids[0], self.cids[0], uuid4().hex, 'auto')
        return self.wait_run(run) if run['status'] != 'empty' else run

    def envelope(self, index=-1):
        return json.loads(self.provider.requests[index]['input'][0]['content'][0]['text'])

    def test_disabled_no_automatic_calls_including_history_and_configuration(self):
        self.group()
        for index in range(1, 30): self.delta(index)
        self.ingest(100)
        self.store.save_verification(self.profile['profile_id'], 1, self.profile['verification'])
        before = len(self.provider.requests)
        self.settle()
        self.assertEqual(self.store.ai_candidates(), [])
        self.app.state.ai.tick()
        self.assertEqual(len(self.provider.requests), before)
        self.assertEqual(len(self.ready()), 29)
        self.assertEqual(self.start()['status'], 'empty')

    def test_twentieth_message_triggers_once_and_silence_consumes_count(self):
        self.group(20)
        for index in range(1, 20): self.delta(index)
        self.settle()
        self.assertEqual(self.store.ai_candidates(), [])
        self.delta(20)
        self.assertEqual(self.store.ai_candidates(), [])  # merge window
        self.settle()
        self.assertEqual(len(self.store.ai_candidates()), 1)
        with patch.object(self.provider, 'generate', return_value=dict(memory='已归纳', reply='')):
            run = self.start()
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['trigger_reason'], 'group_threshold')
        self.assertTrue(run['chose_silence'])
        self.assertEqual(run['delivery']['status'], 'not_required')
        self.assertEqual(self.ready(), [])
        self.assertEqual(self.store.ai_candidates(), [])

    def test_mention_bypasses_disabled_threshold_and_wins_simultaneous_threshold(self):
        self.group()
        self.delta(1)
        self.delta(2, mentioned_wxids=['wxid_synthetic_0'], mention_status='known')
        run = self.start()
        self.assertEqual(run['trigger_reason'], 'mention')
        self.assertEqual(run['message_count'], 2)
        self.assertEqual(run['delivery']['status'], 'queued')
        self.assertEqual(self.ready(), [])
        self.finish(run)
        self.update(self.preferences()['revision'], group_proactive_message_threshold=1)
        self.delta(3, mentioned_wxids=['wxid_synthetic_0'], mention_status='known')
        second = self.start()
        self.assertEqual(second['trigger_reason'], 'mention')
        self.assertNotEqual(run['round_id'], second['round_id'])

    def test_self_system_history_duplicate_and_correction_do_not_add_count(self):
        self.group(20)
        self.delta(1)
        self.delta(1)
        self.delta(1, content={'text': '修正'})
        self.delta(2, is_self=True)
        self.delta(3, type='system')
        self.delta(4, sent_at='2020-01-01T00:00:00Z')
        self.ingest(5)
        self.assertEqual(len(self.ready()), 1)
        self.delta(6, type='voice', content={'transcript': None})
        self.assertEqual(len(self.ready()), 1)
        self.delta(6, type='voice', content={'transcript': '就绪'})
        self.assertEqual(len(self.ready()), 2)

    def test_batches_keep_mention_and_later_arrivals_separate(self):
        self.group()
        self.delta(1, mentioned_wxids=['wxid_synthetic_0'], mention_status='known', content={'text': '请总结以上讨论'})
        for index in range(2, 56): self.delta(index)
        original = self.provider.generate
        def generate(*args):
            self.delta(56, content={'text': '下一轮消息'})
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=generate):
            first = self.start()
        self.assertEqual(first['message_count'], 50)
        self.assertFalse(first['round_final'])
        self.assertFalse(self.envelope()['reply_allowed'])
        self.assertEqual(first['delivery']['status'], 'not_required')
        second = self.start()
        self.assertEqual(second['round_id'], first['round_id'])
        self.assertEqual(second['message_count'], 5)
        self.assertEqual(second['round_completed_count'], 55)
        self.assertTrue(self.envelope()['reply_allowed'])
        self.assertEqual(self.envelope()['trigger_messages'][0]['content']['text'], '请总结以上讨论')
        self.assertEqual(second['delivery']['status'], 'queued')
        self.assertEqual(len(self.ready()), 1)
        self.finish(second)
        self.assertEqual(self.store.ai_candidates(), [])

    def test_threshold_round_multiple_batches_has_only_one_delivery(self):
        self.group(20)
        for index in range(1, 104): self.delta(index)
        first, second, third = self.start(), self.start(), self.start()
        self.assertEqual([r['message_count'] for r in (first, second, third)], [50, 50, 3])
        self.assertEqual(len({r['round_id'] for r in (first, second, third)}), 1)
        self.assertEqual([r['delivery']['status'] for r in (first, second, third)], ['not_required', 'not_required', 'queued'])
        self.assertEqual(self.ready(), [])

    def test_failure_retry_preserves_completed_batch(self):
        self.group(20)
        for index in range(1, 56): self.delta(index)
        first = self.start()
        memory = self.preferences()['memory']
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', '模拟失败', 503)):
            failed = self.start()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.preferences()['memory'], memory)
        self.assertEqual(len(self.ready()), 5)
        self.assertEqual(self.store.ai_candidates(), [])
        retry = self.app.state.ai.retry(self.ids[0], self.cids[0], failed['run_id'], uuid4().hex)
        final = self.wait_run(retry)
        self.assertEqual(final['message_count'], 5)
        self.assertEqual(final['round_id'], first['round_id'])
        self.assertEqual(final['round_completed_count'], 55)

    def test_disable_threshold_cancels_continuation_and_unsent_reply(self):
        self.group(20)
        for index in range(1, 56): self.delta(index)
        first = self.start()
        self.update(self.preferences()['revision'], group_proactive_message_threshold=0)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertIsNone(self.store.call(lambda: self.store._group_round(self.ids[0], self.cids[0])))
        self.assertEqual(len(self.ready()), 5)
        self.update(self.preferences()['revision'], group_proactive_message_threshold=1)
        final = self.start()
        self.assertNotEqual(first['round_id'], final['round_id'])
        self.assertEqual(final['delivery']['status'], 'queued')
        self.update(self.preferences()['revision'], group_proactive_message_threshold=0)
        self.assertEqual(self.store.ai_run(self.ids[0], self.cids[0], final['run_id'])['delivery']['status'], 'cancelled')

    def test_disable_while_remote_request_runs_prevents_reply_and_memory_commit(self):
        self.group(1)
        self.delta()
        original = self.provider.generate
        def generate(*args):
            self.update(self.preferences()['revision'], group_proactive_message_threshold=0)
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.start()
        self.assertEqual(run['status'], 'cancelled')
        self.assertEqual(run['delivery']['status'], 'not_required')
        self.assertEqual(self.preferences()['memory'], '')
        self.assertEqual(len(self.ready()), 1)

    def test_restart_preserves_round_snapshot_and_completed_progress(self):
        self.group(20)
        for index in range(1, 56): self.delta(index)
        first = self.start()
        self.store.close()
        with closing(Store(self.path)) as store:
            aid, cid = self.ids[0], self.cids[0]
            run = store.start_ai_run(aid, cid, uuid4().hex, 'auto')
            snapshot = store.claim_ai_run(aid, cid, run['run_id'])
            self.assertEqual(len(snapshot['envelope']['messages']), 5)
            self.assertEqual(snapshot['envelope']['round_id'], first['round_id'])
            store.finish_ai_run(aid, cid, run['run_id'], result=dict(memory='最终记忆', reply='完成'))
            self.assertEqual(store.ai_run(aid,cid,run['run_id'])['round_completed_count'], 55)

    def test_input_trimming_creates_memory_batches_without_early_reply(self):
        self.group(1)
        for index in range(1, 8): self.delta(index, content={'text': 'x'*10000})
        original = self.provider.generate
        def summarize(*args):
            result = original(*args)
            return {**result, 'memory': '压缩记忆'}
        with patch.object(self.provider, 'generate', side_effect=summarize):
            first = self.start()
            self.assertFalse(first['round_final'])
            self.assertLess(first['message_count'], 7)
            self.assertEqual(first['delivery']['status'], 'not_required')
            second = self.start()
        self.assertTrue(second['round_final'])
        self.assertEqual(second['round_completed_count'], 7)
        self.assertEqual(second['delivery']['status'], 'queued')

    def test_group_eligibility_and_scan_settings_survive_disable_without_cross_account_effects(self):
        self.group(1)
        self.delta(1)
        self.assertEqual(self.update(self.preferences()['revision'], ai_enabled=False, scan_interval_seconds=45).status_code, 200)
        self.assertEqual(self.store.sync_targets()[self.ids[0]]['synthetic@chatroom'], 45)
        self.delta(2)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.update(self.preferences()['revision'], ai_enabled=True).status_code, 200)
        self.assertEqual(self.ready(), [])  # old enable epoch cannot speak later
        self.assertEqual(self.preferences(1)['group_proactive_message_threshold'], 0)
        self.assertEqual(self.preferences(1)['scan_interval_seconds'], 2)
        for value in (-1, 1001, 1.5, True, None):
            self.assertEqual(self.update(self.preferences()['revision'], group_proactive_message_threshold=value).status_code, 422)

    def test_preferences_validation_independence_and_conflict(self):
        self.assertEqual(self.preferences()['scan_interval_seconds'], 2)
        self.assertEqual(self.update(scan_interval_seconds=60).json()['scan_interval_seconds'], 60)
        self.assertEqual(self.preferences(1)['scan_interval_seconds'], 2)
        self.assertEqual(self.update(scan_interval_seconds=20).status_code, 409)
        for value in (0, 3601, 1.5, True, None):
            self.assertEqual(self.update(1, scan_interval_seconds=value).status_code, 422)
        self.assertEqual(self.update(1, group_proactive_message_threshold=20).status_code, 422)
        self.assertEqual(self.store.sync_targets()[self.ids[0]][self.store.conversation(self.ids[0],self.cids[0])['source_id']], 60)

    def test_v8_migration_backup_rollback_defaults_and_no_historical_count(self):
        self.group()
        self.ingest()
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            remove_v8(db)
            db.execute('PRAGMA user_version=7')
            db.commit()
        with patch('backend.app.storage.GROUP_SCHEMA', (*GROUP_SCHEMA, 'INVALID SQL')):
            with self.assertRaises(ConversationError): Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)
            self.assertNotIn('scan_interval_seconds', {r[1] for r in db.execute('PRAGMA table_info(conversation_preferences)')})
        with closing(Store(self.path)) as store:
            prefs = store.preferences(self.ids[0], self.cids[0])
            self.assertEqual((prefs['scan_interval_seconds'], prefs['group_proactive_message_threshold']), (2, 0))
            self.assertEqual(store.call(lambda: store.db.execute('SELECT count(*) FROM group_message_eligibility').fetchone()[0]), 0)
            self.assertEqual(store.ai_candidates(), [])
        backups = list((self.path.parent/'migration-backups').glob('before-v8-*.sqlite3'))
        self.assertTrue(backups)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
