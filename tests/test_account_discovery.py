"""Synthetic own-account discovery. No real desktop, account or model access."""
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wechat_agent_bridge.adapters.account_discovery import OwnAccountDiscovery, profile_fields, window_in_live_use
from wechat_agent_bridge.adapters.windows import WindowsAdapter
from wechat_agent_bridge.adapters.own_account_windows import WindowsOwnAccountBackend
from wechat_agent_bridge.console_models import Account, ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore
from tests.test_console import SyntheticProtector


FIELDS = ['昵称：合成小鲸鱼', '微信号：wxid_synthetic_owner']
STAMP = (77, 101, 'synthetic-creation-time', 'synthetic/Weixin.exe', '4.1.15.56')


class FieldTests(unittest.TestCase):
    def test_nickname_and_labeled_id_are_separate(self):
        self.assertEqual(profile_fields(FIELDS), dict(name='合成小鲸鱼', wechat_id='wxid_synthetic_owner'))

    def test_unlabeled_nickname_immediately_above_id(self):
        self.assertEqual(profile_fields(['合成小鲸鱼', FIELDS[1], '地区：合成地区'])['name'], '合成小鲸鱼')

    def test_duplicate_ancestor_text_and_split_label(self):
        self.assertEqual(profile_fields(['合成小鲸鱼\n微信号：\nwxid_synthetic_owner', '合成小鲸鱼'])['name'], '合成小鲸鱼')

    def test_english_labels(self):
        self.assertEqual(profile_fields(['Nickname: Synthetic', 'WeChat ID: synthetic_owner'])['name'], 'Synthetic')

    def test_bare_id_or_nickname_cannot_become_id(self):
        for fields in (['wxid_synthetic_owner'], ['昵称：wxid_synthetic_owner'],
                       ['昵称：Synthetic', '备注 微信号：wxid_synthetic_owner']):
            with self.subTest(fields=fields), self.assertRaisesRegex(ConsoleError, 'id_missing_or_ambiguous'):
                profile_fields(fields)

    def test_conflicting_ids_rejected(self):
        with self.assertRaisesRegex(ConsoleError, 'id_missing_or_ambiguous'):
            profile_fields(FIELDS + ['微信号：different_owner'])

    def test_missing_or_multiple_names_never_fall_back_to_id(self):
        for fields in ([FIELDS[1]], ['A','B',FIELDS[1]], FIELDS + ['昵称：Another']):
            with self.subTest(fields=fields), self.assertRaisesRegex(ConsoleError, 'nickname_missing_or_ambiguous'):
                profile_fields(fields)

    def test_overlong_id_not_silently_truncated(self):
        with self.assertRaises(ConsoleError):
            profile_fields([FIELDS[0], '微信号：'+'a'*65])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.backend.windows.return_value = [101]
        self.backend.stamp.return_value = STAMP
        self.backend.own_profile.return_value = FIELDS
        self.clock = Mock(return_value=100)
        self.discovery = OwnAccountDiscovery(self.backend, self.clock)

    def test_no_grant_or_expected_id_needed(self):
        account = self.discovery.discover()[0]
        self.assertEqual(account['name'], '合成小鲸鱼')
        self.assertEqual(account['wechat_id'], 'wxid_synthetic_owner')
        self.assertTrue(account['identity_certain'])
        self.assertFalse(account['verified'])  # Does not promote client compatibility.
        self.backend.own_profile.assert_called_once_with(101)

    def test_repeated_refresh_does_not_reopen_card(self):
        self.assertEqual(self.discovery.discover(), self.discovery.discover())
        self.backend.own_profile.assert_called_once()

    def test_expired_cache_rechecks_same_process(self):
        self.discovery.discover()
        self.clock.return_value = 116
        self.discovery.discover()
        self.assertEqual(self.backend.own_profile.call_count, 2)

    def test_process_epoch_change_discards_identity(self):
        self.discovery.discover()
        self.backend.stamp.return_value = (*STAMP[:2], 'new-epoch', *STAMP[3:])
        self.backend.own_profile.side_effect = ConsoleError('account_avatar_not_exposed')
        account = self.discovery.discover()[0]
        self.assertFalse(account['identity_certain'])
        self.assertIsNone(account['wechat_id'])

    def test_instance_changed_during_read_not_published(self):
        self.backend.stamp.side_effect = [STAMP, (*STAMP[:2], 'new-epoch', *STAMP[3:])]
        self.assertFalse(self.discovery.discover()[0]['identity_certain'])

    def test_disconnect_drops_cached_account(self):
        self.discovery.discover()
        self.backend.windows.return_value = []
        self.assertEqual(self.discovery.discover(), [])
        self.assertEqual(self.discovery.cache, {})

    def test_exited_window_not_reported_connected(self):
        self.backend.stamp.side_effect = ConsoleError('account_discovery_instance_changed')
        self.assertEqual(self.discovery.discover(), [])

    def test_existing_contact_or_restore_failure_never_claims_identity(self):
        for reason in ('account_discovery_existing_profile', 'account_discovery_restore_failed'):
            self.discovery.cache.clear()
            self.backend.own_profile.side_effect = ConsoleError(reason)
            with self.subTest(reason=reason):
                account = self.discovery.discover()[0]
                self.assertFalse(account['identity_certain'])
                self.assertEqual(account['discovery_reason'], reason)

    def test_exception_does_not_expose_ui_text(self):
        self.backend.own_profile.side_effect = RuntimeError('synthetic private message')
        self.assertNotIn('private message', str(self.discovery.discover()))

    def test_same_nickname_does_not_merge_different_accounts(self):
        self.backend.windows.return_value = [101, 202]
        self.backend.stamp.side_effect = [STAMP, STAMP, (88,202,*STAMP[2:]), (88,202,*STAMP[2:])]
        self.backend.own_profile.side_effect = [FIELDS, [FIELDS[0], '微信号：another_owner']]
        result = self.discovery.discover()
        self.assertEqual(len({a['id'] for a in result}), 2)

    def test_service_discovery_does_not_change_grants_or_call_model(self):
        store = ConsoleStore(':memory:', SyntheticProtector())
        self.addCleanup(store.close)
        adapter = SimpleNamespace(discover=lambda: [Account(**a) for a in self.discovery.discover()])
        provider = Mock()
        service = ConsoleService(adapter, store, provider_factory=lambda _: provider)
        self.assertEqual(service.discover()[0]['name'], '合成小鲸鱼')
        self.assertFalse(service.grants)
        self.assertEqual(store.values('grant'), [])
        self.assertTrue(service.paused)
        provider.assert_not_called()
        self.assertEqual(provider.mock_calls, [])


class TransportTests(unittest.TestCase):
    def adapter(self, responses, polls=None):
        adapter = WindowsAdapter(auto_discovery=True)
        adapter.process = Mock()
        adapter.process.is_alive.return_value = True
        adapter.connection = Mock()
        adapter.connection.poll.side_effect = polls or [True]*len(responses)
        adapter.connection.recv.side_effect = responses
        adapter._stop = Mock()
        return adapter

    def test_timeout_restores_parent_held_receipt(self):
        receipt = {'synthetic':'restoration'}
        adapter = self.adapter([{'discovery_restore':receipt}], [True,False])
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window') as restore:
            with self.assertRaisesRegex(ConsoleError, 'windows_operation_timeout'):
                adapter.discover()
            restore.assert_called_once_with(receipt)
        adapter._stop.assert_called_once()

    def test_disconnected_worker_restores_receipt(self):
        receipt = {'synthetic':'restoration'}
        adapter = self.adapter([{'discovery_restore':receipt}, EOFError()])
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window') as restore:
            with self.assertRaisesRegex(ConsoleError, 'windows_worker_disconnected'):
                adapter.discover()
            restore.assert_called_once_with(receipt)

    def test_failed_parent_restore_rejects_success(self):
        adapter = self.adapter([{'discovery_restore':{}}, {'value':[]}])
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window', side_effect=RuntimeError()):
            with self.assertRaisesRegex(ConsoleError, 'account_discovery_restore_failed'):
                adapter.discover()

    def test_completed_worker_restore_not_repeated(self):
        adapter = self.adapter([{'discovery_restore':{}}, {'discovery_restore':None}, {'value':[]}])
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window') as restore:
            self.assertEqual(adapter.discover(), [])
            restore.assert_not_called()

    def test_legacy_acceptance_path_kept_separate(self):
        adapter = WindowsAdapter()
        adapter._call = Mock(return_value=[])
        adapter.discover()
        adapter._call.assert_called_once_with('discover')


class OwnCardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.backend = WindowsOwnAccountBackend.__new__(WindowsOwnAccountBackend)
        self.backend.stamp = Mock(return_value=STAMP)
        self.backend._popups = Mock(side_effect=[[], [202]])
        self.backend._fields = Mock(return_value=FIELDS)
        self.backend._avatar = Mock()
        self.backend.uia = SimpleNamespace(UIA_InvokePatternId=10000)
        self.backend.checkpoint = Mock()
        self.api = Mock()
        self.api.GetLastInputInfo.return_value = 100
        self.api.GetTickCount.return_value = 1000
        self.gui = Mock()
        self.gui.IsIconic.return_value = False
        self.modules = patch.dict(sys.modules, {
            'win32api':self.api, 'win32con':SimpleNamespace(SW_RESTORE=9), 'win32gui':self.gui,
            'comtypes.gen.UIAutomationClient':SimpleNamespace(IUIAutomationInvokePattern=object)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.restore = patch('wechat_agent_bridge.adapters.own_account_windows.restore_window').start()
        self.desktop_locked = patch('wechat_agent_bridge.adapters.background.desktop_locked',return_value=False).start()
        self.live_use = patch('wechat_agent_bridge.adapters.account_discovery.window_in_live_use',return_value=False).start()
        self.addCleanup(patch.stopall)

    def test_opens_only_self_avatar_reads_twice_and_restores(self):
        self.assertEqual(self.backend.own_profile(101), FIELDS)
        self.backend._avatar.assert_called_once_with(101)
        self.assertEqual(self.backend._fields.call_count, 2)
        self.restore.assert_called_once()
        self.assertEqual(self.restore.call_args.args[0]['popup'], 202)
        self.assertIsNone(self.backend.checkpoint.call_args.args[0])

    def test_existing_card_is_not_read_or_closed(self):
        self.backend._popups.side_effect = [[202]]
        with self.assertRaisesRegex(ConsoleError, 'existing_profile'):
            self.backend.own_profile(101)
        self.backend._fields.assert_not_called()
        self.backend._avatar.assert_not_called()
        self.restore.assert_not_called()

    def test_missing_avatar_does_not_click_container(self):
        self.backend._avatar.side_effect = ConsoleError('account_avatar_not_exposed')
        with self.assertRaisesRegex(ConsoleError, 'avatar_not_exposed'):
            self.backend.own_profile(101)
        self.backend._fields.assert_not_called()
        self.restore.assert_called_once()

    def test_restore_failure_prevents_returning_identity(self):
        self.restore.side_effect = ConsoleError('account_discovery_restore_failed')
        with self.assertRaisesRegex(ConsoleError, 'restore_failed'):
            self.backend.own_profile(101)

    def test_inconsistent_double_read_restores_and_rejects(self):
        self.backend._fields.side_effect = [FIELDS, [FIELDS[0], '微信号：another_owner']]
        with self.assertRaisesRegex(ConsoleError, 'instance_changed'):
            self.backend.own_profile(101)
        self.restore.assert_called_once()

    def test_locked_desktop_never_opens_profile(self):
        self.desktop_locked.return_value = True
        with self.assertRaisesRegex(ConsoleError, 'desktop_locked_or_unavailable'):
            self.backend.own_profile(101)
        self.backend._avatar.assert_not_called()
        self.restore.assert_not_called()

    def test_recent_human_input_does_not_move_window(self):
        self.api.GetTickCount.return_value = 101
        with self.assertRaisesRegex(ConsoleError, 'human_takeover'):
            self.backend.own_profile(101)
        self.gui.ShowWindow.assert_not_called()

    def test_live_service_window_is_not_opened_by_development(self):
        self.live_use.return_value = True
        with self.assertRaisesRegex(ConsoleError, 'client_busy'):
            self.backend.own_profile(101)
        self.backend._avatar.assert_not_called()
        self.restore.assert_not_called()


class LiveUseTests(unittest.TestCase):
    def test_only_exact_live_window_is_blocked_without_changing_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            protector = SyntheticProtector()
            store = ConsoleStore(directory/'gate.sqlite3',protector)
            pending = dict(fingerprint=dict(hwnd=101,pid=77),original=0,written=1)
            store.put('patch','pending',pending)
            self.assertTrue(window_in_live_use(101,directory=directory,protector=protector))
            self.assertFalse(window_in_live_use(202,directory=directory,protector=protector))
            self.assertEqual(store.get('patch','pending'),pending)
            store.close()

    def test_no_live_database_does_not_create_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertFalse(window_in_live_use(101,directory=directory))
            self.assertEqual(list(directory.iterdir()), [])

    def test_unreadable_ownership_does_not_allow_ui_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'gate.sqlite3').write_bytes(b'synthetic invalid database')
            self.assertTrue(window_in_live_use(101,directory=directory))


class DefaultModeTests(unittest.TestCase):
    def test_windows_defaults_to_real_account_discovery(self):
        from wechat_agent_bridge.console import default_adapter
        with patch('wechat_agent_bridge.console.os.name', 'nt'):
            self.assertEqual(default_adapter(), 'windows')

    def test_other_systems_keep_mock_default(self):
        from wechat_agent_bridge.console import default_adapter
        with patch('wechat_agent_bridge.console.os.name', 'posix'):
            self.assertEqual(default_adapter(), 'mock')


if __name__ == '__main__':
    unittest.main()
