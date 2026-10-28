"""Offline contracts for the disposable-owner UIA private ETW recovery probe."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_private_recovery as probe


class InjectedExit(BaseException):
    pass


class PrivateRecoveryContracts(unittest.TestCase):
    def successful_case(self, mode='external_controller_precheck'):
        stopped = {'actual_name_and_file_verified': True, 'stop_status': 0,
                   'query_after': 4201, 'events_lost': 0, 'absent': True}
        owner = {'dll_unchanged': True, 'process_handles_closed': [True, True]}
        result = {'case': mode, 'cleanup_unknown': False, 'target_alive_after_cleanup': True,
                  'post_cleanup_results': [probe.normal.INVALID] * probe.normal.COUNT,
                  'external_cleanup': stopped, 'created_observed': True, 'enabled_observed': True,
                  'owner_fault_exitcode': probe.EXIT_CODE,
                  'owner_timeout_observed': True, 'owner_terminated_before_cleanup': True,
                  'owner_alive_during_external_stop': True, 'all_children_dead': True,
                  'target_lease_closed': True, 'temporary_files_removed': True,
                  'source_unchanged': True, 'dll_unchanged': True,
                  'children': [
                      {'index': 0, 'terminated': False, 'exitcode': 0, 'result': owner},
                      {'index': 1, 'terminated': False, 'exitcode': 0,
                       'result': {'dll_unchanged': True}}]}
        if mode == 'command_eof_other':
            result['owner_fault_exitcode'] = 0
            result['owner_finally'] = dict(owner, error='EOFError:', cleanup=[stopped])
            result['external_cleanup'] = {'query_before': 4201, 'absent': True}
        return result

    def precheck(self):
        return {'stage': 'precheck', 'passed': True, 'live_diagnostic_ready': False,
                'fingerprints': {'own': 'hash'}, 'source_unchanged': True,
                'os': probe.platform.version(), 'dll_sha256': probe.normal.uia.DLL_HASH,
                'cases': [self.successful_case()]}

    def test_created_fault_is_journalled_independently_before_normal_ack(self):
        primary, evidence, order = Mock(), Mock(), []
        evidence.send.side_effect = lambda value: order.append('evidence')
        wrapped = probe.FaultPipe(primary, evidence, 'created_before_ack_exit', 'unused', 'token')
        with patch.object(probe, 'checkpoint', side_effect=lambda *args: order.append('journal')), \
                patch.object(probe.os, '_exit', side_effect=InjectedExit) as exit_process:
            with self.assertRaises(InjectedExit):
                wrapped.send({'created': 1, 'name': 'owned'})
        self.assertEqual(order, ['journal', 'evidence'])
        self.assertFalse(evidence.send.call_args.args[0]['normal_ack'])
        primary.send.assert_not_called()
        exit_process.assert_called_once_with(probe.EXIT_CODE)

    def test_enabled_fault_does_not_intercept_earlier_created_ack(self):
        primary, evidence = Mock(), Mock()
        wrapped = probe.FaultPipe(primary, evidence, 'enabled_exit_other', 'unused', 'token')
        with patch.object(probe, 'checkpoint'), patch.object(probe.os, '_exit', side_effect=InjectedExit):
            wrapped.send({'created': 1})
            with self.assertRaises(InjectedExit):
                wrapped.send({'phase_ready': 1})
        primary.send.assert_called_once_with({'created': 1})
        self.assertEqual(evidence.send.call_count, 2)

    def test_timeout_is_bounded_and_has_native_enabled_checkpoint_first(self):
        order, primary, evidence = [], Mock(), Mock()
        evidence.send.side_effect = lambda value: order.append('enabled')
        wrapped = probe.FaultPipe(primary, evidence, 'enabled_unresponsive_other', 'unused', 'token')
        with patch.object(probe, 'checkpoint'), \
                patch.object(probe.time, 'sleep', side_effect=lambda seconds: order.append(seconds)):
            wrapped.send({'phase_ready': 1})
        self.assertEqual(order, ['enabled', 8])
        primary.send.assert_called_once_with({'phase_ready': 1})

    def test_eof_finally_result_is_independently_preserved(self):
        primary, evidence = Mock(), Mock()
        primary.send.side_effect = BrokenPipeError
        wrapped = probe.FaultPipe(primary, evidence, 'command_eof_other', 'unused', 'token')
        with patch.object(probe, 'checkpoint') as saved:
            with self.assertRaises(BrokenPipeError):
                wrapped.send({'finished': {'cleanup': [{'stop_status': 0}]}})
        saved.assert_called_once()
        self.assertEqual(evidence.send.call_args.args[0]['checkpoint'], 'finished')

    def test_query_absence_without_stop_proof_cannot_pass_hard_exit(self):
        for mode in ('external_controller_precheck',) + probe.FAULTS:
            result = self.successful_case(mode)
            self.assertTrue(probe.case_passed(result))
            result['external_cleanup'] = {'query_before': 4201, 'absent': True}
            self.assertEqual(probe.case_passed(result), mode == 'command_eof_other')

    def test_final_cleanup_requires_owner_or_fallback_stop_not_query_absence(self):
        absent = {'query_before': 4201, 'absent': True}
        stopped = self.successful_case()['external_cleanup']
        self.assertFalse(probe.final_cleanup_proven([]))
        self.assertFalse(probe.final_cleanup_proven([
            {'index': 0, 'fallback_cleanup': absent, 'result': {'cleanup': [absent]}}]))
        self.assertTrue(probe.final_cleanup_proven([
            {'index': 0, 'fallback_cleanup': stopped}]))
        self.assertTrue(probe.final_cleanup_proven([
            {'index': 0, 'fallback_cleanup': absent, 'result': {'cleanup': [stopped]}}]))
        self.assertFalse(probe.final_cleanup_proven([
            {'index': 1, 'fallback_cleanup': stopped}]))
        self.assertFalse(probe.final_cleanup_proven([
            {'index': 0, 'result': {'cleanup': [stopped, stopped]}}]))

    def test_hard_exit_query_absence_retains_unknown_and_temporary_evidence(self):
        directory = probe.ROOT / '.local/live-acceptance'
        owner, target = Mock(pid=31, exitcode=probe.EXIT_CODE), Mock(pid=32, exitcode=0)
        owner.is_alive.return_value = target.is_alive.return_value = False
        context = Mock()
        context.Pipe.side_effect = lambda **kwargs: (
            Mock(close=Mock(return_value=None)), Mock(close=Mock(return_value=None)))
        context.Process.side_effect = [owner, target]
        absent = {'query_before': 4201, 'absent': True}
        children = [
            {'index': 0, 'terminated': False, 'exitcode': probe.EXIT_CODE, 'fallback_cleanup': absent},
            {'index': 1, 'terminated': False, 'exitcode': 0, 'result': {'dll_unchanged': True}}]
        ready = [{'ready': {'pid': 31, 'tid': 41, 'created': 51, 'dll_sha256': probe.normal.uia.DLL_HASH}},
                 {'ready': {'pid': 32, 'tid': 42, 'created': 52, 'dll_sha256': probe.normal.uia.DLL_HASH}},
                 {'bound': True}]
        source = {'own': 'hash'}
        with tempfile.TemporaryDirectory(prefix='uia-recovery-own-', dir=directory) as temporary, \
                tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as journals:
            with patch.object(probe.tempfile, 'mkdtemp', return_value=temporary), \
                    patch.object(probe.mp, 'get_context', return_value=context), \
                    patch.object(probe.normal.private, 'receive', side_effect=ready), \
                    patch.object(probe, 'receive_checkpoint', side_effect=lambda pipe, token, kind: {
                        'value': {'created': 1, 'name': probe.normal.session_name(token, 1)}}), \
                    patch.object(probe, 'TargetLease') as lease, \
                    patch.object(probe.normal.private, 'controller'), \
                    patch.object(probe.normal, 'stop_owned_scope', return_value=absent), \
                    patch.object(probe.normal, 'finish_children', return_value=(children, False)) as finish, \
                    patch.object(probe, 'fingerprints', return_value=source), \
                    patch.object(probe.normal.uia, 'uia_hash', return_value=probe.normal.uia.DLL_HASH), \
                    patch.object(probe.shutil, 'rmtree') as remove:
                lease.return_value.close.return_value = True
                result = probe.run_case('created_before_ack_exit', Path(journals), source)
                self.assertEqual(finish.call_args.args[1], 1)  # A bare absence does not skip fallback.
                self.assertFalse(result['passed'])
                self.assertFalse(result['session_cleanup_proven'])
                self.assertTrue(result['cleanup_unknown'])
                self.assertTrue(result['temporary_files_retained'])
                self.assertNotIn('temporary_files_removed', result)
                self.assertEqual(result['error'], 'RuntimeError:external_cleanup_unproven')
                remove.assert_not_called()
                saved = json.loads(next(Path(journals).glob('*-intent.json')).read_text())
                self.assertTrue(saved['result']['cleanup_unknown'])
                self.assertEqual(Path(saved['etl']).parent, Path(temporary))

    def test_eof_requires_actual_owner_stop_and_no_unexpected_worker_error(self):
        for update in ({'cleanup': [{'query_before': 4201, 'absent': True}]},
                       {'error': 'RuntimeError:unexpected'}, {'process_handles_closed': [True, False]}):
            result = self.successful_case('command_eof_other')
            result['owner_finally'].update(update)
            self.assertFalse(probe.case_passed(result))

    def test_killed_target_or_bad_final_dll_hash_cannot_unlock_matrix(self):
        for location, key, value in (('target', 'terminated', True), ('target', 'exitcode', 79),
                                    ('target_result', 'dll_unchanged', False),
                                    ('root', 'dll_unchanged', False)):
            report = self.precheck()
            result = report['cases'][0]
            node = {'target': result['children'][1], 'target_result': result['children'][1]['result'],
                    'root': result}[location]
            node[key] = value
            self.assertFalse(probe.verify_precheck(report, {'own': 'hash'}))

    def test_precheck_requires_exact_stage_source_os_and_precheck_case(self):
        report = self.precheck()
        self.assertTrue(probe.verify_precheck(report, {'own': 'hash'}))
        for key, value in (('fingerprints', {}), ('os', 'other'), ('source_unchanged', False),
                           ('live_diagnostic_ready', True), ('dll_sha256', 'other')):
            altered = copy.deepcopy(report)
            altered[key] = value
            self.assertFalse(probe.verify_precheck(altered, {'own': 'hash'}))
        report['cases'] = [self.successful_case('enabled_exit_other')]
        self.assertFalse(probe.verify_precheck(report, {'own': 'hash'}))

    def test_self_target_never_creates_children_or_session(self):
        with patch.object(probe.tempfile, 'mkdtemp') as temporary, \
                patch.object(probe.mp, 'get_context') as context:
            with self.assertRaisesRegex(RuntimeError, 'not_admitted'):
                probe.run_case('enabled_exit_self', Path('unused'), {})
        temporary.assert_not_called()
        context.assert_not_called()
        self.assertFalse(probe.NOT_ADMITTED['executed'])

    def test_checkpoint_rejects_unknown_token_and_preserves_explicit_failure(self):
        finished = {'token': 'own', 'checkpoint': 'finished', 'normal_ack': False,
                    'value': {'finished': {'incomplete_phase': {'start_status': 183}}}}
        with patch.object(probe.normal.private, 'receive', return_value=finished):
            with self.assertRaises(probe.CheckpointMismatch) as raised:
                probe.receive_checkpoint(Mock(), 'own', 'created')
        self.assertIs(raised.exception.value, finished)
        self.assertEqual(raised.exception.value['value']['finished']['incomplete_phase']['start_status'], 183)

    def test_atomic_checkpoint_does_not_overwrite_previous_evidence(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as directory:
            probe.checkpoint(directory, 'own', 'created', {'checkpoint': 'created'})
            with self.assertRaisesRegex(RuntimeError, 'already_exists'):
                probe.checkpoint(directory, 'own', 'created', {'changed': True})
            self.assertEqual(json.loads((Path(directory) / 'own-created.json').read_text()),
                             {'checkpoint': 'created'})
            self.assertFalse((Path(directory) / 'own-created.json.tmp').exists())

    def test_matrix_stops_after_first_failed_recovery(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as directory, \
                patch.object(probe, 'require_precheck'), patch.object(probe, 'fingerprints', return_value={}), \
                patch.object(probe, 'run_case', return_value={'passed': False}) as run_case:
            result = probe.run('matrix', Path(directory) / 'report.json', {})
        run_case.assert_called_once()
        self.assertFalse(result['remote_owner_recovery_passed'])
        self.assertFalse(result['all_five_cases_verified'])
        self.assertFalse(result['live_diagnostic_ready'])

    def test_missing_precheck_neither_starts_native_nor_consumes_attempt(self):
        with patch.object(probe.sys, 'argv', ['probe', '--run-synthetic', '--stage', 'matrix']), \
                patch.object(probe, 'fingerprints', return_value={}), \
                patch.object(probe, 'require_precheck', side_effect=RuntimeError('missing')), \
                patch.object(probe, 'report_path') as path, patch.object(probe, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                probe.main()
        path.assert_not_called()
        run.assert_not_called()

    def test_default_has_no_native_or_fingerprint_access(self):
        with patch.object(probe.sys, 'argv', ['probe']), patch.object(probe, 'fingerprints') as fingerprints, \
                patch.object(probe, 'run') as run, patch('builtins.print'):
            self.assertEqual(probe.main(), 0)
        fingerprints.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
