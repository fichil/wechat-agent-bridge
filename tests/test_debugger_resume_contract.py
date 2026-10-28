"""Offline contracts only: no child process, native DLL or debugger is started."""
from contextlib import redirect_stdout
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import probe_debugger_resume_flag as probe


def state(count=0, debugger=False, registers=None):
    return {'prior_suspend_count': count, 'debugger_present': debugger, 'thread_created': 7,
            'debug_registers': [0] * 6 if registers is None else registers}


class ResumeFlagContracts(unittest.TestCase):
    def test_default_cli_does_not_initialize_native_or_spawn(self):
        with patch.object(probe, 'kernel_api') as native, \
                patch.object(probe, 'fingerprints') as fingerprints, \
                patch.object(probe, 'run_cases') as cases, \
                patch.object(probe.mp, 'get_context') as context, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(probe.main([]), 0)
        for function in (native, fingerprints, cases, context):
            function.assert_not_called()
        self.assertIn('"state": "prepared"', output.getvalue())
        self.assertIn('"live_eligible": false', output.getvalue())

    def test_no_arbitrary_target_or_stage_cli(self):
        for argument in ('--pid', '--stage', '--breakpoint', '--attach'):
            with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), \
                    self.assertRaises(SystemExit):
                probe.main([argument, '42'])

    def test_report_never_overwrites_prior_evidence(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT) as directory:
            root = Path(directory)
            destination = root / '.local/live-acceptance/old.json'
            destination.parent.mkdir(parents=True)
            destination.write_text('preserve', encoding='utf-8')
            with patch.object(probe, 'ROOT', root), patch.object(probe, 'fingerprints') as fingerprints:
                with self.assertRaises(FileExistsError):
                    probe.main(['--run-synthetic', '--output', str(destination)])
            fingerprints.assert_not_called()
            self.assertEqual(destination.read_text(encoding='utf-8'), 'preserve')

    def test_first_failure_stops_before_negative_control(self):
        runner = Mock(return_value={'case': probe.CASES[0], 'passed': False})
        self.assertEqual(len(probe.run_cases(None, runner=runner)), 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.args[1], 'zero_baseline')

    def test_exactly_two_cases_and_no_dr_followup(self):
        runner = Mock(side_effect=lambda _ctx, name, _progress: {'case': name, 'passed': True})
        self.assertEqual([x['case'] for x in probe.run_cases(None, runner=runner)], list(probe.CASES))
        self.assertEqual(runner.call_count, 2)

    def test_baseline_only_explicit_auxiliary_is_paused(self):
        probe.check_baseline({'1': state(), '2': state(1)}, 2)
        for invalid in ({'1': state(1), '2': state(1)}, {'1': state(), '2': state()},
                        {'1': state()}, {'1': state(), '2': state(2)}):
            with self.assertRaises(RuntimeError):
                probe.check_baseline(invalid, 2)

    def test_baseline_rejects_debugger_and_any_dr_residue(self):
        for invalid in (state(debugger=True), state(registers=[0, 0, 0, 0, 0, 1])):
            with self.assertRaisesRegex(RuntimeError, 'baseline_not_exact'):
                probe.check_baseline({'1': invalid})

    def test_orphan_requires_debugger_detached_and_exact_one_extra_suspend(self):
        initial = {'1': state(), '2': state(1)}
        probe.check_orphan(initial, {'1': state(1), '2': state(2), '3': state(1)})
        for invalid in ({'1': state(1), '2': state(2, debugger=True)},
                        {'1': state(1), '2': state(3)},
                        {'1': state(1), '2': state(2, registers=[1] + [0] * 5)},
                        {'1': state(1)}):
            with self.assertRaises(RuntimeError):
                probe.check_orphan(initial, invalid)

    def test_restoration_rejects_consumed_original_pause_new_thread_and_dr(self):
        initial = {'1': state(), '2': state(1)}
        self.assertTrue(probe.restored(initial, copy.deepcopy(initial)))
        for final in ({'1': state(), '2': state()},
                      dict(initial, **{'3': state()}),
                      {'1': state(), '2': dict(state(1), thread_created=8)},
                      {'1': state(), '2': state(1, registers=[0] * 5 + [1])}):
            self.assertFalse(probe.restored(initial, final))
        self.assertFalse(probe.restored({}, {}))

    def test_reused_tid_of_other_process_is_never_suspended(self):
        bound = Mock(identity={'pid': 42}, handle=99)
        bound.kernel.OpenThread.return_value = 50
        bound.kernel.GetProcessIdOfThread.return_value = 43
        with self.assertRaisesRegex(RuntimeError, 'inspection_thread_not_owned'):
            probe.thread_state(bound, 123)
        bound.kernel.SuspendThread.assert_not_called()
        bound.kernel.GetThreadContext.assert_not_called()
        bound.kernel.ResumeThread.assert_not_called()
        bound.kernel.CloseHandle.assert_called_once_with(50)

    def test_inspection_balances_only_its_one_suspend_after_context_failure(self):
        bound = Mock(identity={'pid': 42}, handle=99)
        bound.kernel.OpenThread.return_value = 50
        bound.kernel.GetProcessIdOfThread.return_value = 42
        bound.kernel.WaitForSingleObject.return_value = 258
        bound.kernel.SuspendThread.return_value = 2
        bound.kernel.ResumeThread.return_value = 3
        bound.kernel.GetThreadContext.return_value = False
        with patch.object(probe, 'thread_creation_time', return_value=7), \
                self.assertRaisesRegex(RuntimeError, 'owned_thread_context_failed'):
            probe.thread_state(bound, 123)
        bound.kernel.SuspendThread.assert_called_once_with(50)
        bound.kernel.ResumeThread.assert_called_once_with(50)
        bound.kernel.CloseHandle.assert_called_once_with(50)

    def test_inspection_does_not_resume_after_failed_suspend(self):
        bound = Mock(identity={'pid': 42}, handle=99)
        bound.kernel.OpenThread.return_value = 50
        bound.kernel.GetProcessIdOfThread.return_value = 42
        bound.kernel.WaitForSingleObject.return_value = 258
        bound.kernel.SuspendThread.return_value = 0xffffffff
        with patch.object(probe, 'thread_creation_time', return_value=7), \
                self.assertRaisesRegex(RuntimeError, 'inspection_suspend_failed'):
            probe.thread_state(bound, 123)
        bound.kernel.ResumeThread.assert_not_called()

    def test_bound_handle_rejects_pid_mismatch_before_open(self):
        kernel = Mock()
        with self.assertRaisesRegex(RuntimeError, 'owned_pid_mismatch'):
            probe.BoundProcess({'pid': 42, 'created': 7}, Mock(pid=43), kernel)
        kernel.OpenProcess.assert_not_called()

    def test_bound_handle_rejects_creation_mismatch_and_closes(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 99
        kernel.WaitForSingleObject.return_value = 258
        with patch.object(probe, 'creation_time', return_value=8), \
                self.assertRaisesRegex(RuntimeError, 'identity_or_liveness_changed'):
            probe.BoundProcess({'pid': 42, 'created': 7}, kernel=kernel)
        kernel.CloseHandle.assert_called_once_with(99)

    def test_bound_handle_detects_exit_without_reopening_pid(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 99
        kernel.WaitForSingleObject.side_effect = [258, 0]
        with patch.object(probe, 'creation_time', return_value=7):
            bound = probe.BoundProcess({'pid': 42, 'created': 7}, kernel=kernel)
            with self.assertRaisesRegex(RuntimeError, 'identity_or_liveness_changed'):
                bound.check()
            bound.close()
        kernel.OpenProcess.assert_called_once()

    def test_rescue_uses_only_0x10_and_waits_for_target_event(self):
        connection = Mock()
        connection.recv.return_value = 'go'
        engine = Mock()
        engine.breakpoints = []
        engine.process_id.return_value = 42
        engine.events.kind, engine.events.exception = 'exception', 0x80000003
        bindings = [Mock(), Mock()]
        with patch.object(probe, 'kernel_api'), \
                patch.object(probe, 'self_identity', return_value={'pid': 50, 'created': 8}), \
                patch.object(probe, 'BoundProcess', side_effect=bindings), \
                patch.object(probe, 'DebugEngine', return_value=engine), \
                patch.object(probe, '_call') as call:
            probe.engine_child(connection, {'pid': 42, 'created': 7}, {'pid': 43, 'created': 9}, True)
        self.assertEqual(call.call_args.args[3], (0, 42, 0x10))
        engine.wait.assert_called_once_with(100)
        engine.add_execution_breakpoint.assert_not_called()
        engine.detach.assert_called_once()
        engine.close.assert_called_once()
        result = connection.send.call_args_list[-1].args[0]['worker_result']
        self.assertEqual(result, {'rescue_detached': True, 'handles_closed': [True, True]})

    def test_worker_second_identity_failure_closes_first_without_engine(self):
        connection = Mock()
        connection.recv.return_value = 'go'
        first = Mock()
        with patch.object(probe, 'kernel_api'), patch.object(probe, 'self_identity', return_value={}), \
                patch.object(probe, 'BoundProcess', side_effect=[first, RuntimeError('wrong_anchor')]), \
                patch.object(probe, 'DebugEngine') as engine:
            probe.engine_child(connection, {'pid': 42}, {'pid': 43}, True)
        first.close.assert_called_once()
        engine.assert_not_called()
        self.assertEqual(connection.send.call_args_list[-1].args[0]['worker_result']['error'], 'wrong_anchor')

    def test_not_started_process_is_never_joined_or_killed(self):
        process = Mock(pid=None)
        self.assertTrue(probe.terminate_owned(process)['dead'])
        process.join.assert_not_called()
        process.terminate.assert_not_called()

    def test_failed_termination_is_not_reported_dead(self):
        process = Mock(pid=42, exitcode=None)
        process.is_alive.return_value = True
        result = probe.terminate_owned(process)
        self.assertFalse(result['dead'])
        process.terminate.assert_called_once()

    def test_start_failure_closes_pipe_and_preserves_failure(self):
        ctx = Mock()
        parent, child = Mock(), Mock()
        ctx.Pipe.return_value = (parent, child)
        ctx.Process.return_value.pid = None
        ctx.Process.return_value.start.side_effect = RuntimeError('start_failed')
        with patch.object(probe, 'kernel_api'):
            result = probe.run_case(ctx, 'zero_baseline')
        self.assertFalse(result['passed'])
        self.assertEqual(result['error'], 'start_failed')
        child.close.assert_called()
        parent.close.assert_called()
        ctx.Process.return_value.join.assert_not_called()


if __name__ == '__main__':
    unittest.main()
