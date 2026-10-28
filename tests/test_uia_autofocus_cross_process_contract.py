"""Pure protocol and evidence tests; no process, COM or desktop is opened."""
import copy
import io
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_autofocus_cross_process as probe
from tests.test_uia_autofocus_tree_contract import Fixture


def result_fixture():
    result = Fixture().result()
    result['target']['tid'] = 44
    for phase in result['phases']:
        phase.update(remote_client_pid=51, remote_client_created=1234, remote_mta_verified=True)
    result['phases'][-1].update(remote_exit_before_provider_cleanup=True, relay_client_handle_closed=True,
        remote_finish={'cleanup': dict.fromkeys(('com_uninitialized', 'provider_handle_closed',
                                               'desktop_restored', 'desktop_closed'), True)})
    return result


def report_fixture():
    result = result_fixture()
    return {'result': result, 'source_unchanged': True, 'uia_dll_unchanged': True,
        'input_checkpoints_unchanged': True, 'checkpoint_count': 20,
        'cleanup': dict.fromkeys(('child_exited', 'native_cleanup_confirmed',
            'owned_windows_absent', 'desktop_closed', 'desktop_absent'), True),
        'remote_identity': {'pid': 51, 'created': 1234},
        'provider_identity': {'pid': 31, 'created': 99}, 'owned_target': copy.deepcopy(result['target']),
        'process_handles_closed': {'client': True, 'provider': True},
        'remote_cleanup': {'exited': True, 'exitcode': 0, 'terminated': False,
                           'result': copy.deepcopy(result['phases'][-1]['remote_finish'])}}


class CrossProcessContractTests(unittest.TestCase):
    def test_protocol_requires_prepare_checks_and_complete(self):
        sender = probe.ExchangeProtocol('nonce', 'phase')
        receiver = probe.ExchangeProtocol('nonce', 'phase')
        for action in ('prepare', 'check', 'check', 'complete'):
            receiver.validate(sender.request(action))
        self.assertEqual(receiver.sequence, 4)
        self.assertEqual(receiver.state, 'complete')
        with self.assertRaises(RuntimeError):
            receiver.request('check')

    def test_protocol_rejects_duplicate_wrong_phase_nonce_or_sequence(self):
        for change in ({'nonce': 'other'}, {'phase': 'other'}, {'seq': 2}, {'extra': 'data'}):
            receiver = probe.ExchangeProtocol('nonce', 'phase')
            request = {'nonce': 'nonce', 'phase': 'phase', 'seq': 1, 'action': 'prepare'}
            request.update(change)
            with self.assertRaises(RuntimeError):
                receiver.validate(request)
        receiver = probe.ExchangeProtocol('nonce', 'phase')
        request = {'nonce': 'nonce', 'phase': 'phase', 'seq': 1, 'action': 'prepare'}
        receiver.validate(request)
        with self.assertRaises(RuntimeError):
            receiver.validate(request)

    def test_protocol_rejects_unknown_actions_or_unbounded_requests(self):
        for action in ('check', 'complete', 'send', 'clear_draft'):
            with self.assertRaises(RuntimeError):
                probe.ExchangeProtocol('nonce', 'phase').request(action)
        receiver = probe.ExchangeProtocol('nonce', 'phase')
        receiver.request('prepare')
        for _ in range(31):
            receiver.request('check')
        with self.assertRaises(RuntimeError):
            receiver.request('check')

    def test_target_must_be_exact_owned_provider_and_distinct_windows(self):
        target = dict(Fixture().target, tid=44)
        self.assertTrue(probe.target_valid(target, 31))
        for changed in (dict(target, pid=99), dict(target, a=100), dict(target, tid=True),
                        dict(target, a=0), {}, None):
            self.assertFalse(probe.target_valid(changed, 31))

    def test_four_phase_fixture_still_executes_real_candidate_contract(self):
        self.assertTrue(probe.source_qualified(result_fixture()))
        self.assertTrue(probe.complete_pass(report_fixture()))

    def test_same_process_or_client_restart_is_not_cross_process_comparison(self):
        for index, change in ((0, {'remote_client_pid': 31}), (3, {'remote_client_pid': 52}),
                              (2, {'remote_client_created': 1235}), (0, {'remote_mta_verified': False})):
            result = result_fixture()
            result['phases'][index].update(change)
            self.assertFalse(probe.source_qualified(result))

    def test_existing_tree_positive_negative_and_unknown_requirements_still_apply(self):
        for index, change in ((0, {'focus_events': []}), (2, {'focus_events': [[101, 'set']]}),
                              (2, {'uia_full_readback_equal': False}),
                              (3, {'legacy_reader_checks': 0})):
            result = result_fixture()
            result['phases'][index].update(change)
            self.assertFalse(probe.source_qualified(result))

    def test_remote_ack_without_exit_before_provider_cleanup_fails(self):
        result = result_fixture()
        result['phases'][-1]['remote_exit_before_provider_cleanup'] = False
        self.assertFalse(probe.source_qualified(result))

    def test_each_remote_com_handle_and_desktop_cleanup_is_required(self):
        for key in ('com_uninitialized', 'provider_handle_closed', 'desktop_restored', 'desktop_closed'):
            result = result_fixture()
            result['phases'][-1]['remote_finish']['cleanup'][key] = False
            self.assertFalse(probe.source_qualified(result))

    def test_root_requires_actual_remote_normal_exit_without_force(self):
        for change in ({'exited': False}, {'exitcode': 43}, {'terminated': True}, {'result': {}}):
            report = report_fixture()
            report['remote_cleanup'].update(change)
            self.assertFalse(probe.complete_pass(report))

    def test_root_and_relay_cleanup_evidence_must_agree(self):
        report = report_fixture()
        report['remote_cleanup']['result']['cleanup']['desktop_closed'] = False
        self.assertFalse(probe.complete_pass(report))
        report = report_fixture()
        report['remote_identity']['created'] = 1235
        self.assertFalse(probe.complete_pass(report))

    def test_provider_and_target_must_match_owned_controller_binding(self):
        for field, change in (('provider_identity', {'pid': 32, 'created': 99}),
                              ('owned_target', {'pid': 31, 'host': 103, 'a': 101, 'b': 102})):
            report = report_fixture()
            report[field] = change
            self.assertFalse(probe.complete_pass(report))

    def test_retained_handles_must_close(self):
        result = result_fixture()
        result['phases'][-1]['relay_client_handle_closed'] = False
        self.assertFalse(probe.source_qualified(result))
        for key in ('client', 'provider'):
            report = report_fixture()
            report['process_handles_closed'][key] = False
            self.assertFalse(probe.complete_pass(report))

    def test_input_changes_source_changes_and_host_cleanup_fail_closed(self):
        for key in ('input_checkpoints_unchanged', 'source_unchanged', 'uia_dll_unchanged'):
            report = report_fixture()
            report[key] = False
            self.assertFalse(probe.complete_pass(report))
        report = report_fixture()
        report['cleanup']['owned_windows_absent'] = False
        self.assertFalse(probe.complete_pass(report))

    def test_deadline_does_not_reset_or_read_after_expiry(self):
        pipe = Mock()
        with patch.object(probe.time, 'monotonic', return_value=10):
            with self.assertRaisesRegex(RuntimeError, 'cross_process_deadline'):
                probe.receive(pipe, 9)
        pipe.poll.assert_not_called()
        pipe.recv.assert_not_called()

    def test_pipe_timeout_is_not_success_or_retried(self):
        pipe = Mock()
        pipe.poll.return_value = False
        with patch.object(probe.time, 'monotonic', return_value=10):
            with self.assertRaisesRegex(RuntimeError, 'cross_process_deadline'):
                probe.receive(pipe, 11)
        pipe.poll.assert_called_once_with(1)
        pipe.recv.assert_not_called()

    def test_reaping_terminates_only_the_owned_process_object(self):
        process = Mock(pid=51, exitcode=-15)
        process.is_alive.side_effect = [True, False]
        result = probe.reap_client(process)
        process.terminate.assert_called_once_with()
        self.assertTrue(result['exited'])
        self.assertTrue(result['terminated'])
        self.assertEqual(result['exitcode'], -15)

    def test_join_failure_remains_failure_even_if_exit_is_later_confirmed(self):
        process = Mock(pid=51, exitcode=0)
        process.join.side_effect = OSError('synthetic')
        process.is_alive.return_value = False
        result = probe.reap_client(process)
        self.assertTrue(result['exited'])
        self.assertIn('remote_join_failed', result['errors'])
        process.terminate.assert_not_called()

    def test_unconfirmed_exit_uses_held_handle_and_never_reopens_pid(self):
        process = Mock(pid=51, sentinel=1234, exitcode=None)
        process.is_alive.return_value = True
        process.terminate.side_effect = OSError('synthetic')
        wait = Mock(return_value=258)
        with patch.object(probe.c, 'WinDLL', return_value=object(), create=True), patch.object(probe.base, 'call', return_value=wait) as call:
            result = probe.reap_client(process)
        self.assertFalse(result['exited'])
        self.assertTrue(result['sentinel_rechecked'])
        wait.assert_called_once_with(1234, 2000)
        self.assertEqual(call.call_args.args[1], 'WaitForSingleObject')

    def test_non_integer_sequence_is_rejected(self):
        receiver = probe.ExchangeProtocol('nonce', 'phase')
        with self.assertRaises(RuntimeError):
            receiver.validate({'nonce': 'nonce', 'phase': 'phase', 'seq': True, 'action': 'prepare'})

    def test_frozen_tree_change_prevents_run(self):
        with patch.object(probe.tree, 'require_base'), patch.object(probe.hashlib, 'sha256',
                return_value=Mock(hexdigest=lambda: 'changed')):
            with self.assertRaisesRegex(RuntimeError, 'frozen_tree_canary_changed'):
                probe.require_sources()

    def test_default_has_no_native_or_external_target_entry(self):
        with patch.object(probe, 'run', side_effect=AssertionError('native forbidden')) as native:
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(probe.main([]), 0)
            native.assert_not_called()
        for flag in ('--pid', '--hwnd', '--account', '--operator-idle', '--send'):
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                probe.main([flag, '123'])


if __name__ == '__main__':
    unittest.main()
