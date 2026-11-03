from contextlib import closing
from copy import deepcopy
import json
import base64
import io
import sqlite3
import time
from unittest.mock import Mock, patch
from uuid import uuid4

from backend.app.ai_execution import AiExecution
from backend.app.conversations import ConversationError
from backend.app.storage import Store
from test_roles import RoleTestCase
from PIL import Image
import test_delivery as delivery
import test_group_scheduling as groups


class RoutingTests(RoleTestCase):
    configured = delivery.DeliveryTests.configured
    ingest = delivery.DeliveryTests.ingest
    delta = delivery.DeliveryTests.delta
    finish = delivery.DeliveryTests.finish
    group = groups.GroupSchedulingTests.group

    def sql(self, query, args=()):
        def execute():
            with self.store.db:
                return self.store.db.execute(query, args).fetchall()
        return self.store.call(execute)

    def begin(self, account=0, trigger='manual'):
        run = self.store.start_ai_run(self.ids[account], self.cids[account], uuid4().hex, trigger)
        return self.step(run)

    def step(self, run):
        self.app.state.ai.perform(run['account_id'], run['conversation_id'], run['run_id'])
        return self.store.ai_run(run['account_id'], run['conversation_id'], run['run_id'])

    def copied_store(self):
        path = self.path.with_name('restart.sqlite3')
        def backup():
            with closing(sqlite3.connect(path)) as target:
                self.store.db.backup(target)
        self.store.call(backup)
        return Store(path)

    def failure(self, code='ai_network_error', retryable=True, retry_after=0):
        error = ConversationError(code, 'synthetic failure')
        error.retryable, error.retry_after = retryable, retry_after
        return error

    def test_fallback_wraps_and_next_batch_starts_at_explicit_profile(self):
        self.configured()
        b = self.create_api(name='B', provider='anthropic', protocol='anthropic_messages', base_url='https://example.test')
        c = self.create_api(name='C', provider='gemini', protocol='gemini_generate_content', base_url='https://example.test')
        self.update(self.preferences()['revision'], api_profile_id=b['profile_id'], ai_max_retries=0)
        self.delta()
        original = self.provider.generate
        seen, envelopes = [], []
        def generate(profile, key, envelope, mode):
            seen.append(profile['profile_id']); envelopes.append(deepcopy(envelope))
            if profile['profile_id'] != self.profile['profile_id']:
                raise self.failure('ai_provider_error', False)
            return original(profile, key, envelope, mode)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.begin()
            self.assertEqual(run['profile_id'], c['profile_id'])
            self.assertLessEqual(run['next_retry_at'], time.time())
            run = self.step(run)
            run = self.step(run)
        self.assertEqual(seen, [b['profile_id'], c['profile_id'], self.profile['profile_id']])
        self.assertEqual(envelopes[0], envelopes[1]); self.assertEqual(envelopes[1], envelopes[2])
        self.assertEqual((run['status'], run['attempt_count'], run['retry_count']), ('completed', 3, 0))
        self.assertEqual(self.preferences()['api_profile_id'], b['profile_id'])
        self.assertEqual(self.preferences()['effective']['active_api_profile_id'], b['profile_id'])
        self.assertEqual(self.preferences()['memory_revision'], 1)
        self.assertEqual(len(self.sql('SELECT * FROM deliveries')), 1)
        self.finish(run)
        self.delta(2)
        with patch.object(self.provider, 'generate', wraps=original) as generate:
            second = self.begin()
        self.assertEqual((second['status'], second['attempt_count']), ('completed', 1))
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0]['profile_id'], b['profile_id'])
        self.update(self.preferences(1)['revision'], index=1, ai_enabled=True, api_profile_id=b['profile_id'])
        self.delta(1, account=1)
        self.assertEqual(self.begin(account=1)['profile_id'], b['profile_id'])

    def test_all_agents_share_extra_retry_budget_and_skip_permanent_errors(self):
        self.configured()
        b = self.create_api(name='B')
        self.update(self.preferences()['revision'], ai_max_retries=2)
        self.delta()
        seen = []
        def generate(profile, *args):
            seen.append(profile['profile_id'])
            raise self.failure('ai_provider_error', profile['profile_id'] == b['profile_id'], 120)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.begin()
            self.assertEqual(run['retry_count'], 0)
            run = self.step(run)
            self.assertGreater(run['next_retry_at'], time.time() + 110)
            self.assertFalse(self.store.ai_retry_candidates())
            run = self.step(run)
            run = self.step(run)
        self.assertEqual(seen, [self.profile['profile_id'], b['profile_id'], b['profile_id'], b['profile_id']])
        self.assertEqual((run['status'], run['retry_count'], run['attempt_count']), ('failed', 2, 4))
        self.assertEqual(self.preferences()['memory_revision'], 0)
        self.assertIsNone(self.sql('SELECT consumed_run_id FROM reply_triggers')[0][0])

    def test_unverified_primary_is_ready_when_backup_is_available(self):
        self.configured()
        b = self.create_api(name='B')
        self.store.save_verification(self.profile['profile_id'], 1, dict(status='failed', vision='unverified', output_mode='schema'))
        self.assertTrue(self.preferences()['effective']['ready'])
        self.delta()
        self.assertEqual(self.begin()['profile_id'], b['profile_id'])

    def test_unreadable_and_unverified_candidates_are_skipped(self):
        self.configured()
        bad = self.create_api(name='unreadable')
        unverified = self.create_api(name='unverified')
        good = self.create_api(name='good')
        self.sql('UPDATE api_profiles SET secret=? WHERE profile_id=?', (b'invalid-dpapi', bad['profile_id']))
        self.store.save_verification(unverified['profile_id'], 1, dict(status='failed', vision='unverified', output_mode='schema'))
        self.delta()
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        self.assertEqual(run['profile_id'], good['profile_id'])
        self.assertEqual(self.step(run)['status'], 'completed')

    def test_picture_uses_vision_backup_and_never_downgrades_the_snapshot(self):
        self.configured()
        b = self.create_api(name='vision')
        self.store.save_verification(self.profile['profile_id'], 1, dict(status='passed', vision='unsupported', output_mode='schema'))
        self.delta(type='image', content={})
        item = self.store.messages(self.ids[0], self.cids[0])['items'][0]
        output = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(output, 'PNG')
        self.store.image(self.ids[0], self.cids[0], item['message_id'], dict(
            status='available', mime_type='image/png', data_base64=base64.b64encode(output.getvalue()).decode()))
        self.update(self.preferences()['revision'], ai_max_retries=0)
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        self.assertEqual(run['profile_id'], b['profile_id'])
        self.assertEqual((run['status'], run['attempt_count']), ('failed', 1))

    def test_empty_private_reply_switches_without_consuming_messages(self):
        self.configured(); b = self.create_api(name='B')
        self.delta(1); self.delta(2); self.delta(3)
        original = self.provider.generate
        def generate(profile, key, envelope, mode):
            self.assertTrue(envelope['reply_required'])
            self.assertEqual(len(envelope['reply_target_ids']), 3)
            if profile['profile_id'] == self.profile['profile_id']:
                return dict(memory='must not commit', reply='  ')
            return original(profile, key, envelope, mode)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.begin()
            self.assertEqual(self.preferences()['memory_revision'], 0)
            self.assertEqual(self.sql('SELECT count(*) FROM reply_triggers WHERE consumed_run_id IS NULL')[0][0], 3)
            run = self.step(run)
        self.assertEqual((run['status'], run['profile_id'], run['message_count']), ('completed', b['profile_id'], 3))
        self.assertEqual(len(self.sql('SELECT * FROM deliveries')), 1)

    def test_continuous_private_messages_do_not_extend_merge_window(self):
        self.configured()
        current = time.time()
        with patch('time.time', return_value=current):
            self.delta(1)
        with patch('time.time', return_value=current + .8):
            self.delta(2)
            self.assertEqual(self.store.ai_candidates(), [])
        with patch('time.time', return_value=current + 1.1):
            self.delta(3)
            self.assertEqual(len(self.store.ai_candidates()), 1)
        run = self.begin(trigger='auto')
        self.assertEqual(run['message_count'], 3)

    def test_later_messages_stay_out_of_failed_snapshot(self):
        self.configured(); self.create_api(name='B'); self.delta(1)
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        self.delta(2)
        run = self.step(run)
        self.assertEqual(run['message_count'], 1)
        self.finish(run)
        later = self.begin()
        self.assertEqual((later['status'], later['message_count']), ('completed', 1))
        self.assertEqual(len(self.sql('SELECT * FROM deliveries')), 2)

    def test_arrival_during_model_request_starts_its_own_merge_window(self):
        self.configured()
        current = time.time()
        with patch('time.time', return_value=current):
            self.delta(1)
        original = self.provider.generate
        def generate(*args):
            with patch('time.time', return_value=current + 10):
                self.delta(2)
            return original(*args)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.begin()
        self.finish(run)
        with patch('time.time', return_value=current + 10.5):
            self.assertEqual(self.store.ai_candidates(), [])
        with patch('time.time', return_value=current + 11.1):
            self.assertEqual(len(self.store.ai_candidates()), 1)

    def test_fallback_uses_its_own_protocol_and_key(self):
        self.configured()
        b = self.create_api(name='B', provider='anthropic', protocol='anthropic_messages',
                            base_url='https://example.test', api_key='different-synthetic-key')
        self.delta()
        original = self.provider.generate
        envelopes = []
        def generate(profile, key, envelope, mode):
            envelopes.append(deepcopy(envelope))
            if profile['profile_id'] == self.profile['profile_id']:
                self.assertEqual(key, 'synthetic-test-only')
                raise self.failure()
            self.assertEqual(key, 'different-synthetic-key')
            self.assertEqual(profile['protocol'], 'anthropic_messages')
            return original(profile, key, envelope, mode)
        with patch.object(self.provider, 'generate', side_effect=generate):
            run = self.step(self.begin())
        self.assertEqual((run['status'], run['profile_id']), ('completed', b['profile_id']))
        self.assertEqual(envelopes[0], envelopes[1])
        self.assertIn('system', self.provider.requests[-1])
        self.assertIn('messages', self.provider.requests[-1])

    def test_interrupted_last_attempt_does_not_reset_budget_on_restart(self):
        self.configured(); self.delta()
        self.update(self.preferences()['revision'], ai_max_retries=0)
        restored = None
        def interrupted(*args):
            nonlocal restored
            restored = self.copied_store()
            raise self.failure()
        with patch.object(self.provider, 'generate', side_effect=interrupted):
            run = self.begin()
        with closing(restored):
            current = restored.ai_run(self.ids[0], self.cids[0], run['run_id'])
            self.assertEqual((current['status'], current['attempt_count']), ('failed', 1))
            self.assertEqual(current['profile_attempts'][0]['status'], 'interrupted')
            self.assertEqual(restored.ai_retry_candidates(), [])

    def test_unused_candidate_deleted_during_call_is_skipped(self):
        self.configured(); b = self.create_api(name='B'); c = self.create_api(name='C'); self.delta()
        def fail(*args):
            self.store.delete_api_profile(b['profile_id'], b['revision'])
            raise self.failure()
        with patch.object(self.provider, 'generate', side_effect=fail):
            run = self.begin()
        self.assertEqual(run['profile_id'], c['profile_id'])
        self.assertEqual(self.step(run)['status'], 'completed')

    def test_candidate_becoming_unverified_while_queued_is_not_called(self):
        self.configured(); b = self.create_api(name='B'); c = self.create_api(name='C'); self.delta()
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        self.store.save_verification(b['profile_id'], 1, dict(status='failed', vision='unverified', output_mode='schema'))
        with patch.object(self.provider, 'generate') as generate:
            run = self.step(run)
        generate.assert_not_called()
        self.assertEqual((run['profile_id'], run['attempt_count']), (c['profile_id'], 1))
        self.assertEqual(self.step(run)['status'], 'completed')

    def test_decryption_failure_after_selection_can_use_next_agent(self):
        self.configured(); b = self.create_api(name='B'); self.delta()
        with patch('backend.app.secret_protection.verify', return_value=True), patch(
                'backend.app.secret_protection.reveal', side_effect=ConversationError('api_key_unreadable', 'synthetic')):
            run = self.begin()
        self.assertEqual((run['profile_id'], run['attempt_count']), (b['profile_id'], 0))
        self.assertEqual(self.step(run)['status'], 'completed')

    def test_restart_preserves_next_candidate_and_consumed_budget(self):
        self.configured(); b = self.create_api(name='B'); self.delta()
        self.update(self.preferences()['revision'], ai_max_retries=0)
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        with closing(self.copied_store()) as restored:
            ai = AiExecution(restored, Mock(), self.provider, automatic=False)
            try:
                ai.perform(self.ids[0], self.cids[0], run['run_id'])
                final = restored.ai_run(self.ids[0], self.cids[0], run['run_id'])
                self.assertEqual((final['status'], final['profile_id'], final['attempt_count']), ('completed', b['profile_id'], 2))
            finally:
                ai.close()

    def test_configuration_change_cancels_old_fallback(self):
        self.configured(); b = self.create_api(name='B'); self.delta()
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        self.update(self.preferences()['revision'], api_profile_id=b['profile_id'])
        with patch.object(self.provider, 'generate') as generate:
            final = self.step(run)
        generate.assert_not_called()
        self.assertEqual(final['status'], 'conflicted')

    def test_new_batch_after_upgrade_ignores_legacy_route_and_uses_default(self):
        self.configured(); b = self.create_api(name='B'); self.delta()
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin()
        run = self.step(run); self.finish(run)
        self.assertEqual([(a['profile_id'], a['status']) for a in run['profile_attempts']],
                         [(self.profile['profile_id'], 'failed'), (b['profile_id'], 'completed')])
        self.assertEqual(self.sql("SELECT * FROM metadata WHERE key GLOB 'ai_agent_route:*'"), [])
        aid, cid = self.ids[0], self.cids[0]
        legacy_route = dict(configuration=run['route_configuration'], epoch=run['epoch'],
                            profile_id=b['profile_id'], revision=b['revision'])
        self.sql('INSERT INTO metadata VALUES(?,?)', (f'ai_agent_route:{aid}:{cid}', json.dumps(legacy_route)))
        self.delta(2)
        with closing(self.copied_store()) as restored:
            prefs = restored.preferences(aid, cid)['effective']
            self.assertEqual((prefs['api_source'], prefs['active_api_profile_id']), ('default', self.profile['profile_id']))
            with closing(AiExecution(restored, self.app.state.ai.bridge, self.provider, automatic=False)) as ai:
                second = restored.start_ai_run(aid, cid, uuid4().hex, 'manual')
                with patch.object(self.provider, 'generate', wraps=self.provider.generate) as generate:
                    ai.perform(aid, cid, second['run_id'])
                generate.assert_called_once()
                self.assertEqual(generate.call_args.args[0]['profile_id'], self.profile['profile_id'])
                self.assertEqual(restored.ai_run(aid, cid, second['run_id'])['status'], 'completed')
        self.store.set_state(aid, 'offline')
        self.store.delete_account(aid, self.store.account(aid)['revision'])
        self.assertEqual(self.sql("SELECT * FROM metadata WHERE key GLOB 'ai_agent_route:*'"), [])
        self.assertEqual(len(self.store.api_profiles()['items']), 2)

    def test_group_threshold_stays_gated_and_fallback_keeps_one_round(self):
        self.group(2); self.create_api(name='B')
        self.delta(1)
        self.assertEqual(self.store.ai_candidates(), [])
        self.delta(2)
        with patch.object(self.provider, 'generate', side_effect=self.failure()):
            run = self.begin(trigger='auto')
        with patch.object(self.provider, 'generate', return_value=dict(memory='group', reply='')):
            final = self.step(run)
        self.assertEqual(final['round_id'], run['round_id'])
        self.assertEqual(final['status'], 'completed')
        self.assertTrue(final['chose_silence'])
        self.assertEqual(self.store.ai_candidates(), [])

    def test_api_test_does_not_fallback(self):
        self.configured(); self.create_api(name='B')
        with patch.object(self.provider, 'test', side_effect=self.failure()) as test:
            response = self.post('api-profiles/test', profile_id=self.profile['profile_id'], expected_revision=1)
            task = response.json()
            for _ in range(100):
                task = self.post('api-profiles/tasks/query', task_id=task['task_id']).json()
                if task['status'] == 'failed': break
                time.sleep(.01)
        self.assertEqual(test.call_count, 1)
        self.assertEqual(task['status'], 'failed')
