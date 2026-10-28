import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import verify_bound_send as trial
from wechat_agent_bridge.acceptance import FIXED_TEXT
from wechat_agent_bridge.console_models import ConsoleError


class Port:
    def __init__(self):
        self.text, self.calls, self.rows = '', [], []
        self.after_set = lambda: None
        self.after_invoke = lambda: None
        self.enabled = True
    def verify_target(self): pass
    def draft(self): return self.text
    def capture(self): return dict(rows=list(self.rows))
    def guard_patterns(self): self.calls.append('guard')
    def send_enabled(self): return self.enabled
    def require_send_capability(self): pass  # Synthetic working sender only.
    def set_value(self, value):
        self.calls.append('set'); self.text = value; self.after_set()
    def invoke(self):
        self.calls.append('invoke'); self.text = ''
        self.rows.append(dict(runtime_id=[1, 2, 3], label=FIXED_TEXT))
        self.after_invoke()


class FixedSendTests(unittest.TestCase):
    def setUp(self):
        self.port = Port()
        self.reserve = Mock(side_effect=lambda: self.port.calls.append('reserve'))
        self.mark = Mock(side_effect=lambda: self.port.calls.append('mark'))
        self.check = Mock()
    def run_trial(self):
        return trial.fixed_transaction(self.port, self.check, self.reserve, self.mark)

    def test_default_disabled_without_windows_access(self):
        with patch.object(trial, 'run', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(trial.main([]), 0)

    def _assert_recovery_blocks_sender(self,pending_bucket):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            directory=Path(d);evidence=directory/'target.json';evidence.write_text('{}',encoding='utf-8')
            store=Mock()
            def get(bucket,key,*default):
                if (bucket,key)==('binding','account'):return {'fingerprint':{}}
                if (bucket,key)==(pending_bucket,'pending'):return {'phase':'terminal_unknown'}
                return None
            store.get.side_effect=get
            ledger=Mock();ledger.status.return_value={}
            lock=Mock()
            args=SimpleNamespace(allow_accessibility_patch=True,account='synthetic-account',group='synthetic-group',
                private='synthetic-private',target_evidence=str(evidence),synthetic_evidence='synthetic',
                virtual_desktop='synthetic',run_fixed_send=False,inspect_after_attempt=False,clear_test_draft=False)
            stack.enter_context(patch.object(trial.original,'DIRECTORY',directory))
            stack.enter_context(patch.object(trial.original,'RunLock',return_value=lock))
            stack.enter_context(patch.object(trial.original,'require_canary',return_value='synthetic'))
            stack.enter_context(patch.object(trial.original,'validate_binding',return_value={}))
            stack.enter_context(patch.object(trial,'sources',return_value={}))
            stack.enter_context(patch.object(trial,'AcceptanceLedger',return_value=ledger))
            stack.enter_context(patch('wechat_agent_bridge.console_store.ConsoleStore',return_value=store))
            stack.enter_context(patch('wechat_agent_bridge.protection.DPAPIProtector',return_value=Mock()))
            stack.enter_context(patch('scripts.probe_bound_uia_context.require_context_canary'))
            native=stack.enter_context(patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate'))
            result=trial.run(args)
            self.assertEqual(result['state'],'failed');self.assertEqual(result['reason'],'prior_recovery_required')
            native.assert_not_called();ledger.reserve.assert_not_called();lock.release.assert_called_once()

    def test_unknown_navigation_blocks_sender_before_native_access_or_budget(self):
        self._assert_recovery_blocks_sender('development_navigation')

    def test_unknown_exact_search_blocks_sender_before_native_access_or_budget(self):
        self._assert_recovery_blocks_sender('development_exact_search')

    def test_echo_is_not_own_identity_or_delivery_confirmation(self):
        result = self.run_trial()
        self.assertEqual(result['state'], 'message_echo_observed')
        self.assertFalse(result['own_identity_verified'])
        self.assertFalse(result['recipient_delivery_verified'])
        self.assertEqual(self.port.calls, ['guard', 'reserve', 'set', 'guard', 'mark', 'invoke'])
        self.reserve.assert_called_once(); self.mark.assert_called_once()

    def test_existing_draft_preserved_no_reservation(self):
        self.port.text = 'synthetic user draft'
        result = self.run_trial()
        self.assertFalse(result['draft_attempted'])
        self.assertEqual(self.port.text, 'synthetic user draft')
        self.reserve.assert_not_called()

    def test_duplicate_fixed_text_stops_before_write(self):
        self.port.rows = [dict(runtime_id=[1, 1], label=FIXED_TEXT)]
        self.assertEqual(self.run_trial()['reason'], 'fixed_text_already_present_no_retry')
        self.assertNotIn('set', self.port.calls)
        self.reserve.assert_not_called()

    def test_budget_rejection_prevents_any_write(self):
        self.reserve.side_effect = ConsoleError('acceptance_send_budget_exhausted')
        self.assertFalse(self.run_trial()['draft_attempted'])
        self.assertNotIn('set', self.port.calls)
        self.mark.assert_not_called()

    def test_unsupported_sender_stops_before_content_capture_or_reservation(self):
        self.port.require_send_capability = Mock(side_effect=ConsoleError('send_accessible_press_unsupported'))
        self.port.capture = Mock(side_effect=AssertionError('must not read messages'))
        result = self.run_trial()
        self.assertEqual(result['reason'], 'send_accessible_press_unsupported')
        self.assertFalse(result['draft_attempted'])
        self.assertFalse(result['send_attempted'])
        self.port.capture.assert_not_called()
        self.reserve.assert_not_called()

    def test_real_uia_invoke_stays_closed_for_known_and_unknown_fingerprints(self):
        for fingerprint, reason in ((dict(version=trial.VERSION, dll_sha256=trial.DLL_SHA256),
                                     'send_accessible_press_unsupported'),
                                    (dict(version='future', dll_sha256='unknown'),
                                     'background_send_implementation_unverified')):
            port = trial.UIAPort.__new__(trial.UIAPort)
            port.fp = fingerprint
            invoke = Mock()
            port.send_bundle = (None, None, invoke)
            with self.assertRaisesRegex(ConsoleError, reason):
                port.invoke()
            invoke.Invoke.assert_not_called()

    def test_focus_change_after_set_no_invoke_or_cleanup(self):
        def check(stage):
            if stage == 'after_set_value': raise ConsoleError('background_focus_changed')
        self.check.side_effect = check
        result = self.run_trial()
        self.assertFalse(result['send_attempted'])
        self.assertEqual(self.port.text, FIXED_TEXT)
        self.assertEqual(self.port.calls.count('set'), 1)
        self.mark.assert_not_called()

    def test_user_replacement_is_never_erased(self):
        self.port.after_set = lambda: setattr(self.port, 'text', 'synthetic replacement')
        self.assertFalse(self.run_trial()['send_attempted'])
        self.assertEqual(self.port.text, 'synthetic replacement')
        self.assertNotIn('invoke', self.port.calls)

    def test_disabled_send_does_not_invoke(self):
        self.port.enabled = False
        self.assertEqual(self.run_trial()['reason'], 'send_control_not_enabled')
        self.mark.assert_not_called()

    def test_failed_attempt_journal_prevents_invoke(self):
        self.mark.side_effect = OSError('synthetic journal full')
        self.assertFalse(self.run_trial()['send_attempted'])
        self.assertNotIn('invoke', self.port.calls)

    def test_invoke_timeout_and_focus_change_remain_unknown(self):
        for fail in (TimeoutError(), ConsoleError('background_focus_changed')):
            self.setUp()
            self.port.after_invoke = Mock(side_effect=fail)
            result = self.run_trial()
            self.assertEqual(result['state'], 'unknown')
            self.assertTrue(result['send_attempted'])
            self.assertEqual(self.port.calls.count('invoke'), 1)

    def test_mismatched_target_and_row_rebuild_rejected(self):
        expected = dict(input_runtime_id=[1, 2], session_runtime_id=[1, 3])
        valid = dict(**expected, identity_verified=True, target_index=0,
                     selected_row_verified=True, value_readonly=False)
        trial.target_matches(expected, valid)
        for field, value in (('target_index', 1), ('session_runtime_id', [1, 4]),
                             ('identity_verified', False), ('input_runtime_id', [9])):
            with self.subTest(field=field), self.assertRaises(ConsoleError):
                trial.target_matches(expected, dict(valid, **{field: value}))

    def test_cleanup_only_exact_test_text_never_invokes(self):
        self.port.text = FIXED_TEXT
        result = trial.clear_test_draft(self.port, self.check, self.mark)
        self.assertEqual(result['state'], 'fixed_draft_cleared')
        self.assertEqual(self.port.text, '')
        self.assertEqual(self.port.calls, ['guard', 'mark', 'set'])

    def test_cleanup_preserves_other_and_changed_drafts(self):
        self.port.text = 'user text'
        result = trial.clear_test_draft(self.port, self.check, self.mark)
        self.assertFalse(result['cleanup_attempted'])
        self.assertEqual(self.port.text, 'user text')
        self.mark.assert_not_called()
        self.port.text = FIXED_TEXT
        self.port.guard_patterns = lambda: setattr(self.port, 'text', 'new user text')
        result = trial.clear_test_draft(self.port, self.check, self.mark)
        self.assertFalse(result['cleanup_attempted'])
        self.assertEqual(self.port.text, 'new user text')

    def test_cleanup_focus_change_before_write_stops(self):
        self.port.text = FIXED_TEXT
        self.check.side_effect = ConsoleError('human_takeover')
        self.assertFalse(trial.clear_test_draft(self.port, self.check, self.mark)['cleanup_attempted'])
        self.assertEqual(self.port.text, FIXED_TEXT)

    def test_cleanup_exception_after_write_is_unknown(self):
        self.port.text = FIXED_TEXT
        self.port.after_set = Mock(side_effect=TimeoutError())
        result = trial.clear_test_draft(self.port, self.check, self.mark)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(self.port.calls.count('set'), 1)
