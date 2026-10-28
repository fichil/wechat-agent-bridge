import contextlib
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import rebind_sender_profile as rebind
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint, VERSION, DLL_SHA256, FLAG_RVA
from wechat_agent_bridge.adapters.background import DesktopSnapshot
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore, digest


FP = Fingerprint(77, 202, 300, VERSION, DLL_SHA256, 4096, FLAG_RVA + 4096, FLAG_RVA)
ACCOUNT = dict(id='synthetic-internal', wechat_id='synthetic_sender', instance_id='77:202', identity_source=rebind.SOURCE)
OLD = dict(account={**ACCOUNT, 'instance_id': '66:101'}, fingerprint=asdict(replace(FP, pid=66, hwnd=101, created=100)))


def receipts():
    fingerprint = dict(kind='replacement_window_fingerprint_readonly', fingerprint=asdict(FP),
                       accessibility_byte=0, patch_enabled=False, target_controls_read=False,
                       history_read=False, send_invoked=False, api_called=False)
    cua = dict(kind='development_cua_own_identity', state='own_profile_visually_verified',
               source='computer_use_own_avatar_card', role='sender', account_sha256=rebind.sha(ACCOUNT['wechat_id']),
               window_id=FP.hwnd, fingerprint_evidence_sha256='synthetic_hash',
               text_input=False, draft_read=False, send_invoked=False, api_called=False, native_uia_or_patch_used=False)
    return fingerprint, cua


class SenderReceiptTests(unittest.TestCase):
    def test_metadata_other_app_focus_changes_do_not_qualify_a_binding_or_silence_events(self):
        before = DesktopSnapshot(1, 2, 3, False)
        current = DesktopSnapshot(4, 5, 3, False)
        events = [{'event': 'foreground', 'target_process': False}]
        rebind.require_metadata_environment(before, current, events)
        self.assertEqual(len(events), 1)
        for wrong in ([{'event': 'focus', 'target_process': True}], [{'event': 'focus'}]):
            with self.assertRaises(ConsoleError): rebind.require_metadata_environment(before, current, wrong)
        with self.assertRaises(ConsoleError):
            rebind.require_metadata_environment(before, replace(current, locked=True), events)
        with self.assertRaises(ConsoleError):
            rebind.require_metadata_environment(before, replace(current, clipboard_sequence=4), events)
        with self.assertRaises(ConsoleError):
            rebind.require_metadata_environment(before, replace(current, target_active=True), events)
    def test_metadata_input_elsewhere_is_allowed_only_for_background_readonly_scope(self):
        outside = DesktopSnapshot(1, 2, 3, False)
        rebind.require_input_scope(10, 11, outside, outside, metadata_only=True)
        with self.assertRaisesRegex(ConsoleError, 'human_takeover'):
            rebind.require_input_scope(10, 11, outside, outside, metadata_only=False)
        foreground = DesktopSnapshot(1, 2, 3, True)
        for before, current in ((foreground, foreground), (outside, foreground), (foreground, outside)):
            with self.assertRaisesRegex(ConsoleError, 'human_takeover'):
                rebind.require_input_scope(10, 11, before, current, metadata_only=True)

    def test_exact_own_profile_receipt_binds_only_current_native_fingerprint(self):
        fingerprint, cua = receipts()
        self.assertEqual(rebind.require_receipts(fingerprint, 'synthetic_hash', cua, ACCOUNT['wechat_id']), FP)

    def test_peer_card_missing_identity_wrong_window_and_stale_fingerprint_are_rejected(self):
        fingerprint, cua = receipts()
        for change in ({'kind': 'development_cua_private_restored'}, {'role': 'recipient'},
                       {'state': 'own_profile_unverified'}, {'source': 'peer_avatar_card'},
                       {'window_id': 999}, {'account_sha256': 'wrong'},
                       {'fingerprint_evidence_sha256': 'stale'}, {'text_input': True}):
            with self.assertRaises(ConsoleError):
                rebind.require_receipts(fingerprint, 'synthetic_hash', {**cua, **change}, ACCOUNT['wechat_id'])

    def test_wrong_build_bad_byte_and_invalid_account_are_rejected(self):
        fingerprint, cua = receipts()
        for change in ({'fingerprint': {**asdict(FP), 'dll_sha256': 'wrong'}}, {'accessibility_byte': 1},
                       {'accessibility_byte': False}, {'target_controls_read': True}):
            with self.assertRaises(ConsoleError):
                rebind.require_receipts({**fingerprint, **change}, 'synthetic_hash', cua, ACCOUNT['wechat_id'])
        with self.assertRaises(ConsoleError):
            rebind.require_receipts(fingerprint, 'synthetic_hash', cua, '')

    def test_default_cli_never_loads_receipts_or_native_ports(self):
        with patch.object(rebind, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rebind.main([]), 0)
            run.assert_not_called()
            self.assertIn('disabled', out.getvalue())


class BindingCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConsoleStore(Path(self.temp.name) / 'gate.sqlite3', SyntheticProtector())
        self.addCleanup(self.store.close)
        self.pending = dict(request_id='synthetic-rebind')
        self.store.put('binding', 'account', OLD)
        self.store.put('account_rebind', 'pending', self.pending)
        for bucket in ('private_baselines', 'verified_contacts', 'quota', 'development_navigation'):
            self.store.put(bucket, 'preserved', {'immutable': True, 'upload_eligible': False})
        self.report = dict(state='profile_observed', patch_restored=True, worker_exited=True, cleanup_errors=[],
                           events=[], sources_unchanged=True, request_id='synthetic-rebind')
        self.before = self.store.db.execute('SELECT bucket,key,payload FROM records ORDER BY bucket,key').fetchall()

    def commit(self, **changes):
        return rebind.commit_binding(self.store, OLD, ACCOUNT, FP, {**self.report, **changes}, self.pending, lambda: None)

    def test_atomic_archive_preserves_all_history_contacts_unknowns_and_quotas(self):
        result = self.commit()
        self.assertEqual(result['state'], 'sender_rebound')
        self.assertEqual(self.store.get('binding_epochs', rebind.content_hash(OLD['fingerprint'])), OLD)
        self.assertEqual(self.store.get('binding', 'account')['fingerprint'], asdict(FP))
        self.assertTrue(self.store.get('binding', 'account')['requires_fresh_target_identity'])
        self.assertIsNone(self.store.get('account_rebind', 'pending'))
        for bucket, key, payload in self.before:
            if bucket not in ('binding', 'account_rebind'):
                self.assertEqual(self.store.db.execute('SELECT payload FROM records WHERE bucket=? AND key=?',
                                                      (bucket, key)).fetchone()[0], payload)

    def test_incomplete_cleanup_and_transient_focus_never_change_binding(self):
        for change in ({'patch_restored': False}, {'worker_exited': False}, {'cleanup_errors': ['restore_failed']},
                       {'events': [{'transient': True}]}, {'sources_unchanged': False}):
            with self.assertRaises(ConsoleError):
                self.commit(**change)
            self.assertEqual(self.store.db.execute('SELECT bucket,key,payload FROM records ORDER BY bucket,key').fetchall(), self.before)

    def test_sqlite_failure_rolls_back_archive_new_binding_and_completion(self):
        key = digest('synthetic-rebind')
        self.store.db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON records "
                              "WHEN NEW.bucket='account_rebind' AND NEW.key='" + key + "' "
                              "BEGIN SELECT RAISE(ABORT, 'synthetic_abort'); END")
        self.store.db.commit()
        with self.assertRaisesRegex(Exception, 'synthetic_abort'):
            self.commit()
        self.assertEqual(self.store.db.execute('SELECT bucket,key,payload FROM records ORDER BY bucket,key').fetchall(), self.before)

    def test_changed_original_binding_or_pending_cannot_be_overwritten(self):
        for bucket, key in (('binding', 'account'), ('account_rebind', 'pending')):
            original = self.store.get(bucket, key)
            self.store.put(bucket, key, {'changed': True})
            with self.assertRaises(ConsoleError):
                self.commit()
            self.assertEqual(self.store.get(bucket, key), {'changed': True})
            self.store.put(bucket, key, original)

    def test_conflicting_prior_epoch_cannot_be_replaced(self):
        key = rebind.content_hash(OLD['fingerprint'])
        self.store.put('binding_epochs', key, {'different': True})
        with self.assertRaises(ConsoleError):
            self.commit()
        self.assertEqual(self.store.get('binding', 'account'), OLD)
        self.assertEqual(self.store.get('binding_epochs', key), {'different': True})


class SenderCoordinatorTests(unittest.TestCase):
    def execute(self, *, identity_error=False, restore_error=False, transient_focus=False, pending_before=False,
                old_instance_alive=False, metadata_only=False, malicious_binding_reply=False,
                outside_input_changes=False, non_target_event=False):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            directory = Path(directory)
            fingerprint, cua = receipts()
            native_path, cua_path = directory / 'fingerprint.json', directory / 'cua.json'
            native_path.write_text(json.dumps(fingerprint), encoding='utf-8')
            cua['fingerprint_evidence_sha256'] = rebind.hashlib.sha256(native_path.read_bytes()).hexdigest()
            cua_path.write_text(json.dumps(cua), encoding='utf-8')
            store = ConsoleStore(directory / 'gate.sqlite3', SyntheticProtector())
            store.put('binding', 'account', OLD)
            store.put('private_baselines', 'immutable', {'retained_rows': 8, 'upload_eligible': False})
            if pending_before:
                store.put('development_navigation', 'pending', {'active': True})
            store.close()
            native = Mock()
            native.fingerprint.return_value = FP
            native.alive.return_value = True
            native.read.side_effect = lambda: native.byte
            native.byte = 0
            def write(value):
                if value == 0 and restore_error:
                    raise ConsoleError('synthetic_restore_failed')
                native.byte = value
            native.write.side_effect = write
            parent, child, process = Mock(), Mock(), Mock(pid=333, exitcode=0)
            process.is_alive.return_value = False
            parent.poll.return_value = True
            account = {**ACCOUNT, 'wechat_id': 'another_sender'} if identity_error else ACCOUNT
            parent.recv.return_value = dict(kind='result', account=account)
            if metadata_only and not malicious_binding_reply:
                parent.recv.return_value = dict(kind='navigation_metadata', metadata=dict(
                    names_or_text_or_values_read=False, action_executed=False, own_avatar_identity_verified=False))
            context = Mock()
            context.Pipe.return_value = (parent, child)
            context.Process.return_value = process
            observer = Mock()
            observer.__enter__ = Mock(return_value=observer)
            observer.evidence = SimpleNamespace(lock=threading.RLock(), failed=False,
                events=[{'transient': True}] if transient_focus else
                [{'event': 'foreground', 'target_process': False}] if non_target_event else [])
            native_factory = Mock(return_value=native)
            for name, value in (
                ('wechat_agent_bridge.protection.DPAPIProtector', lambda: SyntheticProtector()),
                ('wechat_agent_bridge.adapters.accessibility_gate.NativeGate', native_factory),
                ('wechat_agent_bridge.adapters.background.snapshot', Mock(return_value=DesktopSnapshot(1, 2, 3))),
                ('wechat_agent_bridge.adapters.desktop_events.DesktopEventObserver', Mock(return_value=observer)),
            ):
                stack.enter_context(patch(name, value))
            stack.enter_context(patch.object(rebind.original, 'DIRECTORY', directory))
            ticks = iter(range(100))
            stack.enter_context(patch.object(rebind, 'input_tick',
                side_effect=(lambda: next(ticks)) if outside_input_changes else None, return_value=1))
            stack.enter_context(patch.object(rebind.mp, 'get_context', return_value=context))
            old_observer = Mock()
            old_observer.inspect.return_value = (dict(pid=66, created=100, wait_result=258) if old_instance_alive else
                dict(pid=66, open_error=87, pid_enumeration_complete=True, pid_in_enumeration=False))
            stack.enter_context(patch.object(rebind, 'WindowsProcessObserver', return_value=old_observer))
            args = SimpleNamespace(account=ACCOUNT['wechat_id'], allow_accessibility_patch=True,
                                   fingerprint_evidence=str(native_path), cua_evidence=str(cua_path),
                                   inspect_own_navigation=metadata_only)
            report = rebind.run(args)
            store = ConsoleStore(directory / 'gate.sqlite3', SyntheticProtector())
            try:
                bound = store.get('binding', 'account')
                pending = store.get('account_rebind', 'pending')
                baseline = store.get('private_baselines', 'immutable')
            finally:
                store.close()
            return report, bound, pending, (directory / 'autofocus-draft.lock').exists(), native_factory.called, baseline

    def test_coordinator_commits_only_account_after_restore_without_target_or_history_worker(self):
        report, bound, pending, locked, opened, baseline = self.execute()
        self.assertEqual(report['state'], 'sender_rebound')
        self.assertEqual(bound['fingerprint'], asdict(FP))
        self.assertIsNone(pending)
        self.assertFalse(locked)
        self.assertTrue(opened)
        self.assertEqual(baseline, {'retained_rows': 8, 'upload_eligible': False})
        for key in ('target_controls_read', 'draft_read', 'history_read', 'send_invoked', 'api_called'):
            self.assertIs(report[key], False)

    def test_wrong_account_reply_never_changes_old_binding(self):
        report, bound, pending, locked, _, _ = self.execute(identity_error=True)
        self.assertEqual(bound, OLD)
        self.assertEqual(report['state'], 'failed')
        self.assertIsNone(pending)
        self.assertFalse(locked)

    def test_restore_failure_and_transient_focus_keep_old_binding_and_recovery_lock(self):
        for kwargs in ({'restore_error': True}, {'transient_focus': True}):
            report, bound, pending, locked, _, _ = self.execute(**kwargs)
            self.assertEqual(bound, OLD)
            self.assertEqual(report['state'], 'recovery_required')
            self.assertTrue(pending)
            self.assertTrue(locked)

    def test_old_pending_recovery_stops_before_native_access(self):
        report, bound, _, locked, opened, _ = self.execute(pending_before=True)
        self.assertEqual(bound, OLD)
        self.assertFalse(opened)
        self.assertFalse(locked)
        self.assertEqual(report['reason'], 'sender_rebind_prior_recovery_required')

    def test_still_alive_old_instance_cannot_be_replaced(self):
        report, bound, _, locked, opened, _ = self.execute(old_instance_alive=True)
        self.assertEqual(bound, OLD)
        self.assertFalse(opened)
        self.assertFalse(locked)
        self.assertIn('original_process_still_alive', report['reason'])

    def test_metadata_mode_cannot_update_or_promote_the_account(self):
        report, bound, pending, locked, _, _ = self.execute(metadata_only=True)
        self.assertEqual(report['state'], 'own_navigation_metadata_observed')
        self.assertEqual(bound, OLD)
        self.assertFalse(report['account_binding_modified'])
        self.assertIsNone(pending)
        self.assertFalse(locked)

    def test_metadata_mode_rejects_an_account_binding_response(self):
        report, bound, _, _, _, _ = self.execute(metadata_only=True, malicious_binding_reply=True)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(bound, OLD)
        self.assertEqual(report['reason'], 'sender_rebind_navigation_cannot_bind_account')

    def test_other_application_input_does_not_block_metadata_but_still_blocks_binding(self):
        report, bound, pending, locked, _, _ = self.execute(metadata_only=True, outside_input_changes=True)
        self.assertEqual(report['state'], 'own_navigation_metadata_observed')
        self.assertEqual(bound, OLD)
        self.assertIsNone(pending)
        self.assertFalse(locked)
        report, bound, _, _, _, _ = self.execute(outside_input_changes=True)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['reason'], 'sender_rebind_human_takeover')
        self.assertEqual(bound, OLD)

    def test_metadata_non_target_events_are_retained_and_cannot_be_used_to_bind(self):
        report, bound, pending, locked, _, _ = self.execute(metadata_only=True, non_target_event=True)
        self.assertEqual(report['state'], 'own_navigation_metadata_observed')
        self.assertEqual(report['non_target_events_recorded'], 1)
        self.assertEqual(report['events'], [{'event': 'foreground', 'target_process': False}])
        self.assertFalse(report['background_qualified'])
        self.assertEqual(bound, OLD)
        self.assertIsNone(pending)
        self.assertFalse(locked)


class OwnProfileWorkerTests(unittest.TestCase):
    def run_worker(self, second):
        backend = Mock(spec=['verify_own_profile'])
        backend.verify_own_profile.side_effect = [ACCOUNT, second]
        pipe, client = Mock(), Mock()
        with (patch('wechat_agent_bridge.adapters.uia_client.BackgroundUIAClient', return_value=client),
              patch('wechat_agent_bridge.adapters.windows_uia.WindowsUIA', return_value=backend),
              patch.object(rebind.original, '_exchange'), patch.object(rebind.sys, 'coinit_flags', 0, create=True)):
            rebind.worker(pipe, asdict(FP), ACCOUNT['wechat_id'])
        self.assertEqual(backend.verify_own_profile.call_args_list,
                         [unittest.mock.call(FP.pid, FP.hwnd, ACCOUNT['wechat_id'])] * 2)
        pipe.close.assert_called_once()
        client.require_background_mode.assert_called_once()
        return pipe.send.call_args.args[0]

    def test_worker_calls_only_own_profile_twice_never_chat_or_draft_methods(self):
        result = self.run_worker(dict(ACCOUNT))
        self.assertEqual(result, dict(kind='result', account=ACCOUNT))

    def test_profile_change_between_observations_is_rejected(self):
        result = self.run_worker({**ACCOUNT, 'wechat_id': 'another_sender'})
        self.assertEqual(result, dict(kind='error', reason='sender_rebind_profile_changed'))


if __name__ == '__main__':
    unittest.main()
