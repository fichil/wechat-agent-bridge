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
