from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import psutil
from bridge_portable import console, deploy


class ConsoleLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        self.release = self.root / 'releases' / self.commit
        self.release.mkdir(parents=True)
        (self.root / 'data').mkdir()
        deploy.write(self.root / 'installation.json', {'version': 1})
        deploy.write(self.root / 'current.json', {'commit': self.commit})
        self.updates = SimpleNamespace(mode='managed', root=self.root,
            manifest={'commit': self.commit, 'launcher_protocol': 1})

    def test_first_release_provisions_after_old_controller_finishes(self):
        with patch.object(console, 'Updates', return_value=self.updates), \
             patch.object(deploy, 'Deployment') as manager:
            deploy.write(self.root / 'transaction.json', {'commit': self.commit})
            self.assertFalse(console.ensure_console(self.release))
            manager.assert_not_called()
            (self.root / 'transaction.json').unlink()
            self.assertTrue(console.ensure_console(self.release))
            manager.return_value.refresh_console.assert_called_once_with(self.commit)
            manager.return_value.restore_console.assert_called_once_with(self.commit)

    def test_activation_hold_and_deployment_lock_defer_console(self):
        with patch.object(console, 'Updates', return_value=self.updates), patch.object(deploy, 'Deployment') as manager:
            deploy.write(self.root / 'data/deployment-hold.json', {})
            self.assertFalse(console.ensure_console(self.release))
            (self.root / 'data/deployment-hold.json').unlink()
            with patch.object(deploy, 'lock', side_effect=PermissionError):
                self.assertFalse(console.ensure_console(self.release))
            manager.assert_not_called()

    def test_portable_opens_background_and_obsolete_release_does_not(self):
        self.updates.mode = 'portable'
        (self.root / 'updates').mkdir()
        with patch.object(console, 'Updates', return_value=self.updates), patch.object(deploy, 'launch_console') as launch:
            self.assertTrue(console.ensure_console(self.release))
            launch.assert_called_once_with(self.release, background=True)
            deploy.write(self.root / 'current.json', {'commit': 'b' * 40})
            self.assertTrue(console.ensure_console(self.release))
            self.assertEqual(launch.call_count, 1)

    def test_legacy_launcher_never_receives_background_argument(self):
        for protocol in (0, 1):
            deploy.write(self.release / 'portable-manifest.json', {'launcher_protocol': protocol})
            with patch.object(deploy.subprocess, 'Popen') as launch:
                deploy.launch_console(self.release)
                argv = launch.call_args.args[0]
                self.assertEqual('--background' in argv, bool(protocol))
                if deploy.os.name == 'nt':
                    self.assertEqual(launch.call_args.kwargs['startupinfo'].wShowWindow, 0 if protocol else 1)

    def test_busy_launcher_is_not_killed_and_foreign_process_is_ignored(self):
        process = Mock(pid=123, info={'exe': str(self.release / 'WeChatAgentBridge.exe')})
        process.is_running.return_value = True
        process.wait.side_effect = psutil.TimeoutExpired(0)
        other = Mock(pid=456, info={'exe': str(self.root / 'other.exe')})
        with patch.object(deploy.psutil, 'process_iter', return_value=[other, process]), \
             patch.object(deploy, 'notify_launcher_exit') as notify:
            with self.assertRaisesRegex(RuntimeError, 'no process was terminated'):
                deploy.close_launcher(self.release, timeout=0)
            notify.assert_called_once_with(123)
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
            other.wait.assert_not_called()

    def test_background_bootstrap_preserves_startup_intent(self):
        from bridge_portable import rpc, update_engine, updates
        for background in (False, True):
            with patch.object(rpc, 'ROOT', self.root), patch.object(updates, 'installation', return_value=None), \
                 patch.object(deploy, 'read', return_value={'development': False}), \
                 patch.object(update_engine, 'bootstrap', return_value=self.release), \
                 patch.object(deploy, 'launch_console') as launch:
                self.assertEqual(rpc.dispatch({'action': 'bootstrap', 'background': background}), 'opened')
                launch.assert_called_once_with(self.release, background=background)

    def test_managed_password_reset_rejects_deploy_and_obsolete_window(self):
        from bridge_portable import rpc, control, updates
        with patch.object(rpc, 'ROOT', self.release), patch.object(control, 'DATA', self.root / 'data'), \
             patch.object(updates, 'Updates', return_value=self.updates), patch.object(control, 'reset') as reset:
            deploy.write(self.root / 'transaction.json', {})
            with self.assertRaisesRegex(ValueError, '正在部署'): rpc.dispatch({'action': 'reset'})
            (self.root / 'transaction.json').unlink()
            deploy.write(self.root / 'current.json', {'commit': 'b' * 40})
            with self.assertRaisesRegex(ValueError, '已过期'): rpc.dispatch({'action': 'reset'})
            reset.assert_not_called()
            deploy.write(self.root / 'current.json', {'commit': self.commit})
            rpc.dispatch({'action': 'reset', 'password': 'synthetic', 'confirmation': 'synthetic'})
            reset.assert_called_once_with('synthetic', 'synthetic')

    def test_interrupted_portable_rollback_reopens_console(self):
        from bridge_portable.update_engine import Engine, pointer, location
        deploy.write(self.root / 'installation.json', {'mode': 'portable'})
        point = pointer({'version': '1.0.0', 'commit': self.commit, 'architecture': 'windows-x64'})
        state = self.root / 'updates'
        task = 'c' * 32
        deploy.write(state / 'request.json', {'task_id': task, 'previous': point})
        deploy.write(state / 'transaction.json', {'task_id': task, 'phase': 'rollback', 'error': 'fixture', 'reopen': True})
        with patch.object(Engine, 'rollback') as rollback, patch.object(deploy, 'launch_console') as launch:
            Engine(self.root).apply()
            rollback.assert_called_once()
            launch.assert_called_once_with(location(self.root, point), background=True)
            self.assertFalse((state / 'transaction.json').exists())
