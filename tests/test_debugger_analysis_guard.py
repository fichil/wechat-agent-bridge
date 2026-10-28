"""Protocol/qualification only; default tests never attach or create targets."""
import contextlib
import io
import sys
import unittest
from unittest.mock import patch

_path = sys.path[:]
try:
    from scripts import probe_debugger_analysis_guard as p
finally:
    sys.path[:] = _path
del _path


def outcome(mode):
    return dict(mode=mode, ready=True, exited=True, complete=mode=='normal',
                exitcode={'normal':0,'crash':17,'watchdog':1}[mode],
                terminated=mode=='watchdog', timed_out=mode=='watchdog', process_handle_closed=True)


class AnalysisGuardTests(unittest.TestCase):
    def test_default_is_disabled_before_any_native_work(self):
        with patch.object(p, 'fingerprints', side_effect=AssertionError('native')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main([]), 0)

    def test_external_targets_and_short_flag_are_rejected(self):
        for args in (['--pid','1'], ['--run']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                p.main(args)

    def test_expected_failure_requires_observed_terminal_state(self):
        for mode in p.MODES:
            value=outcome(mode)
            self.assertTrue(p.observed_expected_failure(value))
            for change in (dict(exited=False), dict(ready=False), dict(process_handle_closed=False),
                           dict(mode='owner_crash')):
                self.assertFalse(p.observed_expected_failure(dict(value,**change)))

    def test_guard_cannot_claim_success_without_full_target_restoration(self):
        case=dict(passed=True, records=[{'analysis_isolation':outcome('crash')},{'detached':True}],
                  all_threads_checked=True, stable_two_seconds=True,
                  initial_threads={'1':{'suspend':1,'dr7':7}}, final_threads={'1':{'suspend':1,'dr7':7}})
        self.assertTrue(p.case_passed(case))
        for change in (dict(passed=False),dict(all_threads_checked=False),dict(stable_two_seconds=False),
                       dict(final_threads={'1':{'suspend':0,'dr7':0}}),dict(records=[])):
            self.assertFalse(p.case_passed(dict(case,**change)))

    def test_normal_exit_is_not_accepted_as_a_crash_or_timeout(self):
        value=outcome('crash');value['exitcode']=0
        self.assertFalse(p.observed_expected_failure(value))
        value=outcome('watchdog');value['timed_out']=False
        self.assertFalse(p.observed_expected_failure(value))

    def test_analysis_completion_is_acknowledged_only_after_ready(self):
        state=dict(ready=False,complete=False)
        with self.assertRaisesRegex(RuntimeError,'analysis_protocol_invalid'):
            p.accept_message(state,{'complete':'n'},'n')
        self.assertIsNone(p.accept_message(state,{'ready':'n'},'n'))
        self.assertEqual(p.accept_message(state,{'complete':'n'},'n'),{'ack':'n'})
        for message in ({'complete':'n'},{'ready':'n'},{'ready':'other'},{'command':'resume_all'}):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError,'analysis_protocol_invalid'):
                p.accept_message(state,message,'n')
