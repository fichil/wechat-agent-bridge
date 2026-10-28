"""Synthetic file metadata only; no installed binaries or process access."""
import struct
import unittest
from test_pe_inventory import fixture
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import qt_metaobject, qt_inheritance, msvc_col, vtable_slot

BASE = 0x180000000


def metadata_fixture():
    data = fixture()
    struct.pack_into('<QQQQ', data, 0x200, 0, BASE+0x1080, BASE+0x1100, 0)
    for at, value in ((0x280, b'Synthetic'), (0x298, b'send')):
        text = at+0x40
        struct.pack_into('<iII', data, at, -1, len(value), 0)
        struct.pack_into('<q', data, at+16, text-at)
        data[text:text+len(value)+1] = value+b'\0'
    struct.pack_into('<14I', data, 0x300, 8, 0, 0, 0, 1, 14, 0, 0, 0, 0, 0, 0, 0, 1)
    struct.pack_into('<5I', data, 0x338, 1, 1, 19, 0, 6)
    struct.pack_into('<6I', data, 0x400, 1, 0, 0, 0x1240, 0x1260, 0x1200)
    struct.pack_into('<QQ', data, 0x480, BASE+0x1200, BASE+0x1400)
    return data


class StaticTypeTests(unittest.TestCase):
    def test_inheritance_and_signal_is_not_a_slot(self):
        result = qt_inheritance(PE(metadata_fixture()), BASE, 0x1000)
        self.assertEqual(result[0]['class'], 'Synthetic')
        self.assertEqual(result[0]['methods'][0]['kind'], 'signal')

    def test_slot_and_constructor_method_flags(self):
        data = metadata_fixture()
        for flags, expected in ((10, 'slot'), (14, 'constructor')):
            struct.pack_into('<I', data, 0x348, flags)
            self.assertEqual(qt_metaobject(PE(data), BASE, 0x1000)['methods'][0]['kind'], expected)

    def test_parent_cycle_rejected(self):
        data = metadata_fixture(); struct.pack_into('<Q', data, 0x200, BASE+0x1000)
        with self.assertRaisesRegex(ValueError, 'cycle'): qt_inheritance(PE(data), BASE, 0x1000)

    def test_unknown_revision_and_excessive_methods(self):
        for at, value in ((0x300, 9), (0x310, 257)):
            data = metadata_fixture(); struct.pack_into('<I', data, at, value)
            with self.assertRaises(ValueError): qt_metaobject(PE(data), BASE, 0x1000)

    def test_invalid_string_length(self):
        data = metadata_fixture(); struct.pack_into('<I', data, 0x284, 10)
        with self.assertRaisesRegex(ValueError, 'length'): qt_metaobject(PE(data), BASE, 0x1000)

    def test_vtable_and_col(self):
        result = vtable_slot(PE(metadata_fixture()), BASE, 0x1288, 0)
        self.assertEqual(result['target'], 0x1400)
        self.assertEqual(result['col']['type_descriptor'], 0x1240)

    def test_bad_col_self_reference(self):
        data = metadata_fixture(); struct.pack_into('<I', data, 0x414, 0x1201)
        with self.assertRaisesRegex(ValueError, 'invalid_msvc'): msvc_col(PE(data), 0x1200)

    def test_unmapped_target_and_slot_limits(self):
        data = metadata_fixture(); struct.pack_into('<Q', data, 0x488, BASE+0x9000)
        with self.assertRaises(ValueError): vtable_slot(PE(data), BASE, 0x1288, 0)
        with self.assertRaises(ValueError): vtable_slot(PE(metadata_fixture()), BASE, 0x1288, 256)

    def test_declared_property_builtin_type_is_not_a_string_index(self):
        data = metadata_fixture()
        struct.pack_into('<II', data, 0x318, 1, 24)
        struct.pack_into('<3I', data, 0x360, 1, 67, 3)
        result = qt_metaobject(PE(data), BASE, 0x1000)['properties']
        self.assertEqual(result, [dict(name='send', flags=3, readable=True,
                                      writable=True, builtin_type_id=67)])

    def test_unresolved_property_type_uses_bounded_string_index(self):
        data = metadata_fixture()
        struct.pack_into('<II', data, 0x318, 1, 24)
        struct.pack_into('<3I', data, 0x360, 1, 0x80000000, 1)
        result = qt_metaobject(PE(data), BASE, 0x1000)['properties'][0]
        self.assertEqual(result['type_name'], 'Synthetic')
        self.assertTrue(result['readable'])
        self.assertFalse(result['writable'])

    def test_invalid_property_count_offset_and_type_index_stop(self):
        for count, offset, type_info in ((257, 24, 1), (1, 13, 1), (1, 24, 0x80001000)):
            data = metadata_fixture()
            struct.pack_into('<II', data, 0x318, count, offset)
            struct.pack_into('<3I', data, 0x360, 1, type_info, 1)
            with self.subTest(count=count, offset=offset, type_info=type_info), self.assertRaises(ValueError):
                qt_metaobject(PE(data), BASE, 0x1000)
