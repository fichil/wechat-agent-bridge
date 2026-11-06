"""An explicit group catch-up owns bounded memory-only batches, not live triggers."""
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from backend.app.storage import Store
from backend.app.conversations import ConversationError
from test_roles import RoleTestCase
import test_reply_priority as priority


class ManualMemoryTests(RoleTestCase):
    configured = priority.ReplyPriorityTests.configured
    ingest = priority.ReplyPriorityTests.ingest
    delta = priority.ReplyPriorityTests.delta
    wait_run = priority.ReplyPriorityTests.wait_run
    finish = priority.ReplyPriorityTests.finish
    group = priority.ReplyPriorityTests.group
    ready = priority.ReplyPriorityTests.ready
    settle = priority.ReplyPriorityTests.settle
    start = priority.ReplyPriorityTests.start
    envelope = priority.ReplyPriorityTests.envelope
    mention = priority.ReplyPriorityTests.mention
    perform = priority.ReplyPriorityTests.perform

    def manual(self):
        self.settle()
        run = self.store.start_ai_run(self.ids[0], self.cids[0], uuid4().hex, 'manual')
        return self.perform(run)

    def history(self, count, **changes):
        for index in range(1, count+1):
            self.ingest(index, **changes)

    def test_history_continues_in_bounded_batches_without_threshold_or_sends(self):
        self.group(100)
        self.history(223, type='image', content={})
        self.assertEqual(self.store.ai_candidates(), [])
        first = self.manual()
        self.assertEqual(first['message_count'], 100)
        self.assertEqual(first['round_message_count'], 223)
        self.assertEqual(first['processing_type'], 'memory_backfill')
        self.assertFalse(self.envelope()['reply_allowed'])
        self.assertFalse(self.envelope()['role_card_edit_allowed'])
        self.assertEqual(first['delivery']['status'], 'not_required')
        self.update(self.preferences()['revision'], group_proactive_message_threshold=0)
        self.assertEqual(len(self.store.ai_candidates()), 1)
        second = self.start()
        third = self.start()
        self.assertEqual([second['message_count'], third['message_count']], [100, 23])
        self.assertEqual(third['round_completed_count'], 223)
        self.assertTrue(third['round_final'])
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.store.ai_runs(self.ids[0], self.cids[0])['pending_count'], 0)
        self.assertEqual(self.store.delivery_candidates(), [])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]), 0)

    def test_fixed_snapshot_does_not_consume_later_untriggered_messages(self):
        self.group(100)
        self.history(180)
        first = self.manual()
        self.delta(181)
        self.settle()
        second = self.start()
        self.assertEqual(second['round_id'], first['round_id'])
        self.assertEqual(second['message_count'], 80)
        self.assertEqual(len(self.ready()), 1)
        self.assertEqual(self.store.ai_candidates(), [])

    def test_new_mention_precedes_remaining_memory_and_catchup_resumes(self):
        self.group(100)
        self.history(220)
        original = self.provider.generate
        def incoming(*args):
            self.mention(221)
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=incoming):
            first = self.manual()
        self.assertEqual(first['delivery']['status'], 'not_required')
        reply = self.start()
        self.assertEqual(reply['processing_type'], 'realtime_reply')
        self.assertEqual(reply['message_count'], 1)
        self.finish(reply)
        batches = [self.start(), self.start()]
        self.assertEqual([r['message_count'] for r in batches], [100, 20])
        self.assertTrue(batches[-1]['round_final'])
        self.assertTrue(all(r['delivery']['status']=='not_required' for r in batches))
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]), 1)

    def test_retry_yields_to_mention_then_keeps_original_budget(self):
        self.group(100)
        self.history(180)
        queued = self.store.start_ai_run(self.ids[0], self.cids[0], uuid4().hex, 'manual')
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            retry = self.perform(queued)
        self.assertEqual(retry['status'], 'queued')
        self.mention(181)
        reply = self.start()
        suspended = self.store.ai_run(self.ids[0], self.cids[0], queued['run_id'])
        self.assertEqual(suspended['status'], 'suspended')
        self.finish(reply)
        with patch('backend.app.ai_retry_storage.time.time', return_value=suspended['next_retry_at']+1):
            resumed = self.start()
        self.assertEqual(resumed['run_id'], queued['run_id'])
        self.assertEqual(resumed['attempt_count'], 2)
        self.assertEqual(resumed['max_retries'], retry['max_retries'])
        self.assertEqual(resumed['delivery']['status'], 'not_required')
        self.assertTrue(self.start()['round_final'])

    def test_restart_preserves_catchup_snapshot_and_next_batch(self):
        self.group()
        self.history(180)
        first = self.manual()
        self.store.close()
        with closing(Store(self.path)) as store:
            aid,cid=self.ids[0],self.cids[0]
            self.assertEqual(len(store.ai_candidates()), 1)
            run=store.start_ai_run(aid,cid,uuid4().hex,'auto')
            snapshot=store.claim_ai_run(aid,cid,run['run_id'])
            self.assertEqual(run['round_id'],first['round_id'])
            self.assertEqual(len(snapshot['envelope']['messages']),80)
            self.assertFalse(snapshot['envelope']['reply_allowed'])

    def test_disabling_ai_stops_catchup(self):
        self.group()
        self.history(180)
        self.manual()
        self.update(self.preferences()['revision'], ai_enabled=False)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertIsNone(self.store.call(lambda:self.store._group_round(self.ids[0],self.cids[0])))

    def test_waiting_messages_stay_waiting_and_repeated_request_keeps_snapshot(self):
        self.group()
        self.history(110)
        self.ingest(111,type='voice',content={'transcript':None})
        self.ingest(112,type='file',content={})
        request=uuid4().hex
        run=self.store.start_ai_run(self.ids[0],self.cids[0],request,'manual')
        self.ingest(113)
        repeated=self.store.start_ai_run(self.ids[0],self.cids[0],request,'manual')
        self.assertEqual(run['run_id'],repeated['run_id'])
        self.assertEqual(run['round_message_count'],110)
        self.perform(run)
        self.assertEqual(self.start()['message_count'],10)
        counts=self.store.ai_runs(self.ids[0],self.cids[0])
        self.assertEqual(counts['waiting_count'],2)
        self.assertEqual(counts['pending_count'],1)
        self.assertEqual(self.store.ai_candidates(),[])
