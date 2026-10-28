"""Synthetic ports only: no real HWND, process, COM, DPAPI or SQLite access."""
import contextlib
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import verify_desktop_test_binding as probe
from wechat_agent_bridge.adapters import accessibility_gate, windows
from wechat_agent_bridge import console_store, protection
from wechat_agent_bridge.console_models import ConsoleError


SOURCE = 'uia_own_profile_exact_id_user_selected_window'
FP = accessibility_gate.Fingerprint(101, 202, 303, accessibility_gate.VERSION,
    accessibility_gate.DLL_SHA256, 0x180000000, accessibility_gate.FLAG_RVA + 4096,
    accessibility_gate.FLAG_RVA)
ACCOUNT = {'id': 'synthetic-internal', 'wechat_id': 'synthetic_account',
    'instance_id': '101:202', 'identity_source': SOURCE, 'identity_certain': True}
TARGET = {'identity_verified': True, 'selected_row_verified': True, 'target_index': 0,
    'session_runtime_id': [42, 12], 'input_runtime_id': [42, 13], 'draft_empty': True,
    'draft_is_fixed_probe': False, 'value_readonly': False, 'send_button_count': 1,
    'readback': 'unverified',
    'conversation_identity_source': 'exact_selected_row_and_title_same_process_lifetime'}


class Ports:
    def __init__(self):
        self.values, self.writes, self.calls, self.closed = {}, [], [], []
        self.fp, self.byte, self.now = FP, 0, 0.0
        self.profile, self.target = copy.deepcopy(ACCOUNT), copy.deepcopy(TARGET)
        self.profile_error = self.close_error = None
        self.restore_fails = False
        self.after_profile = lambda: None
        self.native_constructed = False
        self.adapter_constructed = False

    def monotonic(self):
        self.now += .01
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def store_factory(self, *_args):
        outer = self
        class Store:
            def get(self, namespace, key, default=None):
                return copy.deepcopy(outer.values.get((namespace, key), default))
            def put(self, namespace, key, value):
                outer.values[namespace, key] = copy.deepcopy(value)
                outer.calls.append(('put', namespace, key))
            def close(self):
                outer.close('store')
        return Store()

    def native_factory(self, hwnd):
        self.native_constructed = True
        if hwnd != FP.hwnd:
            raise AssertionError('unexpected_synthetic_hwnd')
        outer = self
        class Native:
            def fingerprint(self):
                return outer.fp
            def alive(self):
                return True
            def validate_region(self):
                pass
            def read(self):
                return outer.byte
            def write(self, value):
                outer.writes.append(value)
                if value == 0 and outer.restore_fails:
                    raise ConsoleError('synthetic_restore_failed')
                outer.byte = value
            def close(self):
                outer.close('native')
        return Native()

    def adapter_factory(self, **_kwargs):
        self.adapter_constructed = True
        outer = self
        class Adapter:
            def _call(self, method, *args):
                if method not in ('verify_own_profile', 'target_control_probe'):
                    raise AssertionError('non_readonly_method')
                outer.calls.append((method, *args))
                if args[:2] != (FP.pid, FP.hwnd):
                    raise AssertionError('wrong_synthetic_process')
                if method == 'verify_own_profile':
                    if args[2] != ACCOUNT['wechat_id']:
                        raise AssertionError('wrong_synthetic_account')
                    if outer.profile_error:
                        raise ConsoleError(outer.profile_error)
                    outer.after_profile()
                    return copy.deepcopy(outer.profile)
                if args[2] != ['synthetic-group', 'synthetic-private']:
                    raise AssertionError('wrong_synthetic_targets')
                return copy.deepcopy(outer.target)
            def close(self):
                outer.close('adapter')
        return Adapter()

    def close(self, role):
        self.closed.append(role)
        if self.close_error == role:
            raise RuntimeError('synthetic_close_failed')


class BindingTests(unittest.TestCase):
    def execute(self, ports, *, flags=None, extra_flags=()):
        workspace_temp = Path(__file__).resolve().parents[1] / '.local/test-temp'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='desktop-binding-', dir=workspace_temp) as name:
            root = Path(name)
            directory = root / '.local/live-acceptance'
            directory.mkdir(parents=True)
            args = ['probe', '--hwnd', str(FP.hwnd), '--pid', str(FP.pid),
                '--created', str(FP.created), '--account', ACCOUNT['wechat_id'],
                '--group', 'synthetic-group', '--private', 'synthetic-private',
                '--allow-accessibility-patch']
            if flags is not None:
                args = flags
            args.extend(extra_flags)
            with (patch.object(probe, 'ROOT', root), patch.object(sys, 'argv', args),
                  patch.object(probe, 'time', SimpleNamespace(monotonic=ports.monotonic, sleep=ports.sleep)),
                  patch.object(accessibility_gate, 'NativeGate', ports.native_factory),
                  patch.object(windows, 'WindowsAdapter', ports.adapter_factory),
                  patch.object(console_store, 'ConsoleStore', ports.store_factory),
                  patch.object(protection, 'DPAPIProtector', return_value=object()),
                  contextlib.redirect_stdout(io.StringIO())):
                result = probe.main()
            files = list(directory.glob('desktop2-binding-*.json'))
            self.assertEqual(len(files), 1)
            return result, json.loads(files[0].read_text(encoding='utf-8'))

    def assert_readonly(self, report):
        for field in ('draft_attempted', 'send_invoked', 'api_called'):
            self.assertIs(report[field], False)

    def test_required_opt_in_rejects_before_opening_native_port(self):
        ports = Ports()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.execute(ports, flags=['probe', '--hwnd', '202'])
        self.assertFalse(ports.native_constructed)
        self.assertEqual(ports.writes, [])

    def test_success_persists_exact_account_and_restores_one_byte(self):
        ports = Ports()
        code, report = self.execute(ports)
        self.assertEqual(code, 0)
        self.assertEqual(report['state'], 'profile_verified')
        self.assertTrue(report['patch_restored'])
        self.assertEqual(ports.writes, [1, 0])
        self.assertEqual(ports.values['binding', 'account']['account'], ACCOUNT)
        self.assertIsNone(ports.values['patch', 'pending'])
        self.assertEqual(ports.closed, ['adapter', 'native', 'store'])
        self.assert_readonly(report)

    def test_pending_recovery_never_opens_target_or_patches(self):
        for namespace in ('patch', 'draft_probe'):
            ports = Ports()
            ports.values[namespace, 'pending'] = {'synthetic': True}
            with self.subTest(namespace=namespace):
                code, report = self.execute(ports)
                self.assertEqual(code, 1)
                self.assertEqual(report['reason'], 'prior_recovery_required')
                self.assertFalse(ports.native_constructed)
                self.assertEqual(ports.writes, [])
                self.assertEqual(ports.closed, ['store'])

    def test_process_creation_or_pid_mismatch_never_patches(self):
        for changed in (replace(FP, pid=404), replace(FP, created=404)):
            ports = Ports()
            ports.fp = changed
            with self.subTest(changed=changed):
                code, report = self.execute(ports)
                self.assertEqual(code, 1)
                self.assertEqual(ports.writes, [])
                self.assertNotIn(('binding', 'account'), ports.values)
                self.assertFalse(ports.adapter_constructed)
                self.assert_readonly(report)

    def test_profile_id_error_stops_without_binding_or_target_read(self):
        ports = Ports()
        ports.profile_error = 'account_profile_id_mismatch'
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['reason'], ports.profile_error)
        self.assertEqual(ports.writes, [1, 0])
        self.assertNotIn(('binding', 'account'), ports.values)
        self.assertEqual(len([c for c in ports.calls if c[0] == 'verify_own_profile']), 1)
        self.assertFalse(any(c[0] == 'target_control_probe' for c in ports.calls))

    def test_wrong_account_or_instance_return_is_not_persisted(self):
        for key, value in (('wechat_id', 'different_account'), ('instance_id', '999:202'),
                           ('identity_source', 'nickname_only')):
            ports = Ports()
            ports.profile[key] = value
            with self.subTest(key=key):
                code, report = self.execute(ports)
                self.assertEqual(code, 1)
                self.assertNotIn(('binding', 'account'), ports.values)
                self.assertFalse(any(c[0] == 'target_control_probe' for c in ports.calls))
                self.assertEqual(ports.writes, [1, 0])
                self.assert_readonly(report)

    def test_profile_absence_expires_and_restores_without_retrying_write(self):
        ports = Ports()
        ports.profile_error = 'account_identity_uncertain_open_own_profile'
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['reason'], 'own_profile_observation_expired')
        self.assertGreaterEqual(ports.now, 45)
        self.assertLess(ports.now, 47)
        self.assertEqual(ports.writes, [1, 0])
        self.assertNotIn(('binding', 'account'), ports.values)

    def test_worker_timeout_is_not_retried(self):
        ports = Ports()
        ports.profile_error = 'windows_operation_timeout'
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['reason'], ports.profile_error)
        self.assertEqual(len([c for c in ports.calls if c[0] == 'verify_own_profile']), 1)
        self.assertEqual(ports.writes, [1, 0])

    def test_longer_readonly_observation_is_bounded_and_restores(self):
        ports = Ports()
        ports.profile_error = 'account_identity_uncertain_open_own_profile'
        code, report = self.execute(ports, extra_flags=['--observe-seconds', '180'])
        self.assertEqual(code, 1)
        self.assertEqual(report['reason'], 'own_profile_observation_expired')
        self.assertEqual(report['observation_seconds'], 180)
        self.assertGreaterEqual(ports.now, 180)
        self.assertLess(ports.now, 182)
        self.assertEqual(ports.writes, [1, 0])
        self.assert_readonly(report)

    def test_observation_bounds_reject_before_native_access(self):
        for seconds in ('0', '181', 'infinite'):
            ports = Ports()
            with self.subTest(seconds=seconds), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.execute(ports, extra_flags=['--observe-seconds', seconds])
            self.assertFalse(ports.native_constructed)
            self.assertEqual(ports.writes, [])

    def test_identity_changes_after_profile_preserve_recovery_journal(self):
        ports = Ports()
        ports.after_profile = lambda: setattr(ports, 'fp', replace(FP, created=404))
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['state'], 'recovery_required')
        self.assertEqual(ports.writes, [1])
        self.assertIsNotNone(ports.values['patch', 'pending'])
        self.assertNotIn(('binding', 'account'), ports.values)
        self.assertEqual(ports.closed, ['adapter', 'native', 'store'])

    def test_target_mismatch_never_invokes_input_or_send(self):
        ports = Ports()
        ports.target = {'identity_verified': False, 'reason': 'acceptance_target_not_bound'}
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['state'], 'profile_verified_target_not_ready')
        self.assertIs(report['target_controls']['identity_verified'], False)
        self.assertEqual(ports.writes, [1, 0])
        self.assert_readonly(report)

    def test_readonly_input_keeps_account_binding_but_is_not_ready(self):
        ports = Ports()
        ports.target['value_readonly'] = True
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['state'], 'profile_verified_target_not_ready')
        self.assertEqual(ports.values['binding', 'account']['account'], ACCOUNT)
        self.assertTrue(report['patch_restored'])
        self.assertEqual(ports.writes, [1, 0])
        self.assert_readonly(report)

    def test_restore_failure_keeps_journal_and_still_closes_every_port(self):
        ports = Ports()
        ports.restore_fails = True
        code, report = self.execute(ports)
        self.assertEqual(code, 1)
        self.assertEqual(report['state'], 'recovery_required')
        self.assertFalse(report['patch_restored'])
        self.assertIsNotNone(ports.values['patch', 'pending'])
        self.assertEqual(ports.closed, ['adapter', 'native', 'store'])

    def test_cleanup_failure_does_not_skip_remaining_ports_or_report(self):
        for role in ('adapter', 'native', 'store'):
            ports = Ports()
            ports.close_error = role
            with self.subTest(role=role):
                code, report = self.execute(ports)
                self.assertEqual(code, 1)
                self.assertNotEqual(report['state'], 'profile_verified')
                self.assertEqual(ports.closed, ['adapter', 'native', 'store'])
                self.assertEqual(ports.writes, [1, 0])
                self.assert_readonly(report)


if __name__ == '__main__':
    unittest.main()
