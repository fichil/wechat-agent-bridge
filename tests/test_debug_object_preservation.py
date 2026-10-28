"""Pure fixture ownership and original-state contracts; no native calls."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import io
import unittest
from unittest.mock import Mock, patch

from scripts import probe_debug_object_preservation as probe


def state():
    return dict(prior_suspend_count=0, debugger_present=False, thread_created=100,
                debug_registers=[0] * 6)


class OriginalStateTests(unittest.TestCase):
    def test_calibration_retains_failed_mask_hypothesis(self):
        row = dict(state='complete', samples=[dict(requested_dr6=v, observed_dr6=0,
            other_registers_unchanged=True) for v in probe.DR6_SAMPLES],
            original_context_restored=True, pause_balanced=True, initial_threads={'1': state()},
            final_threads={'1': state()}, stable_two_seconds=True,
            source_cleanup=[{'fixture_source_closed': True}], child_cleanup=dict(exitcode=0, terminated=False))
        self.assertFalse(probe.calibration_qualified(row))
        row['samples'] = [dict(requested_dr6=v, observed_dr6=v & 0xe00f,
            other_registers_unchanged=True) for v in probe.DR6_SAMPLES]
        self.assertTrue(probe.calibration_qualified(row))
        row['pause_balanced'] = False
        self.assertFalse(probe.calibration_qualified(row))

    def test_calibration_alone_cannot_start_native(self):
        with patch.object(probe, 'calibrate_registers') as native, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            probe.main(['--calibrate-registers'])
        native.assert_not_called()

    def test_default_and_arbitrary_cli_never_start_target(self):
        with patch.object(probe, 'fingerprints') as hashes, patch.object(probe, 'run_cases') as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for args in (['--run'], ['--pid', '123'], ['--fixture', 'existing_both']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(args)
        hashes.assert_not_called(); run.assert_not_called()

    def test_seed_is_local_slot_one_write_eight_and_distinct_addresses(self):
        self.assertEqual(probe.seed_registers(4096), [4096, 4104, 4112, 4120, 0, 0x900004])
        for address in (0, -8, 4097, True, '4096'):
            with self.assertRaises(RuntimeError):
                probe.seed_registers(address)

    def test_seeded_state_preserves_only_declared_fixture_changes(self):
        identity = dict(main_tid=1, auxiliary_tid=2, marker_address=4096)
        before = {'1': state(), '2': state(), '3': state()}
        current = copy.deepcopy(before)
        current['2']['prior_suspend_count'] = 1
        current['1']['debug_registers'] = [4096, 4104, 4112, 4120, 0xffff07f0, 0x900404]
        probe.check_seeded_states(before, current, identity, True, True)
        for tid, key, value in (('2', 'prior_suspend_count', 0), ('2', 'prior_suspend_count', 2),
                                ('3', 'prior_suspend_count', 1), ('1', 'thread_created', 101),
                                ('1', 'debugger_present', True)):
            changed = copy.deepcopy(current)
            changed[tid][key] = value
            with self.assertRaises(RuntimeError):
                probe.check_seeded_states(before, changed, identity, True, True)
        with self.assertRaises(RuntimeError):
            probe.check_seeded_states(before, dict(current, **{'4': state()}), identity, True, True)

    def test_seed_does_not_ignore_any_event_cause_or_enabled_slot(self):
        expected = probe.seed_registers(4096)
        self.assertTrue(probe.registers_match_seed(expected, expected))
        for position, value in ((4, 1), (4, 0xffff0ff1), (4, 0xffffe7f0), (5, 0x900405), (1, 9999)):
            changed = list(expected)
            changed[position] = value
            self.assertFalse(probe.registers_match_seed(changed, expected))

    def test_fixture_write_balances_its_pause_even_if_context_changed(self):
        thread = Mock(handle=5)
        thread.kernel.SuspendThread.return_value = 0
        thread.kernel.ResumeThread.return_value = 1
        thread.context.return_value = (None, None, [123] + [0] * 5)
        with self.assertRaisesRegex(RuntimeError, 'registers_changed'):
            probe.balanced_register_write(thread, [0] * 6, probe.seed_registers(4096))
        thread.set_registers.assert_not_called()
        thread.kernel.ResumeThread.assert_called_once_with(5)

    def test_failed_suspend_never_resumed_and_prior_pause_never_consumed(self):
        thread = Mock(handle=5)
        thread.kernel.SuspendThread.return_value = 0xffffffff
        with self.assertRaisesRegex(RuntimeError, 'suspend_failed'):
            probe.balanced_register_write(thread, [0] * 6, probe.seed_registers(4096))
        thread.kernel.ResumeThread.assert_not_called()
        thread.kernel.SuspendThread.return_value = 1
        thread.kernel.ResumeThread.return_value = 2
        with self.assertRaisesRegex(RuntimeError, 'main_already_paused'):
            probe.balanced_register_write(thread, [0] * 6, probe.seed_registers(4096))
        thread.set_registers.assert_not_called()
        thread.kernel.ResumeThread.assert_called_once_with(5)

    def test_cleanup_waits_for_full_original_state_verification(self):
        fixture = probe.Fixture('existing_both')
        fixture.baseline = {'1': state(), '2': state()}
        fixture.main = Mock()
        fixture.bound = Mock()
        with self.assertRaisesRegex(RuntimeError, 'requires_exact_recovery'):
            fixture.finish({'1': state()})
        fixture.main.set_registers.assert_not_called()
        fixture.bound.kernel.ResumeThread.assert_not_called()

    def test_qualification_requires_cleanup_ack_and_normal_source_exit(self):
        states = {'1': state()}
        row = dict(passed=True, initial_threads=states, final_threads=states, before_fixture=states,
            fixture_cleanup=dict(original_state_preserved=True, seeded_state_removed=True, after_fixture=states),
            child_cleanup=[dict(exitcode=17), dict(exitcode=0, terminated=False)],
            fixture_source_cleanup=[{'fixture_source_closed': True}])
        self.assertTrue(probe.qualified(row))
        for field, value in (('passed', False), ('final_threads', {}), ('fixture_source_cleanup', []),
            ('child_cleanup', [dict(exitcode=17), dict(exitcode=-15, terminated=True)]),
            ('fixture_cleanup', dict(original_state_preserved=True, seeded_state_removed=False, after_fixture=states))):
            self.assertFalse(probe.qualified(dict(row, **{field: value})))

    def test_first_regression_failure_stops_before_fixture_native_setup(self):
        with patch.object(probe.recovery, 'run_case', return_value={'passed': False}) as run, \
                patch.object(probe, 'Fixture') as fixture, redirect_stdout(io.StringIO()):
            self.assertEqual(len(probe.run_cases(None)), 1)
        run.assert_called_once()
        fixture.assert_not_called()

    def test_first_fixture_failure_stops_later_cases(self):
        responses = [{'passed': True}] * 6 + [{'passed': False}]
        with patch.object(probe.recovery, 'run_case', side_effect=responses) as run, \
                redirect_stdout(io.StringIO()):
            results = probe.run_cases(None)
        self.assertEqual(len(results), 7)
        self.assertEqual(run.call_count, 7)


if __name__ == '__main__':
    unittest.main()
