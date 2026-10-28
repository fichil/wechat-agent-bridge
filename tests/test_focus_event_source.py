"""Synthetic PE/decoded-instruction tests; no installed drivers or trace API."""
import hashlib
import struct
import unittest

from test_pe_inventory import fixture
from scripts.review_focus_event_source import EVENT_2, event_descriptor, imported_operand, verified_pe
from wechat_agent_bridge.adapters.pe_inventory import PE


def function_fixture():
    data = fixture()
    struct.pack_into("<II", data, 0x120, 0x1040, 12)
    struct.pack_into("<III", data, 0x240, 0x1100, 0x1110, 0)
    return data


class FocusEventSourceTests(unittest.TestCase):
    def test_changed_driver_rejected_before_parsing(self):
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verified_pe(b"not a driver", "0"*64, {})

    def test_exact_boundary_required(self):
        data = function_fixture()
        digest = hashlib.sha256(data).hexdigest()
        self.assertIsInstance(verified_pe(data, digest, {0x1100: 0x1110}), PE)
        with self.assertRaisesRegex(ValueError, "boundary"):
            verified_pe(data, digest, {0x1100: 0x1120})

    def test_partial_unwind_entry_rejected(self):
        data = function_fixture()
        struct.pack_into("<I", data, 0x124, 13)
        with self.assertRaisesRegex(ValueError, "unwind"):
            verified_pe(data, hashlib.sha256(data).hexdigest(), {})

    def test_event_identity_includes_keyword(self):
        data = fixture()
        data[0x300:0x310] = EVENT_2
        self.assertEqual(event_descriptor(PE(data), 0x1100)["id"], 2)
        data[0x30F] ^= 1
        with self.assertRaisesRegex(ValueError, "descriptor"):
            event_descriptor(PE(data), 0x1100)

    def test_descriptor_must_be_fully_file_backed(self):
        with self.assertRaisesRegex(ValueError, "unmapped"):
            event_descriptor(PE(fixture()), 0x15F8)

    def test_rex_prefixed_import_uses_decoded_seven_byte_length(self):
        # Win32k has 48 FF 15 disp32 calls. A hard-coded six-byte formula
        # silently misses EtwTraceFocusChange/PsGetThreadId import bindings.
        imports = {0x2000: "EtwTraceFocusChange"}
        self.assertEqual(imported_operand(0x1000, 7, 0xFF9, imports), "EtwTraceFocusChange")
        self.assertIsNone(imported_operand(0x1000, 6, 0xFF9, imports))


if __name__ == "__main__":
    unittest.main()
