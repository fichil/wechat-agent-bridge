import contextlib
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from wechat_agent_bridge.console_models import ConsoleError
from scripts import probe_console_identity as runner


class ConsoleIdentityRunnerTests(unittest.TestCase):
    def test_default_never_starts_console_or_native_probe(self):
        with patch.object(runner,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main([]),0)
            run.assert_not_called()
    def test_serve_flag_alone_does_not_start_or_open_browser(self):
        with patch.object(runner,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(['--serve-console']),0)
            run.assert_not_called()
    def test_worker_exit_requires_terminal_handle_not_only_cleanup_return(self):
        self.assertTrue(runner.worker_exit_evidence(None)['worker_exited'])
        self.assertEqual(runner.worker_exit_evidence(SimpleNamespace(is_alive=lambda:False,exitcode=0))['worker_exit_code'],0)
        for process in (SimpleNamespace(is_alive=lambda:True,exitcode=None),
                        SimpleNamespace(is_alive=lambda:False,exitcode=None)):
            with self.assertRaises(ConsoleError):runner.worker_exit_evidence(process)
    def test_existing_ciphertexts_audit_detects_changed_history_not_expected_patch_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.sqlite3'
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('CREATE TABLE records(bucket,key,payload)')
                db.executemany('INSERT INTO records VALUES(?,?,?)',[
                    ('private_baselines','one',b'synthetic-history'),('binding','account',b'synthetic-epoch'),
                    ('patch','pending',b'synthetic-patch')])
            before=runner.original_records(path)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('UPDATE records SET payload=? WHERE bucket=?',(b'changed','patch'))
            self.assertEqual(runner.original_records(path),before)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('UPDATE records SET payload=? WHERE bucket=?',(b'changed','private_baselines'))
            self.assertNotEqual(runner.original_records(path),before)


if __name__=='__main__':unittest.main()
