"""Conversation-local role edits, atomic commits, protocol and v12 upgrades."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from backend.app.ai_execution_models import DEFAULT_INSTRUCTIONS
from backend.app.ai_instructions_v11 import V11_DEFAULT_INSTRUCTIONS
from backend.app.ai_provider import Provider, build_request, parse_response
from backend.app.conversations import ConversationError
from backend.app.role_evolution import derived_name, merge_role_patch
from backend.app.role_evolution_schema import ROLE_EVOLUTION_SCHEMA
from backend.app.storage import Store
from backend.app.auth import AuthStore
from bridge_portable.configuration import export_configuration, import_configuration
from bridge_portable.migrate import validate_db, snapshot, export_data, import_data
from migration_helpers import remove_v12
import test_delivery as delivery
import test_reply_priority as priority
import test_roles as roles


class RoleEvolutionTests(roles.RoleTestCase):
    delta = delivery.DeliveryTests.delta
    ingest = delivery.DeliveryTests.ingest

    def configure(self):
        self.original = self.create()
        self.profile = self.create_api()
        self.update(ai_enabled=True, ai_max_retries=0)

    def process(self, changes=None, account=0, callback=None, reply='当前回复'):
        aid, cid = self.ids[account], self.cids[account]
        # Confirm synthetic queued deliveries before the next logical turn.
        for job in self.store.delivery_candidates():
            claimed = self.store.claim_delivery(job['run_id'], job['part_index'])
            if claimed:
                self.store.finish_delivery(claimed, dict(status='sent', code='synthetic'))
        run = self.store.start_ai_run(aid, cid, uuid4().hex)
        def generate(_profile, _key, envelope, _mode):
            self.last_envelope = envelope
            if callback:
                callback()
            result = dict(memory='本轮记忆', reply=reply)
            if changes is not None:
                result['role_card_update'] = json.dumps(changes, ensure_ascii=False) if not isinstance(changes, str) else changes
            return result
        with patch.object(self.provider, 'generate', side_effect=generate):
            self.app.state.ai.perform(aid, cid, run['run_id'])
        return self.store.ai_run(aid, cid, run['run_id'])

    def role(self, account=0):
        return self.store.role_card(self.preferences(account)['effective']['role_card_code'])

    def test_create_reuse_noop_duplicate_completion_and_restart(self):
        self.configure(); self.delta()
        events = []; self.store.notify = events.append
        first = self.process({'personality': '逐渐熟悉，主动关心'})
        self.assertEqual(first['status'], 'completed', first)
        self.assertTrue(self.last_envelope['role_card_edit_allowed'])
        self.assertEqual(self.last_envelope['character_card'], self.original['card'])
        saved = self.role(); code = saved['card_code']
        cname = self.store.conversations(self.ids[0])['items'][0]['name']
        self.assertEqual(saved['name'], self.original['name'] + ' - ' + cname)
        self.assertEqual(first['role_card_change'], dict(action='created', card_code=code, name=saved['name'], revision=1, changed_fields=['personality']))
        self.assertEqual(self.store.role_card(self.original['card_code'])['card'], self.original['card'])
        self.assertEqual(saved['card']['data']['extensions'], self.original['card']['data']['extensions'])
        self.assertEqual(saved['card']['vendor'], self.original['card']['vendor'])
        self.assertEqual(self.preferences()['memory_revision'], 1)
        self.assertEqual(first['delivery']['total_parts'], 1)
        self.assertTrue(any(e.get('resources') == ['role_cards'] for e in events))
        self.store.finish_ai_run(self.ids[0], self.cids[0], first['run_id'], result=dict(memory='重复', reply='重复', role_card_update='{"personality":"重复"}'))
        self.assertEqual(self.role()['revision'], 1)
        self.assertEqual(len(self.store.role_cards()['items']), 2)
        self.delta(2)
        second = self.process({'personality': '新的长期性格'})
        self.assertEqual(second['role_card_change']['action'], 'updated')
        self.assertEqual(self.last_envelope['character_card'], saved['card'])
        self.assertEqual(self.role()['card_code'], code)
        self.assertEqual(self.role()['revision'], 2)
        self.delta(3)
        noop = self.process({'personality': '新的长期性格'})
        self.assertIsNone(noop['role_card_change'])
        self.assertEqual(self.role()['revision'], 2)
        self.store.close()
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.preferences(self.ids[0], self.cids[0])['role_card_code'], code)
            self.assertEqual(reopened.role_card(code)['revision'], 2)
            self.assertEqual(reopened.call(lambda: reopened.db.execute('PRAGMA foreign_key_check').fetchall()), [])

    def test_legacy_and_empty_patch_do_not_create(self):
        self.configure()
        for index, value in enumerate((None, '', {}, {'personality': ''}), 1):
            self.delta(index)
            run = self.process(value)
            self.assertEqual(run['status'], 'completed', run)
            self.assertIsNone(run.get('role_card_change'))
        self.assertEqual(len(self.store.role_cards()['items']), 1)
        self.assertIsNone(self.preferences()['role_card_code'])

    def test_explicit_card_switch_and_switch_back_create_fresh_copies(self):
        self.configure()
        self.update(self.preferences()['revision'], role_card_code=self.original['card_code'])
        self.delta(); self.process({'personality': '第一阶段'})
        old = self.role()
        other = self.create(roles.card('另一角色'))
        self.update(self.preferences()['revision'], role_card_code=other['card_code'])
        self.delta(2); self.process({'scenario': '新场景'})
        second = self.role()
        self.assertNotEqual(old['card_code'], second['card_code'])
        self.assertTrue(second['name'].startswith('另一角色 - '))
        self.assertEqual(self.store.role_card(old['card_code'])['card'], old['card'])
        self.update(self.preferences()['revision'], role_card_code=old['card_code'])
        self.delta(3); self.process({'personality': '回到旧设定后新发展'})
        self.assertNotEqual(self.role()['card_code'], old['card_code'])
        self.assertEqual(self.store.role_card(old['card_code'])['revision'], 1)

    def test_same_named_conversations_and_shared_derived_card_are_isolated(self):
        self.configure()
        self.update(index=1, ai_enabled=True, ai_max_retries=0)
        self.delta(); self.process({'personality': '甲'})
        first = self.role()
        self.delta(account=1); self.process({'personality': '乙'}, account=1)
        other = self.role(1)
        self.assertNotEqual(first['card_code'], other['card_code'])
        self.update(self.preferences(1)['revision'], index=1, role_card_code=first['card_code'])
        self.delta(2); self.process({'personality': '甲的新设定'})
        self.assertNotEqual(self.role()['card_code'], first['card_code'])
        self.assertEqual(self.role(1)['card'], first['card'])
        current = self.role()
        defaults = self.store.ai_defaults()
        self.store.save_ai_defaults(defaults['revision'], {'role_card_code': current['card_code']})
        self.delta(3); self.process({'personality': '甲的独立设定'})
        self.assertNotEqual(self.role()['card_code'], current['card_code'])
        self.assertEqual(self.store.role_card(current['card_code'])['card'], current['card'])

    def test_history_invalid_fields_and_limits_never_commit(self):
        self.configure(); self.ingest()
        before = self.preferences()
        denied = self.process({'personality': '历史修改'})
        self.assertFalse(self.last_envelope['role_card_edit_allowed'])
        self.assertEqual(denied['status'], 'failed')
        self.assertEqual(self.preferences()['memory'], before['memory'])
        self.assertEqual(len(self.store.role_cards()['items']), 1)
        self.delta(2)
        for value in ({'name': '改名'}, {'extensions': {}}, {'personality': 3}, {'tags': 'wrong'},
                      {'character_book': []}, {'description': '大' * 30000}, '{"personality":"a","personality":"b"}'):
            run = self.process(value)
            self.assertEqual(run['status'], 'failed', (value if isinstance(value, str) else list(value), run))
            self.assertEqual(self.preferences()['memory_revision'], 0)
            self.assertEqual(len(self.store.role_cards()['items']), 1)
            self.assertEqual(run['delivery']['total_parts'], 0)

    def test_manual_edits_switch_disable_and_memory_conflict(self):
        self.configure(); self.delta()
        other = self.create(roles.card('替代卡'))
        def manual_role():
            card = deepcopy(self.original['card']); card['data']['personality'] = '人工最新设定'
            self.store.save_role_card(card, self.original['card_code'], self.role()['revision'])
        actions = [manual_role,
                   lambda: self.update(self.preferences()['revision'], role_card_code=other['card_code']),
                   lambda: self.update(self.preferences()['revision'], memory='人工记忆'),
                   lambda: self.update(self.preferences()['revision'], ai_enabled=False)]
        for action in actions:
            run = self.process({'personality': '过期模型结果'}, callback=action)
            self.assertIn(run['status'], ('conflicted', 'cancelled'), run)
            self.assertEqual(run['delivery']['total_parts'], 0)
            self.assertEqual(len(self.store.role_cards()['items']), 2)
        self.assertEqual(self.preferences()['memory'], '人工记忆')
        self.assertEqual(self.store.role_card(self.original['card_code'])['card']['data']['personality'], '人工最新设定')

    def test_failed_memory_write_rolls_back_card_binding_and_queue(self):
        self.configure(); self.delta()
        events = []; self.store.notify = events.append
        self.store.call(lambda: self.store.db.execute("CREATE TRIGGER reject_memory BEFORE UPDATE OF memory ON conversation_preferences BEGIN SELECT RAISE(ABORT,'synthetic'); END"))
        run = self.process({'personality': '不得部分保存'})
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(len(self.store.role_cards()['items']), 1)
        self.assertIsNone(self.preferences()['role_card_code'])
        self.assertEqual(self.preferences()['memory_revision'], 0)
        self.assertEqual(run['delivery']['total_parts'], 0)
        self.assertFalse(any(e.get('resources') == ['role_cards'] for e in events))

    def test_full_migration_preserves_derived_binding_configuration_only_drops_ownership(self):
        self.configure(); self.delta(); self.process({'personality': '迁移后的角色'})
        derived = self.role()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; target = root/'full'; config = root/'config'
            source.mkdir(); target.mkdir(); config.mkdir()
            snapshot(self.path, source/'bridge.sqlite3')
            with closing(AuthStore(source/'auth.sqlite3')) as auth:
                auth.set_password('synthetic-password')
            export_data(source, root/'full.zip'); import_data(root/'full.zip', target)
            with closing(Store(target/'bridge.sqlite3')) as store:
                self.assertEqual(store.preferences(self.ids[0], self.cids[0])['role_card_code'], derived['card_code'])
                self.assertEqual(store.role_card(derived['card_code'])['card'], derived['card'])
                self.assertEqual(store.call(lambda: store.db.execute('SELECT count(*) FROM role_card_derivations WHERE active=1').fetchone()[0]), 1)
            export_configuration(source, root/'config.zip'); import_configuration(root/'config.zip', config)
            with closing(Store(config/'bridge.sqlite3')) as store:
                self.assertEqual(store.role_card(derived['card_code'])['card'], derived['card'])
                self.assertEqual(store.call(lambda: store.db.execute('SELECT count(*) FROM role_card_derivations').fetchone()[0]), 0)

    def test_deleted_account_preserves_card_but_drops_owner_and_deleted_source_is_optional(self):
        self.configure(); self.delta(); self.process({'personality': '独立内容'})
        saved = self.role()
        defaults = self.store.ai_defaults()
        self.store.save_ai_defaults(defaults['revision'], {'role_card_code': saved['card_code']})
        self.store.delete_role_card(self.original['card_code'], 1)
        self.assertEqual(self.role()['card'], saved['card'])
        self.store.set_state(self.ids[0], 'offline')
        account = self.store.account(self.ids[0])
        self.store.delete_account(self.ids[0], account['revision'])
        self.assertEqual(self.store.role_card(saved['card_code'])['card'], saved['card'])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM role_card_derivations').fetchone()[0]), 0)


class GroupEvolutionTests(roles.RoleTestCase):
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
    queued = priority.ReplyPriorityTests.queued
    perform = priority.ReplyPriorityTests.perform

    def test_silence_can_edit_and_suspended_history_rebases_after_live_edit(self):
        self.group(1); self.delta(1)
        with patch.object(self.provider, 'generate', return_value=dict(memory='群聊记忆', reply='', role_card_update='{"scenario":"群聊场景"}')):
            silent = self.start()
        self.assertEqual(silent['status'], 'completed', silent)
        self.assertTrue(silent['chose_silence'])
        self.assertEqual(silent['role_card_change']['action'], 'created')
        self.assertEqual(silent['delivery']['total_parts'], 0)
        self.update(self.preferences()['revision'], group_proactive_message_threshold=80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(2, 82): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            self.perform(background)
        self.mention(82)
        with patch.object(self.provider, 'generate', return_value=dict(memory='实时记忆', reply='当前回答', role_card_update='{"personality":"更熟悉的关系"}')):
            live = self.start()
        self.assertEqual(live['status'], 'completed', live)
        self.assertEqual(live['role_card_change']['action'], 'updated')
        suspended = self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])
        self.assertEqual(suspended['status'], 'suspended')
        self.assertEqual(suspended['role_revision'], 2)
        self.finish(live)
        with patch('backend.app.ai_retry_storage.time.time', return_value=(suspended.get('next_retry_at') or time.time()) + 1):
            resumed = self.start()
        self.assertEqual(resumed['run_id'], background['run_id'])
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertFalse(self.envelope()['role_card_edit_allowed'])
        self.assertEqual(self.envelope()['character_card']['data']['personality'], '更熟悉的关系')

    def test_first_derivation_rebases_suspended_role_identity(self):
        self.group(80)
        self.enterContext(patch('backend.app.ai_provider.INPUT_TEXT_LIMIT', 60000))
        for index in range(1, 81): self.delta(index, content={'text': 'x'*1000})
        background = self.queued()
        with patch.object(self.provider, 'generate', side_effect=ConversationError('ai_network_error', 'synthetic')):
            self.perform(background)
        self.mention(81)
        with patch.object(self.provider, 'generate', return_value=dict(memory='实时记忆', reply='当前回答', role_card_update='{"scenario":"新关系"}')):
            live = self.start()
        self.assertEqual(live['role_card_change']['action'], 'created')
        suspended = self.store.ai_run(self.ids[0], self.cids[0], background['run_id'])
        self.assertEqual(suspended['role_card_code'], live['role_card_change']['card_code'])
        self.finish(live)
        with patch('backend.app.ai_retry_storage.time.time', return_value=(suspended.get('next_retry_at') or time.time()) + 1):
            resumed = self.start()
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(self.envelope()['character_card']['data']['scenario'], '新关系')


class EvolutionProtocolTests(unittest.TestCase):
    def test_protocols_schema_legacy_new_and_permission(self):
        raw = dict(memory='真实记忆', reply='回复', role_card_update='{"personality":"温和"}')
        wrappers = {
            'openai_responses': lambda text: dict(status='completed', output=[dict(type='message', content=[dict(type='output_text', text=text)])]),
            'openai_chat_completions': lambda text: dict(choices=[dict(finish_reason='stop', message=dict(content=text))]),
            'anthropic_messages': lambda text: dict(stop_reason='end_turn', content=[dict(type='text', text=text)]),
            'gemini_generate_content': lambda text: dict(candidates=[dict(finishReason='STOP', content=dict(parts=[dict(text=text)]))]),
        }
        for protocol, wrap in wrappers.items():
            profile = dict(protocol=protocol, base_url='https://example.test/v1', model='synthetic', provider='custom', anthropic_version='2023-06-01')
            envelope = dict(instructions='old instructions', character_card=roles.card(), memory='', messages=[], role_card_edit_allowed=True)
            payload = wrap(json.dumps(raw))
            provider = Provider(httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
            for mode in ('schema', 'json', 'prompt'):
                self.assertIn('role_card_update', build_request(profile, 'synthetic', envelope, mode)[2].decode())
                self.assertEqual(provider.generate(profile, 'synthetic', envelope, mode), raw)
            envelope['role_card_edit_allowed'] = False
            with self.assertRaises(ConversationError): provider.generate(profile, 'synthetic', envelope, 'schema')
            old = dict(memory='旧协议', reply='旧回复')
            self.assertEqual(parse_response(protocol, wrap(json.dumps(old))), old)

    def test_complete_fields_extensions_size_and_long_names(self):
        original = roles.card()
        changes = {key: value for key, value in original['data'].items() if key in
                   ('description','personality','scenario','first_mes','mes_example','system_prompt','post_history_instructions','alternate_greetings','character_book','tags')}
        changes['personality'] = '新的性格'
        merged, fields = merge_role_patch(original, json.dumps(changes), True)
        self.assertEqual(fields, ['personality'])
        self.assertEqual(merged['data']['unknown_field'], original['data']['unknown_field'])
        self.assertEqual(merged['data']['character_book'], original['data']['character_book'])
        book = deepcopy(original['data']['character_book'])
        book['entries'][0]['content'] = '新的世界书内容'
        del book['extensions']; del book['entries'][0]['extensions']; del book['entries'][0]['unknown_entry']
        updated, fields = merge_role_patch(original, json.dumps({'character_book': book}), True)
        self.assertEqual(fields, ['character_book'])
        self.assertEqual(updated['data']['character_book']['entries'][0]['unknown_entry'], {'keep': 'yes'})
        for bad in ({'extensions': {'forged': True}}, {'unknown_metadata': None}):
            with self.assertRaises(ConversationError):
                merge_role_patch(original, json.dumps({'character_book': {**book, **bad}}), True)
        for initial, conversation in [('初'*200, '会'*300), ('初', '会'*300), ('初'*200, '会')]:
            name = derived_name(initial, conversation)
            self.assertEqual(len(name), 200)
            self.assertTrue(name.startswith('初') and name.endswith('会'))


class EvolutionMigrationTests(unittest.TestCase):
    def test_exact_default_upgrade_custom_preservation_backup_and_retry(self):
        for instructions in (V11_DEFAULT_INSTRUCTIONS, '我的自定义提示词'):
            with self.subTest(instructions=instructions[:12]), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'bridge.sqlite3'
                with closing(Store(path)) as store:
                    store.save_role_card(roles.card())
                with closing(sqlite3.connect(path)) as db:
                    remove_v12(db)
                    db.execute('PRAGMA user_version=11')
                    db.execute('UPDATE ai_settings SET instructions=?,revision=7', (instructions,))
                    db.commit()
                with patch('backend.app.role_evolution_schema.ROLE_EVOLUTION_SCHEMA', ROLE_EVOLUTION_SCHEMA + ('INVALID SQL',)):
                    with self.assertRaises(ConversationError): Store(path)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='role_card_derivations'").fetchone())
                    self.assertEqual(db.execute('SELECT instructions FROM ai_settings').fetchone()[0], instructions)
                with closing(Store(path)) as store:
                    settings = store.ai_settings()
                    expected = DEFAULT_INSTRUCTIONS if instructions == V11_DEFAULT_INSTRUCTIONS else instructions
                    self.assertEqual(settings['instructions'], expected)
                    self.assertEqual(settings['revision'], 8 if instructions == V11_DEFAULT_INSTRUCTIONS else 7)
                    self.assertEqual(len(store.role_cards()['items']), 1)
                self.assertEqual(validate_db(path, 'bridge.sqlite3'), 12)
                self.assertTrue(list((path.parent / 'migration-backups').glob('before-v12-*.sqlite3')))

    def test_configuration_export_import_keeps_card_without_owner_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir()
            with closing(Store(source/'bridge.sqlite3')) as store:
                original = store.save_role_card(roles.card('会话角色'))
            archive = root/'config.zip'
            export_configuration(source, archive)
            import_configuration(archive, target)
            with closing(Store(target/'bridge.sqlite3')) as store:
                self.assertEqual(store.role_card(original['card_code'])['card'], original['card'])
                self.assertEqual(store.call(lambda: store.db.execute('SELECT count(*) FROM role_card_derivations').fetchone()[0]), 0)
