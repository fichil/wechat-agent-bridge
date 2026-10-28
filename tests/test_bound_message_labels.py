import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import probe_bound_message_labels as probe
from wechat_agent_bridge.console_models import ConsoleError


class BoundMessageLabelTests(unittest.TestCase):
    def test_default_has_no_live_access(self):
        with patch.object(probe.original, 'run', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)

    def test_protection_precedes_file_and_public_output_excludes_body(self):
        value = dict(loaded_count=1, rows=[dict(label='synthetic private text', class_name='mmui::ChatTextItemView')],
                     coverage='accessible_loaded_tail_only')
        with tempfile.TemporaryDirectory() as directory:
            protector = Mock()
            protector.protect.side_effect = RuntimeError('locked')
            with self.assertRaises(RuntimeError): probe.protect_capture(value, directory, protector)
            self.assertEqual(list(Path(directory).iterdir()), [])
            protector.protect.side_effect = None
            protector.protect.return_value = b'encrypted'
            result = probe.protect_capture(value, directory, protector)
            self.assertNotIn('synthetic private text', json.dumps(result))
            self.assertFalse(result['sender_identity_verified'])
            self.assertEqual(Path(result['protected_capture']).read_bytes(), b'encrypted')

    def fixture(self, count=1):
        raw, root, uia, listing, matches, items = [Mock() for _ in range(6)]
        root.FindAll.return_value = matches
        matches.Length = 1
        matches.GetElement.return_value = listing
        listing.CurrentProcessId = 7
        listing.GetRuntimeId.return_value = [7, 10]
        listing.FindAll.return_value = items
        items.Length = count
        def get(index):
            item = Mock(CurrentProcessId=7, CurrentName='synthetic', CurrentClassName='mmui::ChatTextItemView')
            item.GetRuntimeId.return_value = [7, index]
            return item
        items.GetElement.side_effect = get
        return raw, root, uia, listing, items

    def test_tail_is_bounded_and_historical(self):
        raw, root, uia, listing, items = self.fixture(60)
        result = probe.collect_labels(raw, root, uia, 7)
        self.assertEqual(len(result['rows']), 50)
        self.assertEqual(items.GetElement.call_args_list[0].args, (10,))
        self.assertTrue(result['historical'])

    def test_wrong_process_and_changing_list_rejected(self):
        raw, root, uia, listing, items = self.fixture()
        listing.CurrentProcessId = 9
        with self.assertRaisesRegex(ConsoleError, 'process_mismatch'): probe.collect_labels(raw, root, uia, 7)
        listing.CurrentProcessId = 7
        listing.GetRuntimeId.side_effect = ([7, 10], [7, 11])
        with self.assertRaisesRegex(ConsoleError, 'list_changed'): probe.collect_labels(raw, root, uia, 7)
