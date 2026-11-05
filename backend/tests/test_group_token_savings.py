"""Batching, media coverage and bounded memory regression with synthetic data."""
from contextlib import closing
import json
from unittest.mock import patch

from backend.app.ai_provider import parse_response, build_request
from backend.app.conversations import ConversationError
from backend.app.diagnostics import token_usage
from backend.app.storage import Store
from test_roles import RoleTestCase
import test_reply_priority as priority


class GroupTokenSavingsTests(RoleTestCase):
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
    cache_image = priority.ReplyPriorityTests.cache_image
    queued = priority.ReplyPriorityTests.queued
    perform = priority.ReplyPriorityTests.perform

    def test_hundred_texts_one_request_and_overflow_waits(self):
        self.group(100)
        for i in range(1, 100): self.delta(i)
        self.settle()
        self.assertEqual(self.store.ai_candidates(), [])
        self.delta(100)
        self.delta(101)
        before = len(self.provider.requests)
        run = self.start()
        self.assertEqual(run['message_count'], 100)
        self.assertTrue(run['round_final'])
        self.assertEqual(len(self.provider.requests)-before, 1)
        self.assertEqual(len(self.ready()), 1)
        self.finish(run)
        self.assertEqual(self.store.ai_candidates(), [])

    def test_fifty_nine_then_mention_consumes_sixty(self):
        self.group(100)
        for i in range(1, 60): self.delta(i)
        self.mention(60)
        before = len(self.provider.requests)
        run = self.start()
        self.assertEqual(run['message_count'], 60)
        self.assertEqual(len(self.provider.requests)-before, 1)
        self.assertEqual(self.ready(), [])
        self.assertTrue(run['round_final'])
        self.finish(run)
        self.assertEqual(self.store.ai_candidates(), [])

    def test_media_placeholders_do_not_split_or_reopen_after_cache(self):
        self.group(100)
        for i in range(1, 101):
            self.delta(i, **({'type': 'sticker', 'content': {}} if i <= 30 else {}))
        run = self.start()
        self.assertEqual(run['message_count'], 100)
        self.assertFalse(any(m['content'].get('image_reference') for m in self.envelope()['messages']))
        self.finish(run)
        self.cache_image(1)
        self.assertEqual(self.ready(), [])
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.store.ai_missing_images(self.ids[0], self.cids[0]), [])

    def test_mention_attaches_only_relevant_images_and_all_text(self):
        self.group(100)
        for i in range(1, 10):
            self.delta(i, type='image', content={}); self.cache_image(i)
        self.mention(10)
        run = self.start()
        images = [m for m in self.envelope()['messages'] if m['content'].get('image_reference')]
        self.assertEqual(run['message_count'], 10)
        self.assertEqual(len(images), 3)
        self.assertTrue(run['round_final'])

    def test_quoted_processed_image_is_context_not_a_new_count(self):
        self.group(1)
        item = self.delta(1, type='image', content={}); self.cache_image(1)
        first = self.start(); self.finish(first)
        self.mention(2, content={'text': '解释这张图', 'quoted_server_id': str(item['_ref']['server_id'])})
        second = self.start()
        envelope = self.envelope()
        self.assertEqual(len(envelope['context_only_ids']), 1)
        self.assertEqual(len(envelope['messages']), 2)
        self.assertEqual(sum(bool(m['content'].get('image_reference')) for m in envelope['messages']), 1)
        self.assertEqual(self.ready(), [])
        self.assertTrue(second['round_final'])

    def test_history_is_not_automatically_added_to_live_batch(self):
        self.group(1)
        self.ingest(40)
        self.delta(41)
        run = self.start()
        self.assertEqual(run['message_count'], 1)
        self.assertTrue(run['round_final'])

    def test_old_memory_backed_up_and_compacted_once_before_chat(self):
        self.group(1)
        original = '长期关系事实。' * 1000
        self.update(self.preferences()['revision'], memory=original)
        self.delta(1)
        before = len(self.provider.requests)
        run = self.start()
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(len(self.provider.requests)-before, 2)
        self.assertLessEqual(len(self.preferences()['memory']), 4000)
        compact = json.loads(self.provider.requests[before]['input'][0]['content'][0]['text'])
        self.assertEqual(compact['character_card'], {})
        self.assertEqual(compact['messages'], [])
        self.assertFalse(compact['reply_allowed'])
        backup_key = 'memory-backup:'+self.ids[0]+':'+run['run_id']+':before'
        backup = self.store.call(lambda: json.loads(self.store.db.execute('SELECT value FROM metadata WHERE key=?', (backup_key,)).fetchone()[0]))
        self.assertEqual(backup['memory'], original)
        current = self.store.call(lambda: json.loads(self.store.db.execute('SELECT payload FROM ai_runs WHERE run_id=?', (run['run_id'],)).fetchone()[0]))
        self.assertEqual(current['http_attempt_count'], 2)
        self.store.set_state(self.ids[0], 'offline')
        account = self.store.account(self.ids[0])
        self.store.delete_account(self.ids[0], account['revision'])
        self.assertIsNone(self.store.call(lambda: self.store.db.execute('SELECT 1 FROM metadata WHERE key=?', (backup_key,)).fetchone()))

    def test_oversized_new_memory_has_one_compaction_and_atomic_commit(self):
        self.group(1); self.delta(1)
        with patch.object(self.provider, 'generate', side_effect=[dict(memory='x'*6000, reply='回答'), dict(memory='摘要', reply='')]) as calls:
            run = self.start()
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.preferences()['memory'], '摘要')
        self.assertEqual(run['delivery']['total_parts'], 1)

    def test_failed_compaction_pauses_without_consuming_or_retrying(self):
        self.group(1); self.delta(1)
        with patch.object(self.provider, 'generate', return_value=dict(memory='x'*6000, reply='')) as calls:
            run = self.start()
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.preferences()['memory'], '')
        self.assertEqual(len(self.ready()), 1)
        self.assertEqual(self.store.ai_retry_candidates(), [])

    def test_interrupted_compaction_is_not_reissued_after_restart(self):
        self.group(1)
        self.update(self.preferences()['revision'], memory='x'*6000)
        self.delta(1)
        queued = self.queued()
        self.store.claim_ai_run(self.ids[0], self.cids[0], queued['run_id'])
        self.store.begin_memory_compaction(self.ids[0], self.cids[0], queued['run_id'], 'before', 'x'*6000)
        self.store.close()
        with closing(Store(self.path)) as store:
            run = store.ai_run(self.ids[0], self.cids[0], queued['run_id'])
            store.claim_ai_run(self.ids[0], self.cids[0], queued['run_id'])
            with self.assertRaises(ConversationError) as raised:
                store.begin_memory_compaction(self.ids[0], self.cids[0], queued['run_id'], 'before', 'x'*6000)
            self.assertEqual(raised.exception.code, 'ai_memory_compaction_interrupted')

    def test_output_capacity_never_repeats_same_provider(self):
        self.group(1); self.delta(1)
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_output_capacity', 'bounded')) as calls:
            run = self.start()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(self.ready()), 1)

    def test_capacity_error_uses_at_most_one_of_multiple_backups(self):
        self.group(1)
        self.create_api(name='备用一')
        self.create_api(name='备用二')
        self.delta(1)
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_output_capacity', 'bounded')) as calls:
            first = self.perform(self.queued())
            self.assertEqual(first['status'], 'queued')
            final = self.perform(first)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(len(self.ready()), 1)

    def test_compaction_cannot_overwrite_concurrent_manual_memory(self):
        self.group(1)
        self.update(self.preferences()['revision'], memory='x'*6000)
        self.delta(1)
        def change(*args):
            self.update(self.preferences()['revision'], memory='用户手工更正')
            return dict(memory='压缩内容', reply='')
        with patch.object(self.provider, 'generate', side_effect=change):
            result = self.start()
        self.assertIn(result['status'], ('failed', 'conflicted'))
        self.assertEqual(self.preferences()['memory'], '用户手工更正')
        self.assertEqual(len(self.ready()), 1)

    def test_usage_unknown_and_provider_mapping(self):
        self.assertTrue(all(value is None for value in token_usage(None).values()))
        self.assertEqual(token_usage(dict(prompt_tokens=10, completion_tokens=3, prompt_cache_hit_tokens=4)),
                         dict(input_tokens=10, output_tokens=3, total_tokens=13, cached_tokens=4))
        self.assertEqual(token_usage(dict(input_tokens=0, output_tokens=0))['total_tokens'], 0)

    def test_capacity_errors_are_distinct_from_invalid_json(self):
        with self.assertRaises(ConversationError) as raised:
            parse_response('openai_chat_completions', dict(choices=[dict(finish_reason='length', message={})]))
        self.assertEqual(raised.exception.code, 'ai_output_capacity')

    def test_instructions_only_sent_once(self):
        self.group(1); self.delta(1)
        self.start()
        body = self.provider.requests[-1]
        self.assertNotIn('instructions', self.envelope())
        self.assertIn('3000', body['instructions'])

    def test_disabled_group_maintenance_preserves_switch_and_creates_backup(self):
        from backend.compact_group_memory import compact_disabled_group
        self.group(100)
        self.update(self.preferences()['revision'], memory='重要事实。'*1500, ai_enabled=False)
        result = self.store.call(lambda: compact_disabled_group(self.store.db, self.ids[0], self.cids[0], provider=self.provider))
        self.assertLessEqual(result['after_chars'], 4000)
        self.assertFalse(self.preferences()['ai_enabled'])
        self.assertEqual(self.preferences()['ai_max_retries'], 2)
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]), 0)
        self.assertEqual(self.envelope()['character_card'], {})
        self.assertFalse(self.envelope()['role_card_edit_allowed'])

    def test_failed_maintenance_cannot_repeat_same_revision(self):
        from backend.compact_group_memory import compact_disabled_group
        self.group(100)
        self.update(self.preferences()['revision'], memory='x'*6000, ai_enabled=False)
        def run():
            return self.store.call(lambda: compact_disabled_group(self.store.db, self.ids[0], self.cids[0], provider=self.provider))
        with patch.object(self.provider, 'generate', side_effect=RuntimeError('synthetic')) as calls:
            with self.assertRaises(RuntimeError): run()
            with self.assertRaises(ValueError): run()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.preferences()['memory'], 'x'*6000)

    def test_maintenance_refuses_enabled_conversation(self):
        from backend.compact_group_memory import compact_disabled_group
        self.group(100)
        with self.assertRaises(ValueError):
            self.store.call(lambda: compact_disabled_group(self.store.db, self.ids[0], self.cids[0], provider=self.provider))
