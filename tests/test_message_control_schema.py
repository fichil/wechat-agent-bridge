"""Synthetic UIA tree only; message text properties must never be read."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from scripts import message_control_schema as m


class Collection:
    def __init__(self, rows):
        self.rows, self.Length = rows, len(rows)
    def GetElement(self, index):
        return self.rows[index]


class Element:
    def __init__(self, identity, children=(), *, pid=17, kind=50007, item_type=''):
        self.identity, self.children = identity, list(children)
        self.CurrentProcessId, self.CurrentControlType = pid, kind
        self.CurrentClassName, self.CurrentItemType = 'mmui::Message', item_type
    @property
    def CurrentName(self):
        raise AssertionError('message text was read')
    def GetRuntimeId(self):
        return [42, self.identity]
    def FindAll(self, *_args):
        return Collection(self.children)


def fixture(rows):
    listing = Element(1, rows, kind=50008)
    root = Element(0, [listing])
    uia = SimpleNamespace(UIA_NamePropertyId=1, UIA_ControlTypePropertyId=2,
        UIA_ListControlTypeId=50008, UIA_ListItemControlTypeId=50007,
        TreeScope_Descendants=4, TreeScope_Children=2)
    return Mock(), root, uia, listing


class MessageSchemaTests(unittest.TestCase):
    def test_only_tail_metadata_is_retained_without_reading_text(self):
        raw, root, uia, _ = fixture([Element(i) for i in range(10, 20)])
        result = m.collect(raw, root, uia, 17)
        self.assertEqual(result['visible_row_count'], 10)
        self.assertEqual(result['inspected_tail_rows'], 5)
        self.assertFalse(result['text_read'])
        self.assertFalse(result['own_identity_verified'])
        self.assertEqual(result['sender_decision'], 'unverified')

    def test_direction_hint_is_not_an_own_identity_claim(self):
        raw, root, uia, _ = fixture([Element(2, item_type='OutgoingMessage')])
        result = m.collect(raw, root, uia, 17)
        self.assertEqual(result['rows'][0]['nodes'][0]['item_type_direction_hint'], 'outgoing')
        self.assertFalse(result['own_identity_verified'])

    def test_received_text_cannot_leak_through_metadata(self):
        item = Element(2, item_type='private message body')
        item.CurrentClassName = '<script>private message body</script>'
        raw, root, uia, _ = fixture([item])
        result = m.collect(raw, root, uia, 17)
        self.assertNotIn('private message body', json.dumps(result))
        self.assertEqual(result['rows'][0]['nodes'][0]['class_name'], 'other')

    def test_cross_process_and_cycle_stop(self):
        for item, reason in ((Element(2, pid=18), 'message_schema_process_mismatch'),
                             (Element(2, [Element(2)]), 'message_schema_identity_cycle')):
            raw, root, uia, _ = fixture([item])
            with self.subTest(reason=reason), self.assertRaisesRegex(m.ConsoleError, reason):
                m.collect(raw, root, uia, 17)

    def test_identity_change_stops(self):
        item = Element(2)
        item.GetRuntimeId = Mock(side_effect=[[42, 2], [42, 2], [42, 3]])
        raw, root, uia, _ = fixture([item])
        with self.assertRaisesRegex(m.ConsoleError, 'message_schema_identity_changed'):
            m.collect(raw, root, uia, 17)

    def test_depth_and_child_limits_are_explicit(self):
        item = Element(2, [Element(i) for i in range(10, 50)])
        raw, root, uia, _ = fixture([item])
        result = m.collect(raw, root, uia, 17)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['node_count'], 33)
        deep = Element(2, [Element(3, [Element(4, [Element(5, [Element(6)])])])])
        raw, root, uia, _ = fixture([deep])
        result = m.collect(raw, root, uia, 17)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['node_count'], 4)

    def test_ambiguous_list_and_row_limit_stop(self):
        raw, root, uia, listing = fixture([])
        root.children.append(Element(3))
        with self.assertRaisesRegex(m.ConsoleError, 'message_schema_list_ambiguous'):
            m.collect(raw, root, uia, 17)
        root.children.pop()
        listing.children = [Element(2)] * (m.MAX_ROWS + 1)
        with self.assertRaisesRegex(m.ConsoleError, 'message_schema_row_limit'):
            m.collect(raw, root, uia, 17)

    def test_auxiliary_values_are_projected_without_retaining_text_or_ids(self):
        item = Element(2)
        item.CurrentHelpText = 'private message\nwxid_secret'
        item.CurrentAutomationId = 'internal_123'
        raw, root, uia, _ = fixture([item])
        result = m.collect(raw, root, uia, 17, auxiliary=True)
        encoded = json.dumps(result)
        self.assertNotIn('private message', encoded)
        self.assertNotIn('wxid_secret', encoded)
        self.assertNotIn('internal_123', encoded)
        self.assertTrue(result['text_read'])
        self.assertFalse(result['message_name_value_text_read'])
        self.assertFalse(result['property_values_retained'])
        shapes = result['rows'][0]['nodes'][0]['auxiliary_property_shapes']
        self.assertTrue(shapes['CurrentHelpText']['has_line_breaks'])
        self.assertTrue(shapes['CurrentAutomationId']['simple_identifier'])

    def test_auxiliary_direction_token_is_not_verified_own_identity(self):
        item = Element(2); item.CurrentHelpText = 'self'
        raw, root, uia, _ = fixture([item])
        result = m.collect(raw, root, uia, 17, auxiliary=True)
        self.assertEqual(result['rows'][0]['nodes'][0]['auxiliary_property_shapes']
                         ['CurrentHelpText']['exact_direction_token'], 'self')
        self.assertFalse(result['own_identity_verified'])

    def test_auxiliary_unavailable_unexpected_and_oversized_values(self):
        item = Element(2)
        self.assertEqual(m.auxiliary_shape(item, 'CurrentHelpText')['state'], 'unsupported_or_failed')
        for value, state in ((None, 'empty'), ('', 'empty'), (17, 'unexpected_type'), ('x' * 4097, 'over_limit')):
            item.CurrentHelpText = value
            self.assertEqual(m.auxiliary_shape(item, 'CurrentHelpText')['state'], state)

    def test_auxiliary_cannot_request_message_text_property(self):
        with self.assertRaisesRegex(m.ConsoleError, 'message_auxiliary_property_forbidden'):
            m.auxiliary_shape(Element(2), 'CurrentName')

    def test_full_description_uses_property_api_without_newer_getter(self):
        item = Element(2)
        item.GetCurrentPropertyValue = Mock(return_value='private description')
        value = m.auxiliary_shape(item, 'CurrentFullDescription', full_description_property_id=30159)
        item.GetCurrentPropertyValue.assert_called_once_with(30159)
        self.assertEqual(value['state'], 'text_shape')
        self.assertNotIn('private description', json.dumps(value))
        item.GetCurrentPropertyValue.return_value = ''
        self.assertEqual(m.auxiliary_shape(item, 'CurrentFullDescription',
            full_description_property_id=30159)['state'], 'empty_or_default')
