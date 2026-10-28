import struct
import unittest
from wechat_agent_bridge.adapters.pe_inventory import PE, inventory


def fixture():
    data = bytearray(0x800)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, 0x8664, 1)
    struct.pack_into('<H', data, 0x94, 240)
    struct.pack_into('<H', data, 0x98, 0x20b)
    struct.pack_into('<I', data, 0x104, 16)
    struct.pack_into('<IIII', data, 0x190, 0x600, 0x1000, 0x600, 0x200)
    return data


class StaticPETests(unittest.TestCase):
    def test_empty_valid_image(self):
        report = inventory(fixture())
        self.assertEqual(report['exports'], [])
        self.assertEqual(report['delay_imports'], [])

    def test_invalid_or_truncated(self):
        for data in (b'', b'MZ', bytes(100), fixture()[:500]):
            with self.assertRaises(ValueError): PE(data)

    def test_virtual_unbacked_rva_rejected(self):
        with self.assertRaises(ValueError): PE(fixture()).offset(0x1600)

    def test_named_export(self):
        data = fixture()
        struct.pack_into('<II', data, 0x108, 0x1000, 40)
        struct.pack_into('<IIIIII', data, 0x210, 1, 1, 1, 0x1040, 0x1044, 0x1048)
        struct.pack_into('<IIH', data, 0x240, 0x1100, 0x1050, 0)
        data[0x250:0x255] = b'Test\0'
        self.assertEqual(PE(data).exports()[0]['name'], 'Test')

    def test_excessive_export_count_rejected(self):
        data = fixture()
        struct.pack_into('<II', data, 0x108, 0x1000, 40)
        struct.pack_into('<III', data, 0x210, 1, 200000, 200000)
        with self.assertRaises(ValueError): PE(data).exports()

    def test_delay_import(self):
        data = fixture()
        struct.pack_into('<II', data, 0x170, 0x1000, 64)
        struct.pack_into('<IIIIIIII', data, 0x200, 1, 0x1050, 0, 0x1060, 0x1070, 0, 0, 0)
        data[0x250:0x258] = b'foo.dll\0'
        struct.pack_into('<Q', data, 0x270, 0x1080)
        data[0x282:0x287] = b'Test\0'
        self.assertEqual(PE(data).delay_imports()[0]['name'], 'Test')
