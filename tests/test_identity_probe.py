import unittest
from unittest.mock import Mock
from wechat_agent_bridge.identity_probe import IdentityProbe
from wechat_agent_bridge.console_models import ConsoleError


class IdentityProbeTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.events=[]
        self.observe=Mock(return_value='synthetic-account')
        self.restore=Mock(side_effect=lambda:self.events.append('restore'))
        self.commit=Mock(side_effect=lambda account:self.events.append('commit'))
        self.check=Mock()
        self.task=IdentityProbe('7:101',self.observe,self.restore,self.commit,self.check,
                                timeout=1,clock=lambda:self.now,wait=self.wait)
    def wait(self,seconds):self.now+=seconds
    def test_restore_precedes_commit_and_no_automatic_running(self):
        result=self.task.run()
        self.assertEqual(self.events,['restore','commit'])
        self.assertEqual(result['state'],'verified')
        self.assertTrue(result['identity_saved'])
    def test_only_missing_card_is_retried(self):
        self.observe.side_effect=[ConsoleError('account_identity_uncertain_open_own_profile'),'synthetic-account']
        self.assertEqual(self.task.run()['state'],'verified')
        self.assertEqual(self.observe.call_count,2)
    def test_timeout_restores_without_commit(self):
        self.observe.side_effect=ConsoleError('account_identity_uncertain_open_own_profile')
        result=self.task.run()
        self.assertEqual(result['reason'],'identity_probe_timeout')
        self.restore.assert_called_once()
        self.commit.assert_not_called()
    def test_wrong_id_or_transport_fault_never_retries(self):
        for error in ('account_profile_id_mismatch','windows_operation_timeout','identity_probe_observation_changed'):
            self.setUp()
            self.observe.side_effect=ConsoleError(error)
            self.assertEqual(self.task.run()['reason'],error)
            self.observe.assert_called_once()
            self.commit.assert_not_called()
    def test_cancel_before_observation_restores(self):
        self.task.cancel()
        self.assertEqual(self.task.run()['state'],'cancelled')
        self.observe.assert_not_called()
        self.commit.assert_not_called()
        self.restore.assert_called_once()
    def test_cancel_after_observation_discards_result(self):
        def observe():self.task.cancel();return 'synthetic-account'
        self.observe.side_effect=observe
        self.assertEqual(self.task.run()['state'],'cancelled')
        self.commit.assert_not_called()
    def test_cancel_during_restore_blocks_commit(self):
        self.restore.side_effect=self.task.cancel
        self.assertEqual(self.task.run()['state'],'cancelled')
        self.commit.assert_not_called()
    def test_restore_failure_is_not_success_even_with_valid_identity(self):
        self.restore.side_effect=OSError('synthetic')
        result=self.task.run()
        self.assertEqual(result['reason'],'identity_probe_restore_failed')
        self.assertFalse(result['restored'])
        self.commit.assert_not_called()
    def test_process_lock_or_stop_check_failure_restores(self):
        self.check.side_effect=ConsoleError('desktop_locked_or_unavailable')
        self.assertEqual(self.task.run()['reason'],'desktop_locked_or_unavailable')
        self.observe.assert_not_called()
        self.commit.assert_not_called()
    def test_commit_fault_does_not_mark_identity_saved(self):
        self.commit.side_effect=OSError('synthetic')
        result=self.task.run()
        self.assertEqual(result['reason'],'identity_probe_commit_failed')
        self.assertFalse(result['identity_saved'])
    def test_invalid_timeout_is_rejected(self):
        for timeout in (0,61,True):
            with self.assertRaises(ConsoleError):
                IdentityProbe('7:101',self.observe,self.restore,self.commit,self.check,timeout=timeout)


if __name__=='__main__':unittest.main()
