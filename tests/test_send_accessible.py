import contextlib
import io
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import probe_send_accessible as probe
from wechat_agent_bridge.console_models import ConsoleError


class SendAccessibleTests(unittest.TestCase):
    def test_selected_cell_requires_reviewed_layout_code_and_selected_byte(self):
        fp=SimpleNamespace(module_base=0x100000)
        memory=Mock(); memory.read.return_value=b'guard'; memory.number.return_value=1
        profile=SimpleNamespace(pe=SimpleNamespace(data=b'guard',offset=lambda *_:0))
        metadata={'class':'mmui::ChatSessionCell','hierarchy':[{'rva':0x9073490,'class':'mmui::XTableCell'}]}
        code={0x9D2080:(5,hashlib.sha256(b'guard').hexdigest())}
        with patch.object(probe,'SESSION_STATE_CODE',code):
            self.assertTrue(probe.read_selected_cell(memory,fp,profile,0x200000,0x9D0F50,metadata)['selected'])
            memory.number.return_value=0
            with self.assertRaisesRegex(ConsoleError,'not_selected'):
                probe.read_selected_cell(memory,fp,profile,0x200000,0x9D0F50,metadata)
            memory.read.return_value=b'other'
            with self.assertRaisesRegex(ConsoleError,'code_changed'):
                probe.read_selected_cell(memory,fp,profile,0x200000,0x9D0F50,metadata)
        with self.assertRaisesRegex(ConsoleError,'layout_unverified'):
            probe.read_selected_cell(memory,fp,profile,0x200000,0x9D0F51,metadata)

    def test_session_guard_rejects_pointer_outside_bound_module(self):
        fp = SimpleNamespace(module_base=0x100000, module_size=0x10000)
        memory = Mock(); memory.number.return_value = 0x200000
        with patch.object(probe, 'interface_for_id', return_value=0x500000), \
             self.assertRaisesRegex(ConsoleError, 'table_outside_module'):
            probe.inspect_session_guard(memory, fp, Mock(), [42, 1])

    def test_session_guard_checks_loaded_slots_before_object_fields(self):
        fp = SimpleNamespace(module_base=0x100000, module_size=0x10000)
        memory = Mock(); memory.number.side_effect = [0x101000, 0x101100]
        profile = Mock(base=0x180000000)
        profile.pe.unpack.return_value = (profile.base + 0x1200,)
        with patch.object(probe, 'interface_for_id', return_value=0x500000), \
             self.assertRaisesRegex(ConsoleError, 'vtable_changed'):
            probe.inspect_session_guard(memory, fp, profile, [42, 1])

    def test_unknown_callback_does_not_read_captures(self):
        memory = Mock()
        self.assertIsNone(probe.inspect_clicked_capture(memory, Mock(), 0x100000, 0x200000, 0x300000, 0x400000))
        memory.read.assert_not_called()
        memory.number.assert_not_called()

    def test_capture_must_equal_connection_receiver(self):
        synthetic = b's' * 72
        profile = SimpleNamespace(pe=SimpleNamespace(data=synthetic, offset=lambda *_: 0))
        memory = Mock()
        memory.read.return_value = synthetic
        with patch.object(probe, 'CLICKED_CALLBACK_SHA256', hashlib.sha256(synthetic).hexdigest()):
            for actual in (0, 0x500000):
                memory.number.return_value = actual
                with self.assertRaisesRegex(ConsoleError, 'capture_receiver_mismatch'):
                    probe.inspect_clicked_capture(memory, profile, 0x100000, 0x200000, 0x100000 + 0x1C231B0, 0x400000)
            memory.number.return_value = 0x400000
            result = probe.inspect_clicked_capture(memory, profile, 0x100000, 0x200000, 0x100000 + 0x1C231B0, 0x400000)
        self.assertTrue(result['captured_receiver_matches_connection'])
        self.assertFalse(result['callback_executed'])

    def test_changed_loaded_callback_stops_before_capture(self):
        synthetic = b's' * 72
        profile = SimpleNamespace(pe=SimpleNamespace(data=synthetic, offset=lambda *_: 0))
        memory = Mock()
        memory.read.return_value = b'x' * 72
        with patch.object(probe, 'CLICKED_CALLBACK_SHA256', hashlib.sha256(synthetic).hexdigest()), \
             self.assertRaisesRegex(ConsoleError, 'callback_code_changed'):
            probe.inspect_clicked_capture(memory, profile, 0x100000, 0x200000, 0x100000 + 0x1C231B0, 0x400000)
        memory.number.assert_not_called()

    def test_connections_require_exact_getter_live_object_and_widget_type(self):
        fp = SimpleNamespace(module_base=0x100000, module_size=0x10000000)
        profile, memory = Mock(), Mock()
        memory.number.return_value = 0
        with self.assertRaisesRegex(ConsoleError, 'getter_unverified'):
            probe.inspect_button_connections(memory, fp, profile, 0x20000000, 0x30000000)
        memory.number.side_effect = [fp.module_base + 0x1B8FF00, 0x21000000, 0]
        with self.assertRaisesRegex(ConsoleError, 'destroyed'):
            probe.inspect_button_connections(memory, fp, profile, 0x20000000, 0x30000000)
        memory.number.side_effect = [fp.module_base + 0x1B8FF00, 0x21000000, 0x22000000, 1, 0x23000000]
        with patch('wechat_agent_bridge.adapters.runtime_accessibility.object_metadata', return_value={'class':'other'}), \
             self.assertRaisesRegex(ConsoleError, 'class_unverified'):
            probe.inspect_button_connections(memory, fp, profile, 0x20000000, 0x30000000)

    def test_connection_worker_is_still_readonly(self):
        with patch.object(probe, 'worker') as worker:
            probe.connections_worker('pipe', 'fp', 'names', 'expected', False)
        worker.assert_called_once_with('pipe', 'fp', 'names', 'expected', False, inspect_connections=True)

    def test_reviewed_action_requires_exact_file_and_loaded_bytes(self):
        synthetic = b'x' * 0x7F
        memory = Mock()
        memory.read.return_value = synthetic
        with patch.object(probe, 'FOCUS_ONLY_ACTION_SHA256', hashlib.sha256(synthetic).hexdigest()):
            result = probe.verify_focus_only_action(memory, 0x1000, synthetic)
            self.assertFalse(result['press_action_supported'])
            memory.read.return_value = b'y' * 0x7F
            with self.assertRaisesRegex(ConsoleError, 'loaded_code_changed'):
                probe.verify_focus_only_action(memory, 0x1000, synthetic)

    def test_unknown_action_bytes_cannot_be_classified(self):
        memory = Mock()
        with self.assertRaisesRegex(ConsoleError, 'reviewed_code_mismatch'):
            probe.verify_focus_only_action(memory, 0x1000, b'unknown')
        memory.read.assert_not_called()

    def test_default_has_no_native_access(self):
        with patch.object(probe.original, 'run', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 0)

    def test_exact_cache_vtable_and_file_pointer_required(self):
        fp = SimpleNamespace(module_base=0x100000, module_size=0x10000)
        memory, profile = Mock(), Mock(base=0x180000000)
        profile.pe.unpack.return_value = [profile.base + 0x300]
        memory.number.side_effect = [fp.module_base + 0x200, fp.module_base + 0x300]
        with patch.object(probe, 'interface_for_id', return_value=0x50000):
            result = probe.inspect_interface(memory, fp, profile, [1, 2])
        self.assertEqual(result['interface_cast_rva'], 0x300)
        self.assertFalse(result['execution_proven'])
        memory.recheck.assert_called_once()

    def test_cache_change_or_bad_pointer_rejected(self):
        fp = SimpleNamespace(module_base=0x100000, module_size=0x10000)
        for bad_pointer in (True, False):
            memory, profile = Mock(), Mock(base=0x180000000)
            profile.pe.unpack.return_value = [profile.base + 0x300]
            memory.number.side_effect = [fp.module_base + 0x200,
                                        fp.module_base + (0x400 if bad_pointer else 0x300)]
            with patch.object(probe, 'interface_for_id', side_effect=[0x50000, 0x50010]), self.assertRaises(ConsoleError):
                probe.inspect_interface(memory, fp, profile, [1, 2])
