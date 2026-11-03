"""Concurrent recovery uses bounded barriers, never a real WeChat or SMTP account."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from unittest.mock import Mock, patch

from test_roles import RoleTestCase
from test_conversations import accounts
from backend.app.services import browser_values
from wechat_compatibility import registry


class AccountRecoveryTests(RoleTestCase):
    def prepare(self):
        self.bridge = self.app.state.bridge
        manager = self.bridge.compatibility
        manager.enabled = True
        self.store.compatibility_start()
        self.inventory = browser_values(accounts())
        profile = registry()['profiles'][0]
        for account in self.inventory['accounts']:
            for item in account['instances']:
                item.update(core_sha256=profile['builds'][0]['sha256'], profile_id=profile['id'])
        self.inventory['client_instances'] = [deepcopy(i) for a in self.inventory['accounts'] for i in a['instances']]
        self.bridge.discovery.probe = lambda: deepcopy(self.inventory)
        self.bridge.discovery.scan()
        for aid in self.ids:
            self.store.save_delta(aid, dict(groups=[dict(shard='@monitor', table_name='', generation='',
                last_id=0, signature=[], items=[])], issues=[]))
        manager.sender = Mock()
        manager.sender.capability.return_value = dict(ready=True, code='ready')
        return manager

    def test_native_inspection_does_not_block_scan_or_account_apis(self):
        manager = self.prepare()
        entered, release = threading.Event(), threading.Event()
        def inspect(*args, **kwargs):
            entered.set()
            release.wait(5)
            return dict(ready=True, code='ready')
        manager.sender.capability.side_effect = inspect
        with patch.object(manager, 'check_processes', return_value=True), ThreadPoolExecutor(3) as pool:
            checking = pool.submit(manager.reconcile)
            try:
                self.assertTrue(entered.wait(2))
                scan = pool.submit(self.bridge.discovery.scan)
                scan.result(timeout=2)
                query = pool.submit(lambda: self.post('accounts/query'))
                self.assertEqual(len(query.result(timeout=2).json()['accounts']), 2)
                query = pool.submit(lambda: self.post('compatibility/query'))
                self.assertEqual(query.result(timeout=2).status_code, 200)
            finally:
                release.set()
            checking.result(timeout=2)

    def test_relogin_during_inspection_discards_old_ready_result(self):
        manager = self.prepare()
        aid = self.ids[0]
        def inspect(account_id, **kwargs):
            if account_id == aid:
                self.inventory['accounts'][0]['instances'][0]['created'] = '134000000000000999'
                self.inventory['client_instances'][0]['created'] = '134000000000000999'
                self.bridge.discovery.scan()
            return dict(ready=True, code='ready')
        manager.sender.capability.side_effect = inspect
        with patch.object(manager, 'check_processes', return_value=True):
            manager.reconcile()
            row = next(r for r in self.store.compatibility_rows() if r['scope'] == aid)
            self.assertTrue(row['held'])
            self.assertNotEqual(json.loads(row['snapshot']).get('send', {}).get('ready'), True)
            manager.sender.capability.side_effect = None
            manager.reconcile()
            self.assertFalse(self.store.compatibility_blocked(aid))

    def test_deleted_account_cannot_be_recreated_by_old_scan_or_failure(self):
        self.prepare()
        for failure in (False, True):
            with self.subTest(failure=failure):
                entered, release = threading.Event(), threading.Event()
                def probe():
                    entered.set(); release.wait(3)
                    if failure: raise RuntimeError('synthetic probe error')
                    return deepcopy(self.inventory)
                self.bridge.discovery.probe = probe
                with ThreadPoolExecutor(1) as pool:
                    task = pool.submit(self.bridge.discovery.scan)
                    try:
                        self.assertTrue(entered.wait(2))
                        aid = self.store.accounts()[0]['account_id']
                        self.store.set_state(aid, 'offline')
                        self.bridge.delete_account(aid, self.store.account(aid)['revision'])
                    finally:
                        release.set()
                    self.assertEqual(task.result(timeout=2)['error'], 'account_scan_obsolete')
                self.assertNotIn(aid, [a['account_id'] for a in self.store.accounts()])
