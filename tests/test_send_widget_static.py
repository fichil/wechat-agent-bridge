import struct
import unittest

from test_static_types import metadata_fixture, BASE
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import find_qt_metaobjects
from scripts.trace_send_widget_static import inspect


class SendWidgetStaticTests(unittest.TestCase):
    def test_string_requires_header_reference_and_complete_metadata(self):
        data = metadata_fixture()
        result = find_qt_metaobjects(PE(data), BASE, 'Synthetic')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0]['rva'], 0x1000)
        self.assertEqual(result[0][0]['methods'][0]['kind'], 'signal')
        struct.pack_into('<Q', data, 0x208, BASE + 0x1088)
        self.assertEqual(find_qt_metaobjects(PE(data), BASE, 'Synthetic'), [])

    def test_bad_string_pointer_and_inheritance_cycle_not_accepted(self):
        for offset, value in ((0x290, 0x41), (0x200, BASE + 0x1000)):
            data = metadata_fixture()
            struct.pack_into('<Q', data, offset, value)
            self.assertEqual(find_qt_metaobjects(PE(data), BASE, 'Synthetic'), [])

    def test_unknown_names_and_wrong_fingerprint_do_not_qualify(self):
        self.assertEqual(find_qt_metaobjects(PE(metadata_fixture()), BASE, 'Other'), [])
        with self.assertRaisesRegex(ValueError, 'fingerprint'): inspect(metadata_fixture())
        with self.assertRaisesRegex(ValueError, 'class_name'):
            find_qt_metaobjects(PE(metadata_fixture()), BASE, 'Synthetic\0Other')

    def test_excessive_duplicate_name_search_stops(self):
        data = metadata_fixture()
        data += (b'Synthetic\0' * 65)
        with self.assertRaisesRegex(ValueError, 'search_limit'):
            find_qt_metaobjects(PE(data), BASE, 'Synthetic')
