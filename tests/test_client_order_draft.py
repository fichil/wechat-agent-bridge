"""Newly authorized fixed case: no native client or paid API access."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_path=sys.path[:]
try:
    from scripts import probe_client_order_draft as cli
finally:
    sys.path[:]=_path
from wechat_agent_bridge.adapters.focus_diagnostics import DiagnosticLedger

p=cli.original
A='11111111-2222-3333-4444-555555555555'
B='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


class ClientOrderTests(unittest.TestCase):
    def test_default_does_not_enable_or_create_trial(self):
        with patch.object(p,'run') as run,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main([]),0)
        self.assertEqual(json.loads(output.getvalue())['state'],'disabled')
        run.assert_not_called()

    def test_only_known_case_and_valid_desktop_can_select_new_ledger(self):
        for case,guid in [('other',A),(p.CLIENT_ORDER_TRIAL,None),(p.CLIENT_ORDER_TRIAL,'invalid')]:
            with self.subTest(case=case,guid=guid),self.assertRaises(p.ConsoleError):
                p.ledger_file(guid,approved_case=case)
        first=p.ledger_file(A,approved_case=p.CLIENT_ORDER_TRIAL)
        self.assertEqual(first,p.ledger_file(B,approved_case=p.CLIENT_ORDER_TRIAL))
        self.assertNotEqual(first,p.ledger_file(A))
        self.assertNotEqual(first,p.ledger_file(None))

    def test_cli_passes_fixed_case_without_arbitrary_path_option(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            with (patch.object(p,'DIRECTORY',Path(name)),
                  patch.object(p,'run',return_value={'state':'preflight_verified','attempted':False}) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(cli.main(['--preflight','--virtual-desktop',A]),0)
        self.assertEqual(run.call_args.kwargs,dict(taskview_desktop_id=A,approved_case=p.CLIENT_ORDER_TRIAL))
        self.assertFalse(run.call_args.args[0].run)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            cli.main(['--ledger','different.sqlite3'])

    def test_consumed_case_survives_restart_and_preserves_all_old_ledgers(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name,patch.object(p,'DIRECTORY',Path(name)):
            old=[Path(name)/x for x in ('diagnostics.sqlite3','taskview-desktop2-20261026.sqlite3','quota.sqlite3')]
            for i,path in enumerate(old):path.write_bytes(('synthetic-old-'+str(i)).encode())
            hashes={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in old}
            ledger=DiagnosticLedger(p.ledger_file(A,approved_case=p.CLIENT_ORDER_TRIAL))
            request=ledger.reserve('synthetic-fingerprint')
            ledger.consume_draft(request,'synthetic-fingerprint');ledger.finish(request,'failed');ledger.close()
            reopened=DiagnosticLedger(p.ledger_file(B,approved_case=p.CLIENT_ORDER_TRIAL))
            try:
                self.assertTrue(reopened.status()['attempt_used'])
                with self.assertRaises(p.ConsoleError):reopened.reserve('another-fingerprint')
            finally:reopened.close()
            self.assertEqual(hashes,{path:hashlib.sha256(path.read_bytes()).hexdigest() for path in old})
