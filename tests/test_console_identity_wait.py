from types import SimpleNamespace
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_models import Account,ConsoleError,Grant


class ConsoleIdentityWaitTests(unittest.TestCase):
    def setUp(self):
        self.store=ConsoleStore(':memory:',SyntheticProtector())
        self.adapter=Mock(spec=['simulation_only','prepare_identity_probe','observe_identity_probe',
            'restore_identity_probe','commit_identity_probe','check_identity_probe','close'])
        self.adapter.simulation_only=False
        self.adapter.observe_identity_probe.return_value=Account('a','synthetic','synthetic_owner','7:101')
        self.service=ConsoleService(self.adapter,self.store,acceptance=SimpleNamespace(require=lambda *a,**k:None))
        self.service.accounts_cache={'candidate':Account('candidate','window',instance_id='7:101',identity_certain=False)}
        self.addCleanup(self.cleanup)
        self.release=None
    def cleanup(self):
        if self.release:self.release.set()
        self.service.close();self.store.close()
    def start(self):return self.service.identity_probe('start',dict(instance_id='7:101'))
    def finish(self):
        task=self.service.identity_probe_task;task.thread.join(2)
        self.assertFalse(task.thread.is_alive())
        return task.status()
    def block_observe(self):
        entered=threading.Event();self.release=threading.Event()
        def observe(*args):
            entered.set()
            if not self.release.wait(2):raise ConsoleError('synthetic_wait_timeout')
            return Account('a','synthetic','synthetic_owner','7:101')
        self.adapter.observe_identity_probe.side_effect=observe
        self.start();self.assertTrue(entered.wait(1))
    def test_completed_probe_stays_paused_and_persists_terminal_audit(self):
        self.start()
        self.assertEqual(self.finish()['state'],'verified')
        self.assertTrue(self.service.paused)
        self.assertEqual(self.store.values('identity_probe')[0]['state'],'verified')
        self.adapter.restore_identity_probe.assert_called_once()
        self.adapter.commit_identity_probe.assert_called_once()
        self.assertEqual(self.store.usage(self.service.clock())['day'],0)
    def test_pause_cancels_inflight_and_discards_observed_identity(self):
        self.service.accounts_cache['candidate']=replace(self.service.accounts_cache['candidate'],identity_certain=True)
        self.block_observe();self.service.pause();self.release.set()
        self.assertEqual(self.finish()['state'],'cancelled')
        self.assertFalse(self.service.accounts_cache['candidate'].identity_certain)
        self.service.grants['candidate']=Grant(read=True,paused=True)
        with self.assertRaisesRegex(ConsoleError,'account_identity_uncertain'):
            self.service._require('candidate')
        self.adapter.commit_identity_probe.assert_not_called()
        self.adapter.restore_identity_probe.assert_called_once()
    def test_no_concurrent_start_resume_or_manual_chat_read(self):
        self.block_observe()
        with self.assertRaisesRegex(ConsoleError,'already_running'):self.start()
        with self.assertRaisesRegex(ConsoleError,'in_progress'):self.service.resume()
        with self.assertRaisesRegex(ConsoleError,'in_progress'):self.service._require('a')
        self.service.identity_probe('cancel',{});self.release.set();self.finish()
    def test_unknown_instance_fails_before_adapter_prepare(self):
        with self.assertRaisesRegex(ConsoleError,'client_instance_missing'):
            self.service.identity_probe('start',dict(instance_id='8:999'))
        self.adapter.prepare_identity_probe.assert_not_called()
    def test_restore_failure_never_saves_identity(self):
        self.adapter.restore_identity_probe.side_effect=OSError('synthetic')
        self.start()
        self.assertEqual(self.finish()['reason'],'identity_probe_restore_failed')
        self.adapter.commit_identity_probe.assert_not_called()
    def test_restart_cancels_audit_without_restarting_probe_or_touching_binding(self):
        self.store.put('identity_probe','old',dict(id='old',state='waiting'))
        self.store.put('binding','account',dict(old_epoch=True))
        restarted=ConsoleService(self.adapter,self.store,acceptance=SimpleNamespace())
        self.assertEqual(self.store.get('identity_probe','old')['state'],'cancelled_on_restart')
        self.assertIsNone(restarted.identity_probe_task)
        self.assertEqual(self.store.get('binding','account'),dict(old_epoch=True))
        restarted.close()


if __name__=='__main__':unittest.main()
