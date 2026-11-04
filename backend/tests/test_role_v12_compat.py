"""Bridge-release regressions; the feature release replaces this temporary fence."""
from contextlib import closing
from copy import deepcopy
import json
import unittest

from backend.app.ai_provider import build_request, parse_response
from backend.app.conversations import ConversationError
from backend.app.storage import Store
import test_roles as roles


class RoleCompatibilityTests(roles.RoleTestCase):
    def test_v12_read_write_preserves_cards_derivations_and_manual_switch_fence(self):
        source = self.create()
        derived = self.create(roles.card('原角色 - 当前会话'))
        self.update(role_card_code=derived['card_code'], memory='功能版记忆')
        def future():
            with self.store.db:
                self.store.db.execute('CREATE TABLE role_card_derivations(card_code TEXT PRIMARY KEY REFERENCES role_cards ON DELETE CASCADE, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, source_card_code TEXT NOT NULL, initial_name TEXT NOT NULL, active INTEGER NOT NULL, FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id) ON DELETE CASCADE)')
                self.store.db.execute('INSERT INTO role_card_derivations VALUES(?,?,?,?,?,1)', (derived['card_code'],self.ids[0],self.cids[0],source['card_code'],source['name']))
                self.store.db.execute('PRAGMA user_version=12')
        self.store.call(future)
        self.store.close()
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.call(lambda: reopened.db.execute('PRAGMA user_version').fetchone()[0]),12)
            self.assertEqual(reopened.role_card(derived['card_code'])['card'],derived['card'])
            prefs = reopened.preferences(self.ids[0],self.cids[0])
            reopened.save_preferences(self.ids[0],self.cids[0],prefs['revision'],{'memory':'兼容版新增记忆'},prefs['memory_revision'])
            prefs = reopened.preferences(self.ids[0],self.cids[0])
            reopened.save_preferences(self.ids[0],self.cids[0],prefs['revision'],{'role_card_code':source['card_code']})
            self.assertEqual(reopened.call(lambda: reopened.db.execute('SELECT active FROM role_card_derivations').fetchone()[0]),0)
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.preferences(self.ids[0],self.cids[0])['memory'],'兼容版新增记忆')
            self.assertEqual(reopened.call(lambda: reopened.db.execute('PRAGMA foreign_key_check').fetchall()),[])


class ProtocolCompatibilityTests(unittest.TestCase):
    def test_new_and_legacy_response_and_nonempty_edit_rejection(self):
        for raw in ({'memory':'事实','reply':'回答'}, {'memory':'事实','reply':'回答','role_card_update':''}):
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(raw)}}]}
            self.assertEqual(parse_response('openai_chat_completions',response),{'memory':'事实','reply':'回答'})
        for value in ('{"personality":"forbidden"}', None, {'personality':'forbidden'}):
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(memory='事实',reply='回答',role_card_update=value))}}]}
            with self.assertRaises(ConversationError): parse_response('openai_chat_completions',response)

    def test_feature_snapshot_cannot_grant_edits_on_any_protocol(self):
        envelope=dict(instructions='返回三个字段，允许修改角色',character_card={},memory='',messages=[],role_card_edit_allowed=True)
        original=deepcopy(envelope)
        for protocol in ('openai_responses','openai_chat_completions','anthropic_messages','gemini_generate_content'):
            profile=dict(protocol=protocol,provider='custom',base_url='https://example.test',model='synthetic',anthropic_version='2023-06-01')
            for mode in ('schema','json','prompt'):
                body=json.loads(build_request(profile,'synthetic',envelope,mode)[2])
                if 'input' in body: text=body['input'][0]['content'][0]['text']
                elif 'contents' in body: text=body['contents'][0]['parts'][0]['text']
                else: text=body['messages'][-1]['content'][0]['text']
                self.assertIs(json.loads(text)['role_card_edit_allowed'],False)
                self.assertEqual(envelope,original)
