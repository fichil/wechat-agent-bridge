"""Synthetic TDH blobs only. These tests never load TDH or collect ETW events."""
import struct
import unittest

from scripts.inspect_focus_events import event_descriptors, filtering_evidence, parse_event, utf16_at


def fixture():
    blob = bytearray(136)
    def string(value):
        offset = len(blob)
        blob.extend(value.encode('utf-16-le') + b'\0\0')
        return offset
    struct.pack_into('<HBBBBHQ', blob, 32, 258, 0, 0, 4, 0, 258, 0x20000)
    struct.pack_into('<I', blob, 52, string('Synthetic-Provider'))
    struct.pack_into('<I', blob, 68, string('FocusedProcessChangeGained'))
    struct.pack_into('<II', blob, 100, 1, 1)
    struct.pack_into('<IIHHIHHI', blob, 112, 0, string('NewProcessId'), 8, 0, 0, 1, 4, 0)
    return blob


class MetadataTests(unittest.TestCase):
    def test_decodes_metadata_fields_not_live_payload(self):
        result = parse_event(fixture())
        self.assertEqual(result['id'], 258)
        self.assertEqual(result['keyword'], '0x20000')
        self.assertEqual(result['fields'][0]['name'], 'NewProcessId')
        self.assertEqual(result['fields'][0]['type_name'], 'UInt32')
        self.assertNotIn('value', result['fields'][0])

    def test_rejects_truncated_and_excessive_property_arrays(self):
        for length in (0, 111, 135):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_event(fixture()[:length])
        blob = fixture()
        struct.pack_into('<I', blob, 100, 257)
        with self.assertRaises(ValueError): parse_event(blob)

    def test_rejects_offsets_and_unterminated_strings(self):
        for offset in (2, 113, 100000):
            with self.subTest(offset=offset), self.assertRaises(ValueError): utf16_at(fixture(), offset)
        with self.assertRaises(ValueError): utf16_at(b'\0' * 112 + b'A\0', 112)

    def test_struct_field_is_not_misread_as_scalar(self):
        blob = fixture()
        struct.pack_into('<I', blob, 112, 1)
        with self.assertRaisesRegex(ValueError, 'complex_property'): parse_event(blob)

    def test_event_list_bounds(self):
        descriptor = bytes(fixture()[32:48])
        self.assertEqual(event_descriptors(struct.pack('<II', 1, 0) + descriptor), [descriptor])
        for blob in (b'', struct.pack('<II', 2, 0) + descriptor, struct.pack('<II', 100000, 0)):
            with self.assertRaises(ValueError): event_descriptors(blob)

    def test_constructed_filter_never_means_trace_or_kernel_pid_support(self):
        result = filtering_evidence('kernel', [{'construction_succeeded': True}])
        self.assertEqual(result['pid_scope_filter'], 'not_usable_for_kernel_registration')
        self.assertEqual(result['payload_filter_effective'], 'not_tested')
        self.assertFalse(result['trace_started'])
        self.assertFalse(result['live_diagnostic_ready'])


if __name__ == '__main__':
    unittest.main()
