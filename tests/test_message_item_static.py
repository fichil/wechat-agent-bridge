"""Static research gate tests; no installed DLL or process is accessed."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

_path = sys.path[:]
try:
    from scripts import trace_message_items_static as p
finally:
    sys.path[:] = _path
del _path


class MessageStaticTests(unittest.TestCase):
    def test_wrong_file_fingerprint_stops_before_pe_parsing(self):
        with self.assertRaisesRegex(ValueError, 'unreviewed_dll_fingerprint'):
            p.inspect(b'not a DLL')

    def test_output_outside_local_evidence_is_rejected_before_input_read(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / '.local') as directory:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                p.main(['--dll', 'missing.dll', '--output', str(Path(directory) / 'out.json')])

    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / '.local/live-acceptance') as directory:
            out = Path(directory) / 'out.json'; out.write_text('preserved', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                p.main(['--dll', 'missing.dll', '--output', str(out)])
            self.assertEqual(out.read_text(encoding='utf-8'), 'preserved')
