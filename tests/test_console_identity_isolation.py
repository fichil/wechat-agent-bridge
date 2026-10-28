from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
from wechat_agent_bridge.adapters.windows import WindowsAdapter
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint
from wechat_agent_bridge.console_models import Account,ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.adapters.background import DesktopSnapshot


class SyntheticProtector:
    def protect(self,value):return b'synthetic-only:'+value
    def unprotect(self,value):
        if not value.startswith(b'synthetic-only:'):raise ValueError('synthetic_format')
        return value[len(b'synthetic-only:'):]


class ConsoleIdentityIsolationTests(unittest.TestCase):
    def test_rebinding_same_process_reuses_native_enrollment_without_reopening_card(self):
        self.adapter.allow_accessibility_patch=True
        self.adapter.gate=None
        self.store.put('patch','pending',None)
        saved=asdict(replace(self.account,fingerprint=asdict(self.fp)))
        self.store.put('console_identity','7:101',saved)
        native=self.adapter.native_gate
        with patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate',return_value=native):
            self.adapter.bind_accessibility('7:101','synthetic_owner')
        self.assertEqual(asdict(self.adapter.enrolled_account),saved)
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertEqual(self.adapter._call.call_args.args,('bind_window',7,101))

    def test_cached_identity_from_previous_process_lifetime_is_not_reused(self):
        self.adapter.allow_accessibility_patch=True
        self.adapter.gate=None
        self.store.put('patch','pending',None)
        self.store.put('console_identity','7:101',asdict(replace(self.account,fingerprint=asdict(replace(self.fp,created=122)))))
        native=self.adapter.native_gate
        with patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate',return_value=native):
            self.adapter.bind_accessibility('7:101','synthetic_owner')
        self.assertIsNone(self.adapter.enrolled_account)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ConsoleStore(Path(self.temp.name)/'gate.sqlite3',SyntheticProtector())
        self.addCleanup(self.store.close)
        self.old=dict(account=dict(wechat_id='old_exact'),fingerprint=dict(pid=99),unknown='preserved')
        self.store.put('binding','account',self.old)
        self.fp=Fingerprint(7,101,123,'4.1.15.56','synthetic',1000,2000,10)
        self.account=Account('a','display','synthetic_owner','7:101','4.1.15.56',
                             identity_source='uia_own_profile_exact_id_user_selected_window')
        self.adapter=WindowsAdapter(gate_store=self.store)
        self.adapter.gate=SimpleNamespace(state='enabled')
        self.adapter.gate.record=dict(fingerprint=asdict(self.fp),original=0,written=1)
        self.store.put('patch','pending',self.adapter.gate.record)
        self.adapter.native_gate=Mock();self.adapter.native_gate.fingerprint.return_value=self.fp
        self.adapter.bound_window=dict(fingerprint=self.fp,expected_wechat_id='synthetic_owner',identity_source='pending')
        self.adapter._call=Mock(return_value=asdict(self.account))
    def test_console_probe_preserves_acceptance_epoch_and_stores_separate_identity(self):
        result=self.adapter.probe_account('7:101')
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertEqual(self.store.get('console_identity','7:101'),asdict(result))
        self.assertEqual(result.fingerprint,asdict(self.fp))
    def test_wrong_identity_source_instance_account_or_certainty_does_not_write(self):
        for changes in (dict(wechat_id='another'),dict(instance_id='8:101'),
                        dict(identity_source='nickname'),dict(identity_certain=False)):
            self.adapter._call.return_value=asdict(replace(self.account,**changes))
            with self.subTest(changes=changes),self.assertRaises(ConsoleError):
                self.adapter.probe_account('7:101')
            self.assertIsNone(self.store.get('console_identity','7:101'))
            self.assertEqual(self.store.get('binding','account'),self.old)
            self.assertEqual(self.adapter.bound_window['identity_source'],'pending')
    def test_process_changes_during_profile_call_rejects_response(self):
        self.adapter.native_gate.fingerprint.side_effect=[self.fp,replace(self.fp,created=124)]
        with self.assertRaises(ConsoleError):self.adapter.probe_account('7:101')
        self.assertIsNone(self.store.get('console_identity','7:101'))
        self.assertEqual(self.store.get('binding','account'),self.old)
    def test_storage_failure_does_not_claim_bound_identity(self):
        with patch.object(self.store,'put',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):self.adapter.probe_account('7:101')
        self.assertEqual(self.adapter.bound_window['identity_source'],'pending')
        self.assertEqual(self.store.get('binding','account'),self.old)
    def test_failed_profile_never_writes_identity(self):
        self.adapter._call.side_effect=ConsoleError('profile_missing')
        with self.assertRaises(ConsoleError):self.adapter.probe_account('7:101')
        self.assertIsNone(self.store.get('console_identity','7:101'))
        self.assertEqual(self.store.get('binding','account'),self.old)

    def test_wait_probe_reads_twice_and_never_persists_before_restore(self):
        with patch('wechat_agent_bridge.adapters.background.snapshot',return_value=DesktopSnapshot(1,2,3,True)),\
                patch('wechat_agent_bridge.adapters.background.input_timestamp',return_value=10):
            result=self.adapter.observe_identity_probe('7:101')
        self.assertEqual(self.adapter._call.call_count,2)
        self.assertTrue(all(c.args[0]=='read_own_profile' for c in self.adapter._call.call_args_list))
        self.assertEqual(result.fingerprint,asdict(self.fp))
        self.assertIsNone(self.store.get('console_identity','7:101'))
        self.assertEqual(self.store.get('binding','account'),self.old)

    def test_wait_probe_discards_changed_card_input_or_clipboard(self):
        for cause in ('card','input','clipboard'):
            self.adapter._call.side_effect=([asdict(self.account),asdict(replace(self.account,name='changed'))]
                                            if cause=='card' else None)
            snapshots=[DesktopSnapshot(1,2,3,True),DesktopSnapshot(1,2,4 if cause=='clipboard' else 3,True)]
            with patch('wechat_agent_bridge.adapters.background.snapshot',side_effect=snapshots),\
                    patch('wechat_agent_bridge.adapters.background.input_timestamp',side_effect=[10,11 if cause=='input' else 10]):
                with self.subTest(cause=cause),self.assertRaisesRegex(ConsoleError,'observation_changed'):
                    self.adapter.observe_identity_probe('7:101')
            self.assertIsNone(self.store.get('console_identity','7:101'))

    def test_wait_probe_commit_requires_restored_gate_and_keeps_acceptance_binding(self):
        account=replace(self.account,fingerprint=asdict(self.fp))
        with patch('wechat_agent_bridge.adapters.background.snapshot',return_value=DesktopSnapshot(1,2,3,False)):
            with self.assertRaisesRegex(ConsoleError,'restoration_required'):
                self.adapter.commit_identity_probe(account)
            self.adapter.gate.record=None;self.adapter.gate.state='restored'
            self.store.put('patch','pending',None)
            self.adapter.commit_identity_probe(account)
        self.assertEqual(self.store.get('console_identity','7:101'),asdict(account))
        self.assertEqual(self.store.get('binding','account'),self.old)

    def test_probe_rejects_other_pending_operation_or_patch_owner(self):
        self.store.put('account_rebind','pending',dict(other=True))
        with self.assertRaisesRegex(ConsoleError,'prior_recovery'):
            self.adapter.prepare_identity_probe('7:101')
        self.store.put('account_rebind','pending',None)
        self.store.put('patch','pending',dict(other=True))
        with self.assertRaisesRegex(ConsoleError,'owner_mismatch'):
            self.adapter.prepare_identity_probe('7:101')
        with patch.object(self.adapter,'restore_accessibility') as restore:
            with self.assertRaisesRegex(ConsoleError,'owner_mismatch'):
                self.adapter.restore_identity_probe()
            restore.assert_not_called()
        self.assertEqual(self.store.get('patch','pending'),dict(other=True))

    def test_only_committed_current_epoch_enrollment_replaces_discovery_candidate(self):
        candidate=Account('candidate','window',instance_id='7:101',identity_certain=False)
        self.adapter._call.return_value=[asdict(candidate)]
        self.assertFalse(self.adapter.discover()[0].identity_certain)
        self.adapter.enrolled_account=replace(self.account,fingerprint=asdict(self.fp))
        self.assertEqual(self.adapter.discover()[0].wechat_id,'synthetic_owner')
        self.adapter.native_gate.fingerprint.return_value=replace(self.fp,created=124)
        self.assertFalse(self.adapter.discover()[0].identity_certain)
        self.assertIsNone(self.adapter.enrolled_account)

    def test_stale_backend_account_cannot_escape_cancelled_or_pending_enrollment(self):
        self.adapter._call.return_value=[asdict(self.account)]
        discovered=self.adapter.discover()[0]
        self.assertFalse(discovered.identity_certain)
        self.assertIsNone(discovered.wechat_id)
        self.adapter.enrolled_account=replace(self.account,fingerprint=asdict(self.fp))
        self.adapter.prepare_identity_probe('7:101')
        self.assertIsNone(self.adapter.enrolled_account)
        self.assertFalse(self.adapter.discover()[0].identity_certain)


if __name__=='__main__':unittest.main()
