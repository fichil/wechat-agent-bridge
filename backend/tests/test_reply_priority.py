"""Regression for the 111-message / 20-image / ten-minute group reply backlog."""
import base64
from contextlib import closing
import io
import json
import threading
import time
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from backend.app.conversations import ConversationError
from backend.app.storage import Store
from test_roles import RoleTestCase
import test_group_scheduling as groups


class ReplyPriorityTests(RoleTestCase):
    configured = groups.GroupSchedulingTests.configured
    ingest = groups.GroupSchedulingTests.ingest
    delta = groups.GroupSchedulingTests.delta
    wait_run = groups.GroupSchedulingTests.wait_run
    finish = groups.GroupSchedulingTests.finish
    group = groups.GroupSchedulingTests.group
    ready = groups.GroupSchedulingTests.ready
    settle = groups.GroupSchedulingTests.settle
    start = groups.GroupSchedulingTests.start
    envelope = groups.GroupSchedulingTests.envelope

    def mention(self, index, **fields):
        return self.delta(index, mentioned_wxids=['wxid_synthetic_0'], mention_status='known', **fields)

    def cache_image(self, index):
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'red').save(image, format='PNG')
        mid = self.store.call(lambda: self.store.db.execute('SELECT message_id FROM messages WHERE conversation_id=? AND local_id=?', (self.cids[0], index)).fetchone()[0])
        self.store.image(self.ids[0], self.cids[0], mid, dict(status='available', mime_type='image/png', data_base64=base64.b64encode(image.getvalue()).decode()))

    def queued(self):
        self.settle()
        return self.store.start_ai_run(self.ids[0], self.cids[0], uuid4().hex, 'auto')

    def perform(self, run):
        self.app.state.ai.perform(self.ids[0], self.cids[0], run['run_id'])
        return self.store.ai_run(self.ids[0], self.cids[0], run['run_id'])

    def test_111_messages_20_images_are_processed_with_first_reply(self):
        self.group(100)
        for index in range(1, 111):
            media = index % 5 == 0 and index <= 100
            self.delta(index, **({'type': 'sticker' if index % 10 else 'image', 'content': {}} if media else {}))
            if media:
                self.cache_image(index)
        self.mention(111, content={'text': '当前问题'})
        before = len(self.provider.requests)
        first = self.start()
        self.assertEqual(first['status'], 'completed', first)
        self.assertEqual(first['processing_type'], 'realtime_reply')
        self.assertEqual(len(self.provider.requests), before + 1)
        self.assertEqual(first['delivery']['total_parts'], 1)
        self.assertEqual(first['message_count'], 111)
        self.assertTrue(first['round_final'])
        self.assertIn('received_to_request', first['timings_ms'])
        self.finish(first)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.ready(), [])
        self.assertEqual(self.store.call(lambda: self.store.db.execute("SELECT count(*) FROM group_round_messages WHERE done=0").fetchone()[0]), 0)
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]), 1)

    def test_inflight_threshold_yields_after_one_call_and_suppresses_stale_reply(self):
        self.group(1)
        for index in range(1, 81): self.delta(index)
        generate = self.provider.generate
        def incoming(*args):
            self.mention(81)
            return generate(*args)
        with patch.object(self.provider, 'generate', side_effect=incoming):
            first = self.start()
        self.assertEqual(first['delivery']['status'], 'not_required')
        second = self.start()
        self.assertEqual(second['processing_type'], 'realtime_reply')
        self.assertEqual(second['delivery']['status'], 'queued')
        self.finish(second)
        self.assertEqual(self.store.ai_candidates(), [])

    def test_queued_retry_suspends_preserving_budget_and_rebases_memory(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            failed = self.perform(background)
        self.assertEqual(failed['status'], 'queued')
        self.mention(81)
        reply = self.start()
        suspended = self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])
        self.assertEqual(suspended['status'], 'suspended')
        self.assertEqual(suspended['attempt_count'], 1)
        self.finish(reply)
        # Preserve the actual provider backoff, then resume the same logical job.
        with patch('backend.app.ai_retry_storage.time.time', return_value=(suspended.get('next_retry_at') or time.time()) + 1):
            resumed = self.start()
        self.assertEqual(resumed['run_id'], background['run_id'])
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(resumed['attempt_count'], 2)
        self.assertEqual(resumed['retry_count'], 1)
        self.assertEqual(resumed['max_retries'], failed['max_retries'])
        self.assertIn(reply['reply'], self.envelope()['memory'])
        self.assertEqual(resumed['delivery']['status'], 'not_required')

    def test_retry_suspension_survives_restart(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            self.perform(background)
        self.mention(81)
        reply = self.start()
        self.finish(reply)
        self.store.close()
        with closing(Store(self.path)) as store:
            aid, cid = self.ids[0], self.cids[0]
            suspended = store.ai_run(aid, cid, background['run_id'])
            self.assertEqual(suspended['status'], 'suspended')
            with patch('backend.app.ai_retry_storage.time.time', return_value=(suspended.get('next_retry_at') or time.time()) + 1):
                run = store.start_ai_run(aid, cid, uuid4().hex, 'auto')
                snapshot = store.claim_ai_run(aid, cid, run['run_id'])
            self.assertEqual(run['run_id'], background['run_id'])
            self.assertIn(reply['reply'], snapshot['envelope']['memory'])
            self.assertFalse(snapshot['envelope']['reply_allowed'])

    def test_waiting_media_placeholder_is_not_reprocessed_when_ready(self):
        self.group()
        self.delta(1, type='image', content={})
        self.mention(2)
        first = self.start()
        self.assertEqual(first['message_count'], 2)
        self.finish(first)
        self.cache_image(1)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.ready(), [])

    def test_many_mentions_share_one_request_without_duplicate_targets(self):
        self.group()
        for index in range(1, 56): self.mention(index)
        first = self.start()
        self.assertEqual(len(first['reply_trigger_ids']), 55)
        self.finish(first)
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE consumed_run_id IS NULL').fetchone()[0]), 0)

    def test_input_trimming_keeps_question_and_defers_omitted_context(self):
        self.group()
        for index in range(1, 20): self.delta(index, content={'text': 'x'*10000})
        self.mention(20, content={'text': '必须回应的问题'})
        with patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 65536), patch.object(self.provider, 'generate', return_value=dict(memory='压缩记忆', reply='当前回答')) as provider:
            first = self.start()
        envelope = provider.call_args.args[2]
        self.assertEqual(first['delivery']['status'], 'queued')
        self.assertLess(first['message_count'], 20)
        self.assertEqual(envelope['trigger_messages'][0]['content']['text'], '必须回应的问题')
        self.assertFalse(first['round_final'])

    def test_scheduler_starts_idle_ready_mention_within_five_seconds(self):
        self.group()
        self.mention(1)
        self.settle()
        entered = threading.Event()
        original = self.provider.generate
        def generate(*args):
            entered.set()
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=generate):
            started = time.monotonic()
            self.app.state.ai.start_automatic()
            self.app.state.ai.wake.set()
            self.assertTrue(entered.wait(5))
            self.assertLess(time.monotonic() - started, 5)

    def test_correction_during_reply_is_not_marked_as_memorized(self):
        self.group()
        for index in range(1, 31): self.delta(index)
        self.mention(31)
        original = self.provider.generate
        def corrected(*args):
            self.delta(30, content={'text': '最新更正事实'})
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=corrected):
            first = self.start()
        self.finish(first)
        mid, = self.store.call(lambda: self.store.db.execute('SELECT message_id FROM messages WHERE conversation_id=? AND local_id=30', (self.cids[0],)).fetchone())
        row = self.store.call(lambda: self.store.db.execute('SELECT * FROM messages WHERE message_id=?', (mid,)).fetchone())
        self.assertEqual(self.store.call(self.store._ai_item, row, True)[3]['status'], 'pending')
        # The changed member is still owned by the background round and must
        # catch up even without another mention or reaching the group threshold.
        second = self.start()
        self.assertIn('最新更正事实', self.preferences()['memory'])
        self.assertEqual(second['processing_type'], 'memory_backfill')
        self.assertEqual(second['delivery']['status'], 'not_required')

    def test_final_threshold_response_is_suppressed_when_mention_arrives_inflight(self):
        self.group(1)
        self.delta(1)
        original = self.provider.generate
        def incoming(*args):
            self.mention(2)
            return {**original(*args), 'reply': '旧话题的主动发言'}
        with patch.object(self.provider, 'generate', side_effect=incoming):
            first = self.start()
        self.assertEqual(first['delivery']['total_parts'], 0)
        self.assertEqual(self.start()['processing_type'], 'realtime_reply')

    def test_unknown_delivery_blocks_followup_and_backfill(self):
        self.group()
        for index in range(1, 41): self.delta(index)
        self.mention(41)
        first = self.start()
        self.finish(first, 'unknown')
        self.mention(42)
        self.settle()
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]), 1)

    def test_disable_cancels_suspended_retry(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            self.perform(background)
        self.mention(81)
        reply = self.start()
        self.update(self.preferences()['revision'], ai_enabled=False)
        self.assertEqual(self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])['status'], 'cancelled')
        self.assertEqual(self.store.ai_run(self.ids[0], self.cids[0], reply['run_id'])['delivery']['status'], 'cancelled')
        self.assertEqual(self.store.ai_retry_candidates(), [])

    def test_manual_memory_edit_does_not_rebase_suspended_retry(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            self.perform(background)
        self.mention(81)
        reply = self.start()
        self.finish(reply)
        self.update(self.preferences()['revision'], memory='用户手工记忆')
        resumed = self.start()
        self.assertEqual(resumed['status'], 'conflicted')
        self.assertEqual(self.preferences()['memory'], '用户手工记忆')

    def test_unclaimed_background_can_yield_and_resume_without_snapshot(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        self.mention(81)
        self.store.ai_retry_candidates()
        self.assertEqual(self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])['status'], 'suspended')
        reply = self.start()
        self.finish(reply)
        resumed = self.start()
        self.assertEqual(resumed['run_id'], background['run_id'])
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(resumed['attempt_count'], 1)
        self.assertEqual(resumed['retry_count'], 0)

    def test_suspended_background_closes_if_reply_covers_all_remaining_messages(self):
        self.group(1)
        for index in range(1, 10): self.delta(index)
        background = self.queued()
        self.mention(10)
        reply = self.start()
        suspended = self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])
        self.assertEqual(suspended['status'], 'empty')
        self.assertEqual(reply['delivery']['status'], 'queued')
