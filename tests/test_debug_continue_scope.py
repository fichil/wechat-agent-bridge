"""Scope experiment assertions without attaching or loading native DLLs."""
from contextlib import redirect_stdout, redirect_stderr
import io
import unittest
from unittest.mock import Mock, patch
from scripts import probe_debug_continue_scope as probe


class ContinueScopeTests(unittest.TestCase):
    def test_default_and_external_target_options_do_not_run_native(self):
        with patch.object(probe, 'fingerprints') as hashes, patch.object(probe, 'run_case') as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for args in (['--run'], ['--pid', '1']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(args)
        hashes.assert_not_called(); run.assert_not_called()

    def test_bound_target_and_tid_required_before_native_continue(self):
        bound = Mock(identity=dict(pid=1, main_tid=2))
        native = Mock()
        for event in (dict(pid=3, tid=2), dict(pid=1, tid=3)):
            with self.assertRaises(RuntimeError):
                probe.call_continue(bound, 7, native, event, probe.recovery.CONTINUE)
        native.NtDebugContinue.assert_not_called()

    def test_new_exception_must_be_distinct_and_exact(self):
        identity = dict(pid=1, main_tid=2)
        event = dict(state=6, pid=1, tid=2, code=probe.CUSTOM_EXCEPTION, address=99, first_chance=1)
        probe.check_new_exception(event, identity)
        for key, value in (('pid', 2), ('tid', 3), ('code', probe.recovery.SINGLE_STEP),
                           ('state', 8), ('first_chance', 0), ('address', 0)):
            with self.assertRaises(RuntimeError):
                probe.check_new_exception(dict(event, **{key: value}), identity)

    def test_rejection_status_alone_does_not_prove_event_was_preserved(self):
        good = dict(state='complete', case='unread_new_exception', second_continue_status=probe.NO_REPORTED_EVENT,
            second_continue_count=1, no_wait_between_continues=True, initial_threads={'2': 3}, final_threads={'2': 3},
            stable_two_seconds=True, debugger_after=False, handles_closed=True, cleanup_errors=[],
            child_cleanup=dict(exitcode=0, terminated=False), queue_signalled=True,
            new_exception_remained_pending=True, target_handler_count=1)
        self.assertTrue(probe.scope_qualified(good))
        for key, value in (('second_continue_status', 0), ('second_continue_count', 2),
                           ('no_wait_between_continues', False), ('queue_signalled', False),
                           ('new_exception_remained_pending', False), ('target_handler_count', 0)):
            self.assertFalse(probe.scope_qualified(dict(good, **{key: value})))


if __name__ == '__main__':
    unittest.main()
