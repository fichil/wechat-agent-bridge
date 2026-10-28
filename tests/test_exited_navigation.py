import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import recover_exited_navigation as recovery
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore


class ExitEvidenceTests(unittest.TestCase):
    fp = dict(pid=77, created=1000)

    def test_absence_needs_successful_complete_enumeration_too(self):
        proof = dict(pid=77, open_error=87, pid_enumeration_complete=True, pid_in_enumeration=False)
        self.assertEqual(recovery.require_gone(self.fp, proof), 'pid_absent')
        for change in ({'open_error': 5}, {'open_error': 6}, {'pid_enumeration_complete': False},
                       {'pid_in_enumeration': True}, {'created': 1000}, {'pid': 99}):
            with self.assertRaises(ConsoleError):
                recovery.require_gone(self.fp, {**proof, **change})

    def test_signaled_exact_instance_and_newer_pid_reuse_prove_original_gone(self):
        for current, wait, reason in ((1000, 0, 'terminated_handle'), (2000, 258, 'pid_reused'),
                                      (2000, 0, 'pid_reused')):
            self.assertEqual(recovery.require_gone(self.fp, dict(pid=77, created=current, wait_result=wait)), reason)

    def test_original_alive_wait_errors_old_creation_and_invalid_pid_fail(self):
        for current, wait in ((1000, 258), (1000, 0xffffffff), (999, 0), (1000, False)):
            with self.assertRaises(ConsoleError):
                recovery.require_gone(self.fp, dict(pid=77, created=current, wait_result=wait))
        for fp in (dict(pid=0, created=1000), dict(pid=True, created=1000), dict(pid=77, created=False)):
            with self.assertRaises(ConsoleError):
                recovery.require_gone(fp, dict(pid=fp['pid'], created=1000, wait_result=0))

    def test_default_cli_has_no_native_or_store_access(self):
        with patch.object(recovery, 'WindowsProcessObserver') as native, patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(recovery.main([]), 0)
            native.assert_not_called()
            self.assertIn('disabled', out.getvalue())


class ExitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = ConsoleStore(self.directory / 'gate.sqlite3', SyntheticProtector())
        self.addCleanup(self.store.close)
        self.lock = recovery.original.RunLock(self.directory / 'autofocus-draft.lock')
        self.lock.acquire()
        self.fp = dict(pid=77, hwnd=101, created=1000, version='synthetic', dll_sha256='synthetic',
                       module_base=4096, module_size=8192, flag_rva=10)
        self.binding = dict(fingerprint=self.fp, account=dict(wechat_id='synthetic_sender',
                            identity_source='uia_own_profile_exact_id_user_selected_window'))
        self.pending = dict(request='synthetic-select', fingerprint=self.fp, method='verified_private_SelectionItem_Select',
                            phase='terminal_unknown', lock_token=self.lock.token)
        self.attempt = dict(kind='development_private_semantic_selection', request_id='synthetic-select',
                            fingerprint=self.fp, state='unknown', worker_exited=True, patch_restored=True,
                            cleanup_errors=[], send_invoked=False, draft_written=False)
        self.store.put('binding', 'account', self.binding)
        self.store.put('development_navigation', 'pending', self.pending)
        self.store.put('development_navigation', 'synthetic-select', self.attempt)
        self.store.put('private_baselines', 'immutable', dict(upload_eligible=False, retained_rows=8))
        self.store.put('verified_contacts', 'same-name', dict(fingerprint=self.fp))
        self.store.put('quota', 'persistent', dict(api_used=10, send_used=3))
        self.proof = dict(pid=77, open_error=87, pid_enumeration_complete=True, pid_in_enumeration=False)
        self.observer = Mock()
        self.observer.inspect.side_effect = lambda pid: dict(self.proof)
        self.initial = self.rows()

    def rows(self):
        return self.store.db.execute('SELECT bucket,key,payload FROM records ORDER BY bucket,key').fetchall()

    def run_recovery(self, **kwargs):
        return recovery.recover(self.store, self.lock, 'synthetic_sender', self.observer, **kwargs)

    def assert_preserved(self):
        self.assertEqual(self.store.get('binding', 'account'), self.binding)
        self.assertEqual(self.store.get('development_navigation', 'synthetic-select'), self.attempt)
        for bucket, key, payload in self.initial:
            if bucket != 'development_navigation' or key != recovery.digest('pending'):
                self.assertEqual(self.store.db.execute('SELECT payload FROM records WHERE bucket=? AND key=?',
                                                      (bucket, key)).fetchone()[0], payload)

    def test_inspection_is_read_only_even_for_dead_process(self):
        report = self.run_recovery()
        self.assertEqual(report['state'], 'process_exit_inspected')
        self.assertEqual(self.rows(), self.initial)
        self.assertTrue(self.lock.path.exists())

    def test_recovery_records_exit_before_unlock_without_rewriting_unknown_binding_history_or_quotas(self):
        release = self.lock.release
        def checked_release():
            self.assertIsNotNone(self.store.get('development_navigation', 'synthetic-select:process-exit-observed'))
            self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)
            release()
        with patch.object(self.lock, 'release', side_effect=checked_release):
            report = self.run_recovery(apply=True)
        self.assertEqual(report['state'], 'exited_navigation_retired')
        self.assertFalse(self.lock.path.exists())
        self.assertIsNone(self.store.get('development_navigation', 'pending'))
        self.assertIsNotNone(self.store.get('development_navigation', 'synthetic-select:process-exit-retired'))
        self.assert_preserved()

    def test_access_denied_and_live_original_cannot_change_state(self):
        for proof in (dict(pid=77, open_error=5), dict(pid=77, created=1000, wait_result=258)):
            self.proof = proof
            with self.assertRaises(ConsoleError):
                self.run_recovery(apply=True)
            self.assertEqual(self.rows(), self.initial)
            self.assertTrue(self.lock.path.exists())

    def test_wrong_account_foreign_lock_and_other_pending_reject_before_process_inspection(self):
        with self.assertRaises(ConsoleError):
            recovery.recover(self.store, self.lock, 'another_sender', self.observer, apply=True)
        self.observer.inspect.assert_not_called()
        self.lock.path.write_text('b' * 32, encoding='ascii')
        with self.assertRaises(ConsoleError):
            self.run_recovery(apply=True)
        self.observer.inspect.assert_not_called()
        self.lock.path.write_text(self.pending['lock_token'], encoding='ascii')
        self.store.put('thread_hook', 'pending', {'active': True})
        with self.assertRaisesRegex(ConsoleError, 'other_recovery_pending'):
            self.run_recovery(apply=True)
        self.observer.inspect.assert_not_called()

    def test_missing_original_lock_without_durable_exit_receipt_is_not_auto_takeover(self):
        self.lock.release()
        with self.assertRaisesRegex(ConsoleError, 'lock_missing_without_receipt'):
            self.run_recovery(apply=True)
        self.observer.inspect.assert_not_called()
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)

    def test_binding_or_unknown_changes_during_observation_stop_recovery(self):
        for bucket, key, value in (('binding', 'account', {'fingerprint': {}}),
                                   ('development_navigation', 'synthetic-select', {'state': 'changed'})):
            self.store.put('binding', 'account', self.binding)
            self.store.put('development_navigation', 'synthetic-select', self.attempt)
            def inspect(pid):
                self.store.put(bucket, key, value)
                return dict(self.proof)
            self.observer.inspect.side_effect = inspect
            with self.assertRaisesRegex(ConsoleError, 'original_state_changed'):
                self.run_recovery(apply=True)
            self.assertTrue(self.lock.path.exists())
            self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)

    def test_failed_observation_persistence_never_releases_original_lock(self):
        with patch.object(self.store, 'put', side_effect=OSError('synthetic_disk_failure')):
            with self.assertRaises(OSError):
                self.run_recovery(apply=True)
        self.assertEqual(self.rows(), self.initial)
        self.assertTrue(self.lock.path.exists())

    def test_later_process_observation_failure_never_unlinks(self):
        for observation in (2, 3):
            proofs = [dict(self.proof) for _ in range(3)]
            proofs[observation - 1] = dict(pid=77, open_error=5)
            self.observer.inspect.side_effect = proofs
            with self.assertRaises(ConsoleError):
                self.run_recovery(apply=True)
            self.assertTrue(self.lock.path.exists())
            self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)

    def test_sqlite_abort_after_unlock_rolls_back_retirement_and_is_resumable(self):
        key = recovery.digest('pending')
        self.store.db.execute("CREATE TRIGGER synthetic_commit_failure BEFORE INSERT ON records "
                              "WHEN NEW.bucket='development_navigation' AND NEW.key='" + key + "' "
                              "BEGIN SELECT RAISE(ABORT, 'synthetic_disk_error'); END")
        self.store.db.commit()
        with self.assertRaisesRegex(Exception, 'synthetic_disk_error'):
            self.run_recovery(apply=True)
        self.assertFalse(self.lock.path.exists())
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)
        self.assertIsNone(self.store.get('development_navigation', 'synthetic-select:process-exit-retired'))
        self.assertIsNotNone(self.store.get('development_navigation', 'synthetic-select:process-exit-observed'))
        self.store.db.execute('DROP TRIGGER synthetic_commit_failure')
        self.store.db.commit()
        self.observer.inspect.side_effect = lambda pid: dict(self.proof)
        self.assertEqual(self.run_recovery(apply=True)['state'], 'exited_navigation_retired')
        self.assert_preserved()

    def test_release_failure_is_resumable_and_keeps_original_unknown(self):
        with patch.object(self.lock, 'release', side_effect=OSError('synthetic_unlink_failure')):
            with self.assertRaises(OSError):
                self.run_recovery(apply=True)
        self.assertTrue(self.lock.path.exists())
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)
        self.assertEqual(self.run_recovery(apply=True)['state'], 'exited_navigation_retired')
        self.assert_preserved()

    def test_interruption_after_unlink_keeps_pending_and_can_resume_from_persisted_proof(self):
        def stop_after_unlink():
            if not self.lock.path.exists():
                raise ConsoleError('synthetic_interruption')
        with self.assertRaisesRegex(ConsoleError, 'synthetic_interruption'):
            self.run_recovery(apply=True, checkpoint=stop_after_unlink)
        self.assertFalse(self.lock.path.exists())
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)
        self.assertEqual(self.run_recovery(apply=True)['state'], 'exited_navigation_retired')
        self.assert_preserved()

    def test_foreign_lock_after_exit_receipt_is_not_removed(self):
        with patch.object(self.lock, 'release', side_effect=OSError('synthetic_failure')):
            with self.assertRaises(OSError):
                self.run_recovery(apply=True)
        self.lock.path.write_text('b' * 32, encoding='ascii')
        with self.assertRaisesRegex(ConsoleError, 'original_lock_changed'):
            self.run_recovery(apply=True)
        self.assertEqual(self.lock.path.read_text(encoding='ascii'), 'b' * 32)
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)

    def test_success_cannot_reopen_or_repeat_old_selection(self):
        self.run_recovery(apply=True)
        before = self.rows()
        with self.assertRaisesRegex(ConsoleError, 'terminal_pending_required'):
            self.run_recovery(apply=True)
        self.assertEqual(self.rows(), before)
        self.assert_preserved()

    def test_mismatched_durable_exit_receipt_keeps_pending_and_lock(self):
        self.store.put('development_navigation', 'synthetic-select:process-exit-observed', {'fingerprint': {}})
        with self.assertRaisesRegex(ConsoleError, 'existing_observation_mismatch'):
            self.run_recovery(apply=True)
        self.assertTrue(self.lock.path.exists())
        self.assertEqual(self.store.get('development_navigation', 'pending'), self.pending)


if __name__ == '__main__':
    unittest.main()
