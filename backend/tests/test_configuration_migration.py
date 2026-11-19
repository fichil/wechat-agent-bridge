from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.app.storage import Store
from backend.app.services import browser_values
from test_roles import card
from test_conversations import accounts
from bridge_portable.configuration import export_configuration, read_configuration
from bridge_portable.migrate import import_data, sha


class ConfigurationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.target = self.root/'source', self.root/'新账号 目标'
        self.source.mkdir(); self.target.mkdir()
        self.package = self.root/'roles-and-api.zip'
        with closing(Store(self.source/'bridge.sqlite3')) as store:
            store.save_accounts(browser_values(accounts()))
            self.role = store.save_role_card(card())
            with patch('backend.app.secret_protection.protect',return_value=b'private-ciphertext-marker'):
                self.api = store.save_api_profile(dict(name='合成 API',provider='custom',protocol='openai_responses',
                     base_url='https://example.test/v1',model='test-model',anthropic_version='2026-10-21',workspace_id=''),key='private-key-marker')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_configuration_is_exported_and_new_account_starts_empty(self):
        export_configuration(self.source,self.package)
        with zipfile.ZipFile(self.package) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json','configuration.json'})
            raw = archive.read('configuration.json')
            for forbidden in (b'private-ciphertext-marker',b'private-key-marker',b'wxid_synthetic',b'password_hash'):
                self.assertNotIn(forbidden,raw)
        source_hash = sha(self.source/'bridge.sqlite3')
        result = import_data(self.package,self.target)
        self.assertEqual((result['roles'],result['profiles']), (1,1))
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            self.assertEqual(store.accounts(), [])
            self.assertEqual(store.role_card(self.role['card_code'])['card'],card())
            profile = store.api_profile(self.api['profile_id'])
            self.assertEqual(profile['base_url'],'https://example.test/v1')
            self.assertFalse(profile['has_key']); self.assertFalse(profile['key_readable'])
        self.assertEqual(sha(self.source/'bridge.sqlite3'),source_hash)
        self.assertFalse((self.target/'auth.sqlite3').exists())
        self.assertFalse((self.target/'migration-pending.json').exists())

    def test_existing_accounts_and_admin_preserved_and_failed_replace_rolls_back(self):
        export_configuration(self.source,self.package)
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            store.save_accounts(browser_values(accounts()))
        (self.target/'auth.sqlite3').write_bytes(b'unchanged administrator')
        before = sha(self.target/'bridge.sqlite3')
        import os
        replace = os.replace
        def fail_install(source,dest):
            if Path(source).parent.name.startswith('config-import-'):
                raise OSError('synthetic install failure')
            return replace(source,dest)
        with patch('bridge_portable.configuration.os.replace',side_effect=fail_install):
            with self.assertRaises(OSError): import_data(self.package,self.target)
        self.assertEqual(sha(self.target/'bridge.sqlite3'),before)
        import_data(self.package,self.target)
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            self.assertEqual(len(store.accounts()),2)
        self.assertEqual((self.target/'auth.sqlite3').read_bytes(),b'unchanged administrator')
        before = sha(self.target/'bridge.sqlite3')
        with self.assertRaises(ValueError): import_data(self.package,self.target)
        self.assertEqual(sha(self.target/'bridge.sqlite3'),before)

    def test_corrupt_payload_is_rejected_before_target_write(self):
        export_configuration(self.source,self.package)
        with zipfile.ZipFile(self.package) as source:
            manifest = source.read('manifest.json')
        bad = self.root/'corrupt.zip'
        with zipfile.ZipFile(bad,'w') as dest:
            dest.writestr('manifest.json',manifest)
            dest.writestr('configuration.json','{}')
        with self.assertRaises(ValueError): import_data(bad,self.target)
        self.assertFalse((self.target/'bridge.sqlite3').exists())

    def test_voice_settings_and_bindings_follow_remapped_profiles(self):
        with closing(Store(self.source/'bridge.sqlite3')) as store:
            role = store.role_card(self.role['card_code'])
            role['card']['data']['extensions']['wechat_agent_bridge'] = {
                'voices':{self.api['profile_id']:'nova'}, 'voice_instructions':{self.api['profile_id']:'自然'},
                'voice_service':{'service_id':'bailian','voice':'longhuhu_v3','instructions':'你正在进行自由对话。'}}
            store.save_voice_service(0,'workspace',b'service-private-ciphertext',dict(status='supported'))
            store.save_role_card(role['card'],role['card_code'],role['revision'])
            store.call(lambda:store.db.execute('UPDATE speech_profiles SET model=? WHERE profile_id=?',('speech-model',self.api['profile_id'])))
            store.call(store.db.commit)
        export_configuration(self.source,self.package)
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            fields={k:self.api[k] for k in ('name','provider','protocol','base_url','model','anthropic_version','workspace_id')}
            with patch('backend.app.secret_protection.protect',return_value=b''):
                existing=store.save_api_profile(fields,key='test')
            def collide():
                with store.db:
                    store.db.execute('DELETE FROM speech_profiles')
                    store.db.execute('UPDATE ai_defaults SET api_profile_id=NULL')
                    store.db.execute('UPDATE api_profiles SET profile_id=?',(self.api['profile_id'],))
            store.call(collide)
        import_data(self.package,self.target)
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            imported=next(p for p in store.api_profiles()['items'] if p['profile_id']!=self.api['profile_id'])
            self.assertEqual(imported['speech_model'],'speech-model')
            self.assertFalse(imported['has_key'])
            bindings=store.role_card(self.role['card_code'])['card']['data']['extensions']['wechat_agent_bridge']['voices']
            self.assertEqual(bindings,{imported['profile_id']:'nova'})
            extension=store.role_card(self.role['card_code'])['card']['data']['extensions']['wechat_agent_bridge']
            self.assertEqual(extension['voice_instructions'],{imported['profile_id']:'自然'})
            self.assertEqual(extension['voice_service']['voice'],'longhuhu_v3')
            self.assertFalse(store.voice_service()['has_key'])
        with zipfile.ZipFile(self.package) as archive:
            self.assertNotIn(b'service-private-ciphertext',archive.read('configuration.json'))

    def test_null_voice_extension_roundtrips_without_a_binding(self):
        with closing(Store(self.source/'bridge.sqlite3')) as store:
            role=store.role_card(self.role['card_code'])
            role['card']['data']['extensions']['wechat_agent_bridge']=None
            store.save_role_card(role['card'],role['card_code'],role['revision'])
        export_configuration(self.source,self.package)
        import_data(self.package,self.target)
        with closing(Store(self.target/'bridge.sqlite3')) as store:
            self.assertIsNone(store.role_card(self.role['card_code'])['card']['data']['extensions']['wechat_agent_bridge'])
