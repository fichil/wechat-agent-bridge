"""One-use Task View CLI and durable-budget tests; all data is synthetic."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_discovery_path = sys.path[:]
try:
    from scripts import probe_taskview_draft as cli
finally:
    sys.path[:] = _discovery_path
del _discovery_path
from wechat_agent_bridge.adapters.focus_diagnostics import DiagnosticLedger
from wechat_agent_bridge.console_models import ConsoleError


GUID_A = '11111111-2222-3333-4444-555555555555'
GUID_B = '66666666-7777-8888-9999-aaaaaaaaaaaa'
probe = cli.original


class TaskViewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='taskview-unit-', dir=probe.ROOT / '.local')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch_directory = patch.object(probe, 'DIRECTORY', self.directory)
        self.patch_directory.start()
        self.addCleanup(self.patch_directory.stop)

    def invoke(self, flags, *, report=None, error=None):
        with (patch.object(probe, 'run', return_value=report, side_effect=error) as run,
              contextlib.redirect_stdout(io.StringIO()) as output):
            code = cli.main(flags)
        artifacts = list(self.directory.glob('taskview-desktop2-*.json'))
        return code, json.loads(output.getvalue()), run, artifacts

    def test_default_and_idle_flag_alone_are_disabled_without_runner_or_files(self):
        for flags in ([], ['--operator-idle']):
            with self.subTest(flags=flags):
                code, output, run, artifacts = self.invoke(flags)
                self.assertEqual(code, 0)
                self.assertEqual(output, {'state': 'disabled', 'send_enabled': False})
                run.assert_not_called()
                self.assertEqual(artifacts, [])

    def test_missing_desktop_id_rejects_before_runner(self):
        code, output, run, artifacts = self.invoke(['--preflight'])
        self.assertEqual(code, 1)
        self.assertEqual(output['reason'], 'taskview_desktop_id_required')
        run.assert_not_called()
        self.assertEqual(len(artifacts), 1)

    def test_explicit_preflight_passes_guid_without_changing_run_mode(self):
        code, output, run, artifacts = self.invoke(['--preflight', '--virtual-desktop', GUID_A],
            report={'state': 'preflight_verified', 'attempted': False, 'send_invoked': False,
                    'patch_restored': True})
        self.assertEqual(code, 0)
        args = run.call_args.args[0]
        self.assertTrue(args.preflight)
        self.assertFalse(args.run)
        self.assertFalse(args.operator_idle)
        self.assertEqual(run.call_args.kwargs, {'taskview_desktop_id': GUID_A})
        self.assertEqual(len(artifacts), 1)
        self.assertFalse(output['send_invoked'])

    def test_unknown_attempt_is_retained_in_unique_report(self):
        report = {'state': 'unknown', 'reason': 'background_focus_changed',
            'attempted': True, 'draft_may_remain': True, 'send_invoked': False,
            'patch_restored': True}
        code, output, run, artifacts = self.invoke(['--run', '--operator-idle', '--virtual-desktop', GUID_A],
            report=report)
        self.assertEqual(code, 1)
        self.assertTrue(run.call_args.args[0].run)
        self.assertTrue(run.call_args.args[0].operator_idle)
        self.assertEqual(json.loads(artifacts[0].read_text(encoding='utf-8')), report)
        self.assertTrue(output['attempted'])
        self.assertFalse(output['send_invoked'])

    def test_preparation_failure_keeps_fixed_reason_without_running_fallback(self):
        code, output, run, artifacts = self.invoke(['--run', '--virtual-desktop', GUID_A],
            error=ConsoleError('operator_started_idle_window_required'))
        self.assertEqual(code, 1)
        self.assertEqual(output['reason'], 'operator_started_idle_window_required')
        self.assertFalse(output['attempted'])
        run.assert_called_once()
        self.assertEqual(len(artifacts), 1)

    def test_run_and_preflight_cannot_be_combined(self):
        with patch.object(probe, 'run') as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['--run', '--preflight', '--virtual-desktop', GUID_A])
        run.assert_not_called()


class TaskViewLedgerTests(unittest.TestCase):
    def test_invalid_guid_never_selects_a_campaign_file(self):
        for value in ('', 'not-a-guid', '00000000-0000-0000-0000-000000000000',
                      '{' + GUID_A + '}', GUID_B.upper(), 123):
            with self.subTest(value=value), self.assertRaises(ConsoleError):
                probe.ledger_file(value)

    def test_different_desktops_share_fixed_file_and_original_path_is_unchanged(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as name, \
                patch.object(probe, 'DIRECTORY', Path(name)):
            self.assertEqual(probe.ledger_file(GUID_A), probe.ledger_file(GUID_B))
            self.assertEqual(probe.ledger_file(GUID_A), Path(name) / 'taskview-desktop2-20261026.sqlite3')
            self.assertEqual(probe.ledger_file(None), Path(name) / 'diagnostics.sqlite3')

    def test_restart_and_changed_guid_cannot_reuse_attempt_or_touch_old_quotas(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT / '.local') as name, \
                patch.object(probe, 'DIRECTORY', Path(name)):
            original = DiagnosticLedger(probe.ledger_file(None))
            try:
                request = original.reserve('synthetic-original-process')
                original.consume_draft(request, 'synthetic-original-process')
                original.finish(request, 'failed')
            finally:
                original.close()
            # This file stands for the independent send/API quota database; no
            # diagnostic code may open, replace or migrate its bytes.
            quota = Path(name) / 'quota.sqlite3'
            quota.write_bytes(b'synthetic-send-three-api-ten-no-reset')
            preserved = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (probe.ledger_file(None), quota)}

            first = DiagnosticLedger(probe.ledger_file(GUID_A))
            try:
                request = first.reserve('synthetic-new-process')
                first.consume_draft(request, 'synthetic-new-process')
                first.finish(request, 'failed')
            finally:
                first.close()
            restarted = DiagnosticLedger(probe.ledger_file(GUID_B))
            try:
                self.assertTrue(restarted.status()['attempt_used'])
                self.assertTrue(restarted.status()['draft_attempted'])
                with self.assertRaisesRegex(ConsoleError, 'diagnostic_attempt_already_used'):
                    restarted.reserve('different-process-or-desktop')
            finally:
                restarted.close()
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in preserved}, preserved)


if __name__ == '__main__':
    unittest.main()
