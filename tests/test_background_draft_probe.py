"""Offline coordinator integration: every desktop/process/store port is synthetic."""
from collections import deque
from contextlib import ExitStack
import copy
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

# The opt-in CLI adds its repository root for direct execution. Preserve the
# discover loader's path order when importing that CLI as a test dependency.
_discovery_path = sys.path[:]
try:
    from scripts import probe_background_draft as probe
finally:
    sys.path[:] = _discovery_path
del _discovery_path
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from wechat_agent_bridge.adapters.uia_draft_candidate import DraftTarget
from wechat_agent_bridge.console_models import ConsoleError

TARGET_DESKTOP = '11111111-2222-3333-4444-555555555555'
FRONT_DESKTOP = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


class OwnProcess:
    def __init__(self, stalled=False, start_error=False, join_error=False):
        self.alive, self.exitcode, self.stalled = False, None, stalled
        self.terminated, self.pid = False, None
        self.start_error, self.join_error, self.join_calls = start_error, join_error, 0

    def start(self):
        if self.start_error:
            raise RuntimeError('synthetic_start_failure')
        self.alive = True
        self.pid = 8765

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.join_calls += 1
        if self.join_error:
            raise RuntimeError('synthetic_join_failure')
        if not self.stalled:
            self.alive, self.exitcode = False, 0

    def terminate(self):
        self.terminated, self.alive, self.exitcode = True, False, 1


class BackgroundDraftProbeTests(unittest.TestCase):
    def setUp(self):
        self.fp = Fingerprint(1234, 5678, 999, 'synthetic-version', 'synthetic-hash', 4096, 8192, 1)
        self.target = {'identity_verified': True, 'target_index': 0, 'session_runtime_id': [42, 10],
                       'input_runtime_id': [42, 20], 'draft_empty': True, 'value_readonly': False}
        self.binding = {'account': {'wechat_id': 'synthetic-account',
            'identity_source': 'uia_own_profile_exact_id_user_selected_window'}, 'fingerprint': asdict(self.fp)}
        self.evidence = {'patch': {'fingerprint': asdict(self.fp)}, 'target_controls': self.target}

    def args(self, **changes):
        value = dict(run=True, preflight=False, operator_idle=True, allow_accessibility_patch=True,
                     account='synthetic-account', group='synthetic-group', private='synthetic-private',
                     target_evidence='unused')
        value.update(changes)
        return SimpleNamespace(**value)

    def canary(self):
        phases = []
        for name in ('legacy_default', 'modern_default', 'modern_no_focus'):
            no_focus = name == 'modern_no_focus'
            phases.append({'phase': name, 'fresh_client': True, 'attempted': True,
                'uia_full_readback_equal': True, 'native_full_readback_equal': True, 'identity_stable': True,
                'focus_before': 102, 'focus': 102 if no_focus else 101,
                'focus_events': [] if no_focus else [[101, 'set']], 'event_overflow': False,
                'default_auto_set_focus': 1, 'configured_auto_set_focus': 0 if no_focus else 1})
        return {'passed': True, 'fingerprints': {'probe': 'hash'}, 'uia_sha256': 'dll-hash', 'os': 'test-os',
            'result': {'target': {'a': 101, 'b': 102}, 'mta_verified': True, 'phases': phases},
            'source_unchanged': True, 'uia_dll_unchanged': True, 'input_checkpoints_unchanged': True,
            'checkpoint_count': 2, 'cleanup': {key: True for key in
                ('child_exited', 'native_cleanup_confirmed', 'owned_windows_absent', 'desktop_closed', 'desktop_absent')}}

    def test_canary_needs_exact_script_dll_os_and_real_complete_pass(self):
        report = self.canary()
        probe.validate_canary(report, {'probe': 'hash'}, 'dll-hash', 'test-os')
        for key, value in (('fingerprints', {'probe': 'changed'}), ('uia_sha256', 'other'),
                           ('os', 'other-os'), ('passed', False), ('uia_dll_unchanged', False)):
            altered = copy.deepcopy(report)
            altered[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ConsoleError, 'missing_or_stale'):
                probe.validate_canary(altered, {'probe': 'hash'}, 'dll-hash', 'test-os')
        report['result']['phases'][2]['focus_events'] = [[101, 'set']]
        with self.assertRaisesRegex(ConsoleError, 'missing_or_stale'):
            probe.validate_canary(report, {'probe': 'hash'}, 'dll-hash', 'test-os')

    def test_binding_rejects_other_account_unverified_source_or_reused_pid(self):
        self.assertEqual(probe.validate_binding(self.binding, self.evidence, 'synthetic-account',
                                               asdict(self.fp)), self.target)
        for account, fp, source in (('other-account', self.fp, None),
                                   ('synthetic-account', replace(self.fp, pid=1235), None),
                                   ('synthetic-account', replace(self.fp, created=1000), None),
                                   ('synthetic-account', self.fp, 'nickname_only')):
            binding = copy.deepcopy(self.binding)
            if source:
                binding['account']['identity_source'] = source
            with self.subTest(account=account, fp=fp, source=source), \
                    self.assertRaisesRegex(ConsoleError, 'same_process_verified'):
                probe.validate_binding(binding, self.evidence, account, asdict(fp))

    def test_binding_requires_evidence_from_identical_process_and_group(self):
        for change in ('created', 'unverified', 'private_target', 'missing_runtime', 'bool_runtime', 'too_long'):
            evidence = copy.deepcopy(self.evidence)
            if change == 'created':
                evidence['patch']['fingerprint']['created'] += 1
            elif change == 'unverified':
                evidence['target_controls']['identity_verified'] = False
            elif change == 'private_target':
                evidence['target_controls']['target_index'] = 1
            else:
                evidence['target_controls']['input_runtime_id'] = {
                    'missing_runtime': [], 'bool_runtime': [True], 'too_long': [42] * 17}[change]
            with self.subTest(change=change), self.assertRaises(ConsoleError):
                probe.validate_binding(self.binding, evidence, 'synthetic-account', asdict(self.fp))

    def test_current_target_must_match_historical_runtime_and_expected_draft_phase(self):
        for key in ('session_runtime_id', 'input_runtime_id'):
            current = dict(self.target, **{key: [42, 99]})
            with self.assertRaisesRegex(ConsoleError, 'target_mismatch'):
                probe.check_candidate_target(self.target, current)
        written = dict(self.target, draft_empty=False, draft_is_fixed_probe=True)
        probe.check_candidate_target(self.target, written, written=True)
        with self.assertRaisesRegex(ConsoleError, 'existing_draft'):
            probe.check_candidate_target(self.target, written)
        for update in ({'draft_is_fixed_probe': False}, {'input_runtime_id': [42, 99]},
                       {'value_readonly': True}, {'target_index': 1}):
            with self.assertRaisesRegex(ConsoleError, 'target_or_draft_changed'):
                probe.check_candidate_target(self.target, dict(written, **update), written=True)

    def test_default_cli_only_prepares_without_native_or_coordinator(self):
        with patch.object(probe, 'require_canary', return_value='synthetic-hash'), \
                patch.object(probe, 'run') as run, \
                patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate') as native, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(probe.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())['state'], 'prepared')
        run.assert_not_called()
        native.assert_not_called()

    def test_run_requires_operator_idle_before_canary_store_or_native(self):
        with patch.object(probe, 'require_canary') as canary, \
                patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate') as native, \
                patch('wechat_agent_bridge.console_store.ConsoleStore') as store:
            with self.assertRaisesRegex(ConsoleError, 'operator_started_idle'):
                probe.run(self.args(operator_idle=False))
        canary.assert_not_called()
        native.assert_not_called()
        store.assert_not_called()

    def execute(self, messages, *, timeout=False, restore_error=False, used=False, current_fp=None, args=None,
                start_error=False, join_error=False, observed_events=False, audit_error=False,
                ledger_finish_error=False, restore_state='restored', taskview_desktop_id=None,
                desktop_query=None, desktop_close_error=False, snapshot_port=None,
                ledger_close_error=False, store_close_error=False, inspection_worker=None,target_scope=None):
        queue, events = deque(messages), []
        store, ledger, native, gate, observer = (Mock() for _ in range(5))
        store.get.side_effect = lambda namespace, key, default=None: self.binding if namespace == 'binding' else default
        if audit_error:
            store.put.side_effect = OSError('synthetic_audit_failure')
        ledger.status.return_value = {'attempt_used': used, 'draft_attempted': False, 'state': 'unused'}
        ledger.reserve.side_effect = lambda digest: events.append('reserve') or 'request'
        ledger.consume_draft.side_effect = lambda *values: events.append('consume')
        if ledger_finish_error:
            ledger.finish.side_effect = OSError('synthetic_ledger_finish_failure')
        if ledger_close_error:
            ledger.close.side_effect = OSError('synthetic_ledger_close_failure')
        if store_close_error:
            store.close.side_effect = OSError('synthetic_store_close_failure')
        native.fingerprint.return_value = current_fp or self.fp
        gate.restore.side_effect = ConsoleError('restore_failed') if restore_error else None
        gate.restore.return_value = {'state': restore_state}
        gate.enable.side_effect = lambda _fp, verify: verify()
        observer.__enter__ = Mock(return_value=observer)
        observer.evidence = SimpleNamespace(events=[{'synthetic': True}] if observed_events else [], failed=False)
        process = OwnProcess(stalled=timeout, start_error=start_error, join_error=join_error)
        parent, remote, context = Mock(), Mock(), Mock()
        parent.poll.side_effect = lambda seconds: bool(queue)
        parent.recv.side_effect = queue.popleft
        clock = SimpleNamespace(now=0)
        def send(message):
            events.append(('response', message))
            if timeout and ledger.consume_draft.call_count and message == {'continue': True}:
                clock.now = 46
        parent.send.side_effect = send
        context.Pipe.return_value = (parent, remote)
        context.Process.return_value = process
        desktop_reader = Mock()
        def default_desktop(hwnd, _events):
            if hwnd not in (self.fp.hwnd, 1):
                raise AssertionError('unrelated_window_query')
            return {'desktop_id': TARGET_DESKTOP if hwnd == self.fp.hwnd else FRONT_DESKTOP,
                    'current': hwnd != self.fp.hwnd}
        desktop_reader.query.side_effect = lambda hwnd: (desktop_query or default_desktop)(hwnd, events)
        if desktop_close_error:
            desktop_reader.close.side_effect = ConsoleError('virtual_desktop_close_failed')
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as directory, ExitStack() as stack:
            evidence_file = Path(directory) / 'synthetic-target.json'
            evidence_file.write_text(json.dumps(self.evidence), encoding='utf-8')
            arguments = args or self.args()
            arguments.target_evidence = str(evidence_file)
            for target, value in (
                (probe, {'DIRECTORY': Path(directory)}),
            ):
                stack.enter_context(patch.multiple(target, **value))
            stack.enter_context(patch.object(probe, 'require_canary', return_value='synthetic-uia-hash'))
            stack.enter_context(patch.object(probe, 'helper_fingerprints', return_value={'synthetic': 'hash'}))
            stack.enter_context(patch.object(probe, 'RunLock'))
            ledger_factory = stack.enter_context(patch.object(probe, 'DiagnosticLedger', return_value=ledger))
            stack.enter_context(patch.object(probe.mp, 'get_context', return_value=context))
            stack.enter_context(patch.object(probe.time, 'monotonic', side_effect=lambda: clock.now))
            stack.enter_context(patch.object(probe.time, 'sleep', side_effect=lambda seconds:
                                              setattr(clock, 'now', clock.now + seconds)))
            native_factory = stack.enter_context(patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate', return_value=native))
            stack.enter_context(patch('wechat_agent_bridge.adapters.accessibility_gate.AccessibilityGate', return_value=gate))
            stack.enter_context(patch('wechat_agent_bridge.adapters.background.snapshot',
                side_effect=snapshot_port or (lambda _pid: DesktopSnapshot(1, 2, 3))))
            desktop_factory = stack.enter_context(patch('wechat_agent_bridge.adapters.virtual_desktop.VirtualDesktopReader',
                return_value=desktop_reader))
            stack.enter_context(patch('wechat_agent_bridge.adapters.desktop_events.DesktopEventObserver', return_value=observer))
            stack.enter_context(patch('wechat_agent_bridge.console_store.ConsoleStore', return_value=store))
            stack.enter_context(patch('wechat_agent_bridge.protection.DPAPIProtector'))
            result = probe.run(arguments, taskview_desktop_id=taskview_desktop_id,
                               inspection_worker=inspection_worker,target_scope=target_scope)
        return SimpleNamespace(result=result, store=store, ledger=ledger, native=native, gate=gate,
            observer=observer, process=process, parent=parent, events=events, context=context,
            ledger_factory=ledger_factory, native_factory=native_factory,
            desktop_reader=desktop_reader, desktop_factory=desktop_factory)

    def verified(self):
        return {'kind': 'verified', 'value': {'target': self.target, 'root_runtime_id': [42, 1]}}

    def readback(self):
        return {'kind': 'result', 'value': {'status': 'readback_confirmed', 'state': 'readback_verified',
            'attempted': True, 'claim_attempted': True, 'claim_confirmed': True, 'readback_equal': True}}

    def test_coordinator_consumes_once_before_worker_release_and_restores_patch(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()])
        self.assertEqual(outcome.result['state'], 'readback_verified')
        self.assertTrue(outcome.result['patch_restored'])
        self.assertFalse(outcome.result['send_invoked'])
        self.assertFalse(outcome.result['api_called'])
        outcome.ledger.reserve.assert_called_once()
        outcome.ledger.consume_draft.assert_called_once()
        consume_at = outcome.events.index('consume')
        self.assertEqual(outcome.events[consume_at + 1], ('response', {'continue': True}))
        outcome.ledger.finish.assert_called_once_with('request', 'finished')
        outcome.gate.restore.assert_called_once()
        outcome.native.close.assert_called_once()

    def test_second_claim_or_success_without_claim_cannot_authorize_another_write(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, {'kind': 'claim'}])
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertEqual(outcome.result['reason'], 'candidate_protocol_mismatch')
        outcome.ledger.consume_draft.assert_called_once()
        outcome.gate.restore.assert_called_once()
        unclaimed = self.execute([self.verified(), self.readback()])
        self.assertEqual(unclaimed.result['reason'], 'candidate_result_without_claim')
        unclaimed.ledger.consume_draft.assert_not_called()

    def test_timeout_after_claim_terminates_only_owned_worker_and_restores_patch(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}], timeout=True)
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertTrue(outcome.process.terminated)
        self.assertTrue(outcome.result['worker_exited'])
        self.assertTrue(outcome.result['patch_restored'])
        outcome.ledger.consume_draft.assert_called_once()
        outcome.gate.restore.assert_called_once()
        outcome.ledger.finish.assert_called_once_with('request', 'failed')

    def test_patch_restore_failure_preserves_recovery_required_ledger(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
                               restore_error=True, observed_events=True)
        self.assertEqual(outcome.result['state'], 'recovery_required')
        self.assertFalse(outcome.result['patch_restored'])
        outcome.ledger.finish.assert_called_once_with('request', 'recovery_required')

    def test_target_exit_is_not_accepted_as_patch_restore_or_success(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
                               restore_state='process_exited')
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertEqual(outcome.result['reason'], 'candidate_process_exited_or_restore_unconfirmed')
        self.assertFalse(outcome.result['patch_restored'])
        outcome.ledger.finish.assert_called_once_with('request', 'recovery_required')

    def test_start_failure_never_joins_unstarted_worker_and_still_restores(self):
        outcome = self.execute([], start_error=True)
        self.assertEqual(outcome.result['state'], 'failed')
        self.assertEqual(outcome.process.join_calls, 0)
        self.assertTrue(outcome.result['worker_exited'])
        self.assertTrue(outcome.result['patch_restored'])
        outcome.gate.restore.assert_called_once()
        outcome.ledger.consume_draft.assert_not_called()
        outcome.native.close.assert_called_once()

    def test_worker_cleanup_exception_cannot_bypass_patch_restore(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()], join_error=True)
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertIn('candidate_worker_exit_unconfirmed', outcome.result['cleanup_errors'])
        outcome.gate.restore.assert_called_once()
        self.assertTrue(outcome.result['patch_restored'])
        outcome.native.close.assert_called_once()

    def test_audit_or_ledger_error_retains_unknown_after_native_resources_close(self):
        for argument, reason in (('audit_error', 'candidate_audit_write_failed'),
                                 ('ledger_finish_error', 'candidate_ledger_finish_failed')):
            outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()], **{argument: True})
            self.assertEqual(outcome.result['state'], 'unknown')
            self.assertIn(reason, outcome.result['cleanup_errors'])
            self.assertTrue(outcome.result['patch_restored'])
            outcome.native.close.assert_called_once()
            outcome.ledger.close.assert_called_once()
            outcome.store.close.assert_called_once()

    def test_run_lock_is_exclusive_and_never_removes_changed_ownership(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as directory:
            path = Path(directory) / 'synthetic.lock'
            first, second = probe.RunLock(path), probe.RunLock(path)
            first.acquire()
            with self.assertRaisesRegex(ConsoleError, 'run_locked'):
                second.acquire()
            second.release()
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding='ascii'), first.token)
            path.write_text('different-owner', encoding='ascii')
            with self.assertRaisesRegex(ConsoleError, 'owner_changed'):
                first.release()
            self.assertTrue(path.exists())

    def test_run_lock_releases_only_its_own_token(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as directory:
            lock = probe.RunLock(Path(directory) / 'synthetic.lock')
            lock.acquire()
            lock.release()
            self.assertFalse(lock.path.exists())
            self.assertFalse(lock.owned)

    def test_used_allowance_and_pid_reuse_never_enable_patch_or_spawn_worker(self):
        for options in ({'used': True}, {'current_fp': replace(self.fp, created=1000)}):
            outcome = self.execute([], **options)
            self.assertEqual(outcome.result['state'], 'failed')
            outcome.gate.enable.assert_not_called()
            outcome.context.Process.assert_not_called()
            outcome.ledger.reserve.assert_not_called()

    def test_preflight_verifies_without_claiming_or_calling_draft_candidate(self):
        outcome = self.execute([self.verified(), {'kind': 'result', 'value':
            {'state': 'preflight_verified', 'attempted': False}}], args=self.args(run=False, preflight=True))
        self.assertEqual(outcome.result['state'], 'preflight_verified')
        outcome.ledger.reserve.assert_not_called()
        outcome.ledger.consume_draft.assert_not_called()
        outcome.gate.restore.assert_called_once()

    def test_readonly_worker_cannot_be_used_for_a_write_run(self):
        with patch.object(probe, 'require_canary') as canary:
            with self.assertRaisesRegex(ConsoleError, 'inspection_requires_readonly'):
                probe.run(self.args(), inspection_worker=object())
        canary.assert_not_called()

    def test_readonly_worker_uses_separate_audit_without_consuming_used_allowance(self):
        worker = Mock()
        result = {'state':'preflight_verified', 'attempted':False, 'context':{'stable':True}}
        out = self.execute([self.verified(), {'kind':'result', 'value':result}], used=True,
            args=self.args(run=False, preflight=True), inspection_worker=worker)
        self.assertEqual(out.result['state'], 'preflight_verified')
        self.assertIs(out.context.Process.call_args.kwargs['target'], worker)
        out.ledger.reserve.assert_not_called()
        out.ledger.consume_draft.assert_not_called()
        out.store.put.assert_any_call('capability_evidence', 'readonly_bound_uia_context', out.result)

    def test_explicit_private_readonly_scope_passes_only_its_verified_label_to_worker(self):
        scope=Mock();scope.names=['synthetic-private'];scope.resolve.return_value=self.target
        scope.metadata.return_value=dict(target_kind='private',recipient_id_sha256='a'*64)
        result=dict(state='preflight_verified',attempted=False)
        out=self.execute([self.verified(),{'kind':'result','value':result}],used=True,
            args=self.args(run=False,preflight=True),inspection_worker=Mock(),target_scope=scope)
        self.assertEqual(out.result['state'],'preflight_verified')
        self.assertEqual(out.result['target_kind'],'private')
        self.assertEqual(out.context.Process.call_args.kwargs['args'][2],['synthetic-private'])
        scope.resolve.assert_called_once();self.assertTrue(scope.check.called)
        out.ledger.reserve.assert_not_called();out.ledger.consume_draft.assert_not_called()

    def test_private_scope_cannot_enable_the_old_draft_write_coordinator(self):
        with patch.object(probe,'require_canary') as canary:
            with self.assertRaisesRegex(ConsoleError,'private_scope_requires_readonly'):
                probe.run(self.args(run=False,preflight=True),target_scope=Mock())
        canary.assert_not_called()

    def test_readonly_mismatch_records_only_safe_metadata_before_rejecting(self):
        current=dict(self.target, input_runtime_id=[42,999], arbitrary_text='must not be retained')
        out=self.execute([{'kind':'verified','value':{'target':current}}],
            args=self.args(run=False,preflight=True),inspection_worker=Mock())
        self.assertEqual(out.result['reason'],'diagnostic_target_mismatch')
        self.assertEqual(out.result['observed_target_metadata']['input_runtime_id'],[42,999])
        self.assertNotIn('arbitrary_text',out.result['observed_target_metadata'])
        out.ledger.consume_draft.assert_not_called()
        out.gate.restore.assert_called_once()

    def test_preflight_rejects_worker_claiming_to_have_written(self):
        for value in ({'state':'preflight_verified', 'attempted':True},
                      {'state':'readback_verified', 'attempted':False}):
            out=self.execute([self.verified(), {'kind':'result', 'value':value}],
                args=self.args(run=False, preflight=True))
            self.assertEqual(out.result['reason'], 'inspection_result_claimed_write')
            out.ledger.reserve.assert_not_called()
            out.gate.restore.assert_called_once()

    def test_worker_builds_dataclass_and_accepts_its_own_fixed_draft_after_readback(self):
        backend, pipe = Mock(), Mock()
        order=[]
        raw=Mock();client=SimpleNamespace(_client=raw)
        prepared=(client,Mock(),Mock(),Mock(),False,SimpleNamespace())
        safe_context=dict(stable=True,association='present',requested_auto_focus=False,
            associated_auto_focus=False,matches_requested_client=True,matches_requested_context_body=True)
        backend.instances = {'1234:5678': [SimpleNamespace(element_info=SimpleNamespace(runtime_id=[42, 1]))]}
        backend.target_control_probe.side_effect = [self.target,
            dict(self.target, draft_empty=False, draft_is_fixed_probe=True)]
        def write(target, **options):
            self.assertEqual(target, DraftTarget(5678, 1234, (42, 1), (42, 20)))
            self.assertIs(options['client_factory'](),raw)
            self.assertTrue(options['pattern_guard'](raw,prepared[2],prepared[3]))
            self.assertTrue(options['claim']())
            self.assertTrue(options['checkpoint']('commit_write'))
            self.assertTrue(options['checkpoint']('after_readback'))
            return {'status': 'readback_confirmed', 'attempted': True, 'readback_equal': True}
        with patch('wechat_agent_bridge.adapters.windows_uia.WindowsUIA', side_effect=lambda:order.append('legacy') or backend), \
                patch('scripts.uia_client_bootstrap.prepare_client_elements',side_effect=lambda *_:order.append('prime') or prepared), \
                patch('scripts.uia_client_bootstrap.read_pattern_context',return_value=safe_context), \
                patch('wechat_agent_bridge.adapters.uia_draft_candidate._identity'), \
                patch('wechat_agent_bridge.adapters.uia_draft_candidate._empty'), \
                patch('wechat_agent_bridge.adapters.uia_draft_candidate.write_fixed_draft', side_effect=write) as writer, \
                patch.object(probe, '_exchange', return_value=True):
            probe.candidate_worker(pipe, asdict(self.fp), ['synthetic-group', 'synthetic-private'], self.target, True)
        writer.assert_called_once()
        self.assertEqual(order,['prime','legacy'])
        self.assertEqual(backend.target_control_probe.call_count, 2)
        self.assertEqual(pipe.send.call_args.args[0]['value']['state'], 'readback_verified')
        pipe.close.assert_called_once()

    def test_exchange_timeout_and_declined_continuation_fail_closed(self):
        pipe = Mock()
        pipe.poll.return_value = False
        with self.assertRaisesRegex(ConsoleError, 'controller_timeout'):
            probe._exchange(pipe, 'claim')
        pipe.recv.assert_not_called()
        pipe.poll.return_value, pipe.recv.return_value = True, {'continue': False}
        with self.assertRaisesRegex(ConsoleError, 'stopped_by_controller'):
            probe._exchange(pipe, 'checkpoint', 'commit_write')

    def test_taskview_success_binds_noncurrent_desktop_and_uses_fixed_new_ledger(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
                               taskview_desktop_id=TARGET_DESKTOP)
        self.assertEqual(outcome.result['state'], 'readback_verified')
        self.assertEqual(outcome.result['virtual_desktop_binding']['target'],
            {'hwnd': self.fp.hwnd, 'desktop_id': TARGET_DESKTOP, 'current': False})
        self.assertEqual(outcome.ledger_factory.call_args.args[0].name, probe.TASKVIEW_LEDGER)
        outcome.ledger.consume_draft.assert_called_once()
        outcome.desktop_reader.close.assert_called_once()
        self.assertTrue(outcome.result['virtual_desktop_reader_closed'])
        self.assertFalse(outcome.result['send_invoked'])
        self.assertFalse(outcome.result['api_called'])

    def test_taskview_bad_guid_current_target_or_query_error_reject_before_patch(self):
        def query(case):
            def read(hwnd, _events):
                if case == 'reader_error':
                    raise ConsoleError('virtual_desktop_query_failed')
                target = hwnd == self.fp.hwnd
                return {'desktop_id': (FRONT_DESKTOP if case == 'wrong_guid' else TARGET_DESKTOP)
                        if target else FRONT_DESKTOP,
                        'current': case == 'current_target' if target else case != 'foreground_noncurrent'}
            return read
        for case in ('wrong_guid', 'current_target', 'reader_error', 'foreground_noncurrent'):
            with self.subTest(case=case):
                outcome = self.execute([], taskview_desktop_id=TARGET_DESKTOP, desktop_query=query(case))
                self.assertEqual(outcome.result['state'], 'failed')
                outcome.gate.enable.assert_not_called()
                outcome.context.Process.assert_not_called()
                outcome.ledger.reserve.assert_not_called()
                outcome.desktop_reader.close.assert_called_once()

    def test_taskview_invalid_guid_fails_before_canary_store_or_native(self):
        with (patch.object(probe, 'require_canary') as canary,
              patch.object(probe, 'DiagnosticLedger') as ledger,
              patch('wechat_agent_bridge.console_store.ConsoleStore') as store,
              patch('wechat_agent_bridge.adapters.accessibility_gate.NativeGate') as native,
              self.assertRaisesRegex(ConsoleError, 'taskview_desktop_id_required')):
            probe.run(self.args(), taskview_desktop_id='invalid-guid')
        canary.assert_not_called()
        ledger.assert_not_called()
        store.assert_not_called()
        native.assert_not_called()

    def test_taskview_desktop_change_or_reader_failure_before_claim_rejects_write(self):
        def query(case):
            def read(hwnd, events):
                verified_ack = ('response', {'continue': True}) in events
                if verified_ack and case == 'reader_error':
                    raise ConsoleError('virtual_desktop_query_failed')
                target = hwnd == self.fp.hwnd
                changed = verified_ack and case == 'desktop_changed' and target
                return {'desktop_id': ('99999999-2222-3333-4444-555555555555' if changed else TARGET_DESKTOP)
                        if target else FRONT_DESKTOP, 'current': not target}
            return read
        for case in ('desktop_changed', 'reader_error'):
            with self.subTest(case=case):
                outcome = self.execute([self.verified(), {'kind': 'claim'}],
                    taskview_desktop_id=TARGET_DESKTOP, desktop_query=query(case))
                self.assertEqual(outcome.result['state'], 'failed')
                outcome.ledger.reserve.assert_not_called()
                outcome.ledger.consume_draft.assert_not_called()
                outcome.gate.restore.assert_called_once()
                outcome.desktop_reader.close.assert_called_once()

    def test_taskview_foreground_switch_before_claim_is_rejected(self):
        calls = []
        def snapshot(_pid):
            calls.append(1)
            # Baseline and gate check pass; reject the first controller loop.
            return DesktopSnapshot(1 if len(calls) <= 2 else 9, 2, 3)
        outcome = self.execute([self.verified(), {'kind': 'claim'}],
            taskview_desktop_id=TARGET_DESKTOP, snapshot_port=snapshot)
        self.assertEqual(outcome.result['reason'], 'background_focus_changed')
        outcome.ledger.reserve.assert_not_called()
        outcome.gate.restore.assert_called_once()
        outcome.desktop_reader.close.assert_called_once()

    def test_taskview_reader_failure_after_claim_remains_unknown_and_consumed(self):
        def query(hwnd, events):
            if 'consume' in events:
                raise ConsoleError('virtual_desktop_query_failed')
            return {'desktop_id': TARGET_DESKTOP if hwnd == self.fp.hwnd else FRONT_DESKTOP,
                    'current': hwnd != self.fp.hwnd}
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
            taskview_desktop_id=TARGET_DESKTOP, desktop_query=query)
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertTrue(outcome.result['attempted'])
        outcome.ledger.consume_draft.assert_called_once()
        outcome.ledger.finish.assert_called_once_with('request', 'failed')
        outcome.gate.restore.assert_called_once()

    def test_taskview_reader_close_failure_cannot_be_reported_as_success(self):
        outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
            taskview_desktop_id=TARGET_DESKTOP, desktop_close_error=True)
        self.assertEqual(outcome.result['state'], 'unknown')
        self.assertIn('taskview_reader_close_failed', outcome.result['cleanup_errors'])
        self.assertTrue(outcome.result['patch_restored'])
        outcome.ledger.consume_draft.assert_called_once()
        outcome.native.close.assert_called_once()
        outcome.ledger.close.assert_called_once()
        outcome.store.close.assert_called_once()

    def test_taskview_ledger_or_store_close_failure_retains_unknown_attempt(self):
        for argument in ('ledger_close_error', 'store_close_error'):
            with self.subTest(argument=argument):
                outcome = self.execute([self.verified(), {'kind': 'claim'}, self.readback()],
                    taskview_desktop_id=TARGET_DESKTOP, **{argument: True})
                self.assertEqual(outcome.result['state'], 'unknown')
                self.assertTrue(outcome.result['attempted'])
                self.assertTrue(outcome.result['patch_restored'])
                self.assertTrue(outcome.result['cleanup_errors'])
                outcome.ledger.consume_draft.assert_called_once()
                outcome.desktop_reader.close.assert_called_once()
                outcome.native.close.assert_called_once()
                outcome.ledger.close.assert_called_once()
                outcome.store.close.assert_called_once()

    def test_taskview_used_allowance_opens_no_new_native_or_worker(self):
        outcome = self.execute([], taskview_desktop_id=TARGET_DESKTOP, used=True)
        self.assertEqual(outcome.result['reason'], 'diagnostic_attempt_already_used')
        outcome.native_factory.assert_not_called()
        outcome.desktop_factory.assert_not_called()
        outcome.context.Process.assert_not_called()
        outcome.ledger.reserve.assert_not_called()

    def test_original_entry_still_uses_its_own_ledger_without_virtual_desktop_reader(self):
        outcome = self.execute([], used=True)
        self.assertEqual(outcome.ledger_factory.call_args.args[0].name, 'diagnostics.sqlite3')
        outcome.desktop_factory.assert_not_called()
        outcome.native_factory.assert_not_called()
        outcome.context.Process.assert_not_called()

    def test_taskview_preflight_never_reserves_even_if_attempt_used(self):
        outcome = self.execute([self.verified(), {'kind': 'result', 'value':
            {'state': 'preflight_verified', 'attempted': False}}], taskview_desktop_id=TARGET_DESKTOP,
            used=True, args=self.args(run=False, preflight=True))
        self.assertEqual(outcome.result['state'], 'preflight_verified')
        outcome.ledger.reserve.assert_not_called()
        outcome.ledger.consume_draft.assert_not_called()
        outcome.desktop_reader.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
