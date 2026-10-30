import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import admin
from backend.app.auth import AuthStore, AuthError


class AdminCommandTests(unittest.TestCase):
    def test_interactive_init_reset_and_no_plaintext_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'auth.sqlite3'
            output = io.StringIO()
            old = 'synthetic-cli-password'
            new = 'synthetic-reset-password'
            for action, password in [('init', old), ('reset-password', new)]:
                with patch('sys.argv', ['admin', action, '--auth-database', str(path)]), \
                     patch('sys.stdin.isatty', return_value=True), \
                     patch('backend.admin.getpass', side_effect=[password, password]), redirect_stdout(output):
                    admin.main()
                store = AuthStore(path)
                try:
                    if action == 'init':
                        token, _ = store.login('admin', old, 'test')
                    else:
                        self.assertIsNone(store.session(token))
                        with self.assertRaises(AuthError): store.login('admin', old, 'test')
                        self.assertTrue(store.login('admin', new, 'test')[1])
                    hashed = store.db.execute('SELECT password_hash FROM administrator').fetchone()[0]
                    self.assertTrue(hashed.startswith('$argon2id$v=19$m=19456,t=2,p=1$'))
                finally:
                    store.close()
            self.assertNotIn(old, output.getvalue())
            self.assertNotIn(new, output.getvalue())

    def test_no_piped_passwords_or_mismatched_confirmation(self):
        for interactive in (False, True):
            with patch('sys.argv', ['admin', 'init']), patch('sys.stdin.isatty', return_value=interactive), \
                 patch('backend.admin.getpass', side_effect=['synthetic-password', 'different']), \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                admin.main()
