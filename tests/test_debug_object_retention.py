"""Pure contracts; native debugger experiments remain explicit opt-in."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import ctypes as c
import io
import unittest
from unittest.mock import Mock, patch

from scripts import probe_debug_object_retention as probe


def bound_port():
    bound = Mock(handle=22)
    def presence(_handle, output):
        c.cast(output, c.POINTER(c.c_int))[0] = 1
        return 1
    bound.kernel.CheckRemoteDebuggerPresent.side_effect = presence
    bound.kernel.CloseHandle.return_value = 1
    return bound


def query_result(status=0, size=8, handle=44):
    def query(_process, info_class, output, output_size, length):
        if info_class != 30 or output_size != 8:
            raise AssertionError('unexpected_native_class')
        c.cast(output, c.POINTER(c.c_void_p))[0] = handle
        c.cast(length, c.POINTER(c.c_uint32))[0] = size
        return status
    return Mock(side_effect=query)


class DebugObjectRetentionTests(unittest.TestCase):
    def test_default_and_external_pid_never_enter_native(self):
        with patch.object(probe, 'fingerprints') as fingerprints, \
                patch.object(probe, 'run_case') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for args in (['--pid', '42'], ['--run'], ['--stage', 'attached']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(args)
        fingerprints.assert_not_called()
        run.assert_not_called()

    def test_changed_bound_identity_prevents_query(self):
        bound, query = bound_port(), query_result()
        bound.check.side_effect = RuntimeError('identity_changed')
        with self.assertRaisesRegex(RuntimeError, 'identity_changed'):
            probe.retain_object(bound, query)
        query.assert_not_called()

    def test_no_debugger_prevents_private_query(self):
        bound, query = bound_port(), query_result()
        bound.kernel.CheckRemoteDebuggerPresent.side_effect = None
        bound.kernel.CheckRemoteDebuggerPresent.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'debugger_missing'):
            probe.retain_object(bound, query)
        query.assert_not_called()

    def test_query_shape_and_handle_ownership(self):
        bound, query = bound_port(), query_result()
        self.assertEqual(probe.retain_object(bound, query), 44)
        self.assertEqual(bound.check.call_count, 2)
        bound.kernel.CloseHandle.assert_not_called()
        bound.kernel.SuspendThread.assert_not_called()
        bound.kernel.ResumeThread.assert_not_called()
        bound.kernel.SetThreadContext.assert_not_called()

    def test_bad_query_closes_returned_handle_but_never_invalid_handle(self):
        for status, size, handle in ((-1, 8, 44), (0, 4, 44), (0, 8, None)):
            bound = bound_port()
            with self.assertRaisesRegex(RuntimeError, 'query_failed'):
                probe.retain_object(bound, query_result(status, size, handle))
            self.assertEqual(bound.kernel.CloseHandle.call_count, int(bool(handle)))

    def test_identity_change_after_query_closes_handle(self):
        bound = bound_port()
        bound.check.side_effect = [None, RuntimeError('identity_changed')]
        with self.assertRaisesRegex(RuntimeError, 'identity_changed'):
            probe.retain_object(bound, query_result())
        bound.kernel.CloseHandle.assert_called_once()

    def test_retention_does_not_substitute_for_full_restoration(self):
        state = {'11': dict(prior_suspend_count=0, debug_registers=[0] * 6)}
        row = dict(mode='crash', state='complete', all_children_exited=True, handles_closed=True,
                   retained_before_exit=True, owner_exit_confirmed=True, owner_exitcode=17,
                   owner_terminated=False, initial_threads=state, final_threads=copy.deepcopy(state),
                   stable_two_seconds=True, debugger_after_close=False, records=[], cleanup_errors=[])
        self.assertTrue(probe.case_qualified(row))
        for field, value in (('handles_closed', False), ('owner_exit_confirmed', False),
                             ('debugger_after_close', True), ('stable_two_seconds', False),
                             ('owner_exitcode', 0), ('initial_threads', {}),
                             ('cleanup_errors', ['close_failed'])):
            self.assertFalse(probe.case_qualified(dict(row, **{field: value})))
        changed = copy.deepcopy(row)
        changed['final_threads']['11']['prior_suspend_count'] = 1
        self.assertFalse(probe.case_qualified(changed))
        changed = copy.deepcopy(row)
        changed['final_threads']['11']['debug_registers'][0] = 123
        self.assertFalse(probe.case_qualified(changed))
        self.assertFalse(probe.case_qualified(dict(row, records=[{'cleanup_error': 'unknown'}])))

    def test_exit_mode_is_evidence_not_intent(self):
        self.assertTrue(probe.expected_exit('normal', 0, False))
        self.assertTrue(probe.expected_exit('crash', 17, False))
        self.assertTrue(probe.expected_exit('watchdog', -15, True))
        for args in (('normal', 0, True), ('crash', 17, True), ('watchdog', None, True),
                     ('watchdog', 0, True), ('watchdog', -15, False), ('unrecognized', 17, False)):
            self.assertFalse(probe.expected_exit(*args))


if __name__ == '__main__':
    unittest.main()
