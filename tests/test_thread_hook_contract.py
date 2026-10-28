import contextlib
import ctypes
import io
import unittest
from unittest.mock import Mock, patch

from scripts import test_thread_hook as probe


class ThreadHookContractTests(unittest.TestCase):
    def test_default_and_rejected_target_arguments_never_open_windows(self):
        with patch.object(probe, 'Experiment', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.main(['--run-synthetic', '--pid', '123'])

    def test_shared_contract_has_explicit_widths_and_correct_offsets(self):
        self.assertEqual(ctypes.sizeof(probe.Contract), 216)
        self.assertEqual(probe.Contract.controller_created.offset, 32)
        self.assertEqual(probe.Contract.deadline.offset, 72)
        self.assertEqual(probe.Contract.phase.offset, 80)
        self.assertEqual(probe.Contract.wake_result.offset, 184)

    def test_missing_manifest_entries_do_not_qualify_old_binaries(self):
        with patch.object(probe.json, 'loads', return_value=dict(synthetic_only=True, binaries=[], sources={})), \
             patch.object(probe.Path, 'read_text', return_value='{}'), \
             self.assertRaisesRegex(RuntimeError, 'manifest_incomplete'):
            probe.validate_build()

    def test_non_synthetic_build_rejected_before_launch(self):
        with patch.object(probe.json, 'loads', return_value=dict(synthetic_only=False)), \
             patch.object(probe.Path, 'read_text', return_value='{}'), \
             self.assertRaisesRegex(RuntimeError, 'synthetic_build_required'):
            probe.validate_build()

    def test_native_input_change_stops_wait(self):
        experiment = probe.Experiment.__new__(probe.Experiment)
        experiment.initial = ('owned', 100, 1)
        experiment.report = dict(samples=0, focus_changed=False)
        with patch.object(probe, 'input_snapshot', return_value=('changed', 101, 2)), \
             self.assertRaisesRegex(RuntimeError, 'input_environment_changed'):
            experiment.wait(lambda: False)
        self.assertTrue(experiment.report['focus_changed'])

    def test_cleanup_requires_all_resources_and_only_intended_crash(self):
        record = dict(unmapped=True, mapping_closed=True, desktop_closed=True,
                      children=[dict(role=role, exitcode=0, forced=False) for role in ('owner', 'target', 'probe')])
        self.assertTrue(probe.cleanup_complete(record, 'normal'))
        record['children'][0]['exitcode'] = 1
        self.assertFalse(probe.cleanup_complete(record, 'normal'))
        self.assertTrue(probe.cleanup_complete(record, 'owner_crash_pending'))
        record['children'][1]['exitcode'] = 1
        self.assertFalse(probe.cleanup_complete(record, 'owner_crash_pending'))
        record['children'][1]['exitcode'] = 0
        record['desktop_closed'] = False
        self.assertFalse(probe.cleanup_complete(record, 'owner_crash_pending'))
