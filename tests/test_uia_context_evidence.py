"""Synthetic bytes only. No COM, process, desktop or native memory access."""
import copy
import struct
import unittest

from scripts import uia_context_evidence as u


BASE, CLIENT, ELEMENT, PATTERN, NODE, ASSOCIATION = (0x10000000, 0x20000000,
    0x21000000, 0x22000000, 0x23000000, 0x24000000)
IMAGE = {'base': BASE, 'size': 0x420000, 'sha256': u.DLL_SHA256}


class BytesPort:
    def __init__(self):
        self.memory, self.calls, self.after_read = {}, [], lambda _address: None

    def put(self, address, value, width=8):
        self.memory[address, width] = struct.pack('<Q' if width == 8 else '<I', value)

    def read(self, address, count, *, image=False):
        self.calls.append((address, count, image))
        value = self.memory[address, count]
        self.after_read(address)
        return value


def fixture(flag=0, associated_client=CLIENT):
    p = BytesPort()
    for ptr, table, slot, method in ((CLIENT, 0x2E3E40, 0x1D0, 0x1AC360),
        (ELEMENT, u.ELEMENT_TABLE, 0x18, 0x2298E0),
        (PATTERN, u.PATTERN_TABLE, 0x18, 0x1C6670),
        (NODE, u.NODE_TABLE, 0x30, 0x15B790)):
        p.put(ptr, BASE + table)
        p.put(BASE + table + slot, BASE + method)
    p.put(CLIENT + 0x364, flag, 4)
    p.put(PATTERN + 0x50, ELEMENT)
    p.put(ELEMENT + 0x38, NODE)
    p.put(NODE + 0x20, ASSOCIATION)
    p.put(ASSOCIATION + 0x10, CLIENT + 0x2E0)
    p.put(CLIENT + 0x318, associated_client)
    return p


class ContextTests(unittest.TestCase):
    def inspect(self, p, **kwargs):
        return u.inspect_context(p, kwargs.pop('image', IMAGE), CLIENT, ELEMENT, PATTERN,
                                 kwargs.pop('public_auto_focus', False))

    def test_present_false_association_binds_requested_client(self):
        result = self.inspect(fixture())
        self.assertEqual(result['association'], 'present')
        self.assertFalse(result['requested_auto_focus'])
        self.assertFalse(result['associated_auto_focus'])
        self.assertTrue(result['matches_requested_client'])
        self.assertTrue(result['matches_requested_context_body'])
        self.assertTrue(result['stable'])
        self.assertLess(result['bytes_read'], u.MAX_READ_BYTES)
        self.assertFalse(result['internal_methods_called'])

    def test_invoke_pattern_has_independent_verified_table_and_entry(self):
        p = fixture()
        p.put(PATTERN, BASE + u.INVOKE_PATTERN_TABLE)
        p.put(BASE + u.INVOKE_PATTERN_TABLE + 0x18, BASE + 0x1C2180)
        result = u.inspect_context(p, IMAGE, CLIENT, ELEMENT, PATTERN, False,
                                   pattern_kind='invoke')
        self.assertTrue(result['stable'])
        self.assertTrue(result['matches_requested_client'])
        self.assertFalse(result['associated_auto_focus'])
        self.assertEqual(result['tables']['pattern'], u.INVOKE_PATTERN_TABLE)

    def test_value_pattern_cannot_qualify_an_invoke_pattern(self):
        p = fixture()
        with self.assertRaisesRegex(u.ContextError, '^pattern_vtable_unverified$'):
            u.inspect_context(p, IMAGE, CLIENT, ELEMENT, PATTERN, False,
                              pattern_kind='invoke')
        self.assertFalse(any(a == PATTERN + 0x50 for a, _, _ in p.calls))

    def test_invoke_entry_and_parent_must_match(self):
        for method, parent, reason in ((0x1C6670, ELEMENT, 'pattern_method_mismatch'),
                                      (0x1C2180, ELEMENT + 8, 'pattern_element_mismatch')):
            with self.subTest(reason=reason):
                p = fixture()
                p.put(PATTERN, BASE + u.INVOKE_PATTERN_TABLE)
                p.put(BASE + u.INVOKE_PATTERN_TABLE + 0x18, BASE + method)
                p.put(PATTERN + 0x50, parent)
                with self.assertRaisesRegex(u.ContextError, '^' + reason + '$'):
                    u.inspect_context(p, IMAGE, CLIENT, ELEMENT, PATTERN, False,
                                      pattern_kind='invoke')

    def test_unknown_pattern_kind_stops_before_reading_memory(self):
        for kind in ('Invoke', 'selection', None, []):
            p = fixture()
            with self.assertRaisesRegex(u.ContextError, '^pattern_kind_unverified$'):
                u.inspect_context(p, IMAGE, CLIENT, ELEMENT, PATTERN, False,
                                  pattern_kind=kind)
            self.assertEqual(p.calls, [])

    def test_true_bool_variants_are_consistent_with_public_api(self):
        for value in (1, 0xFFFFFFFF):
            with self.subTest(value=value):
                result = self.inspect(fixture(value), public_auto_focus=True)
                self.assertTrue(result['associated_auto_focus'])

    def test_null_association_does_not_follow_any_body_or_client(self):
        p = fixture(); p.put(NODE + 0x20, 0)
        result = self.inspect(p)
        self.assertEqual(result['association'], 'null')
        self.assertNotIn('associated_auto_focus', result)
        self.assertFalse(any(a == ASSOCIATION + 0x10 for a, _, _ in p.calls))

    def test_other_verified_client_is_reported_without_claiming_same_owner(self):
        other = CLIENT + 0x1000
        p = fixture(associated_client=other)
        p.put(other, BASE + 0x2E3E40); p.put(other + 0x364, 1, 4)
        result = self.inspect(p)
        self.assertFalse(result['matches_requested_client'])
        self.assertTrue(result['associated_auto_focus'])

    def test_wrong_fingerprint_stops_before_any_memory_read(self):
        p = fixture()
        with self.assertRaisesRegex(u.ContextError, '^image_fingerprint_mismatch$'):
            self.inspect(p, image=dict(IMAGE, sha256='wrong'))
        self.assertEqual(p.calls, [])

    def test_unknown_node_table_is_logged_but_not_followed(self):
        p = fixture(); p.put(NODE, BASE + 0x1234)
        with self.assertRaisesRegex(u.ContextError, '^node_vtable_unverified$') as error:
            self.inspect(p)
        self.assertEqual(error.exception.evidence['tables']['node'], 0x1234)
        self.assertFalse(any(a == NODE + 0x20 for a, _, _ in p.calls))

    def test_verified_delegating_node_stops_at_unknown_delegate_table(self):
        p=fixture();delegate=NODE+0x1000
        p.put(NODE,BASE+u.DELEGATING_NODE_TABLE)
        p.put(BASE+u.DELEGATING_NODE_TABLE+0x30,BASE+0x15F740)
        p.put(NODE+0x28,delegate);p.put(delegate,BASE+0x1234)
        with self.assertRaisesRegex(u.ContextError,'^delegated_node_vtable_unverified$') as error:
            self.inspect(p)
        self.assertEqual(error.exception.evidence['tables']['delegated_node'],0x1234)
        self.assertFalse(any(a in (NODE+0x20,delegate+0x20) for a,_,_ in p.calls))

    def test_delegating_node_cycle_is_rejected_before_following(self):
        p=fixture();p.put(NODE,BASE+u.DELEGATING_NODE_TABLE)
        p.put(BASE+u.DELEGATING_NODE_TABLE+0x30,BASE+0x15F740)
        p.put(NODE+0x28,NODE)
        with self.assertRaisesRegex(u.ContextError,'^node_delegate_cycle$'):
            self.inspect(p)

    def test_verified_delegate_layout_returns_same_association_or_null(self):
        for association in (ASSOCIATION,0):
            p=fixture();delegate=NODE+0x1000
            p.put(NODE,BASE+u.DELEGATING_NODE_TABLE)
            p.put(BASE+u.DELEGATING_NODE_TABLE+0x30,BASE+0x15F740)
            p.put(NODE+0x28,delegate);p.put(delegate,BASE+u.DELEGATE_TABLE)
            p.put(BASE+u.DELEGATE_TABLE+0x28,BASE+0x120450)
            p.put(delegate+0x170,association)
            with self.subTest(association=association):
                result=self.inspect(p)
                self.assertEqual(result['association'],'present' if association else 'null')
                self.assertTrue(result['stable'])
                self.assertFalse(any(a==NODE+0x20 for a,_,_ in p.calls))

    def test_delegate_method_mismatch_stops_before_its_association_field(self):
        p=fixture();delegate=NODE+0x1000
        p.put(NODE,BASE+u.DELEGATING_NODE_TABLE)
        p.put(BASE+u.DELEGATING_NODE_TABLE+0x30,BASE+0x15F740)
        p.put(NODE+0x28,delegate);p.put(delegate,BASE+u.DELEGATE_TABLE)
        p.put(BASE+u.DELEGATE_TABLE+0x28,BASE+0x99)
        with self.assertRaisesRegex(u.ContextError,'^delegated_node_method_mismatch$'):
            self.inspect(p)
        self.assertFalse(any(a==delegate+0x170 for a,_,_ in p.calls))

    def test_mismatched_method_is_rejected_before_pattern_fields(self):
        p = fixture(); p.put(BASE + u.PATTERN_TABLE + 0x18, BASE + 0x99)
        with self.assertRaisesRegex(u.ContextError, '^pattern_method_mismatch$'):
            self.inspect(p)
        self.assertFalse(any(a == PATTERN + 0x50 for a, _, _ in p.calls))

    def test_pattern_element_mismatch_does_not_follow_foreign_node(self):
        p = fixture(); p.put(PATTERN + 0x50, ELEMENT + 0x1000)
        with self.assertRaisesRegex(u.ContextError, '^pattern_element_mismatch$'):
            self.inspect(p)
        self.assertFalse(any(a == ELEMENT + 0x38 for a, _, _ in p.calls))

    def test_flag_mismatch_and_unknown_values_reject(self):
        for value, reason in ((1, 'client_flag_readback_mismatch'), (3, 'client_flag_invalid')):
            with self.subTest(value=value), self.assertRaisesRegex(u.ContextError, '^'+reason+'$'):
                self.inspect(fixture(value))

    def test_changed_object_during_final_validation_rejects(self):
        p = fixture()
        def change(address):
            if address == CLIENT + 0x318:
                p.put(ELEMENT + 0x38, NODE + 0x1000)
        p.after_read = change
        with self.assertRaisesRegex(u.ContextError, '^object_changed_during_read$'):
            self.inspect(p)

    def test_failed_or_short_read_has_fixed_reason(self):
        for mode in ('exception', 'short'):
            p = fixture()
            def read(_address, _count, *, image=False):
                if mode == 'exception':
                    raise RuntimeError('private data must not escape')
                return b'\0'
            p.read = read
            with self.subTest(mode=mode), self.assertRaisesRegex(u.ContextError,
                '^memory_read_(failed|incomplete)$'):
                self.inspect(p)

    def test_invalid_ranges_and_read_limit_reject(self):
        p = fixture(); r = u.Reader(p, IMAGE)
        for address in (0, -1, 0x800000000000, True):
            with self.subTest(address=address), self.assertRaises(u.ContextError):
                r.read(address)
        for _ in range(u.MAX_READ_BYTES // 8):
            r.read(CLIENT)
        with self.assertRaisesRegex(u.ContextError, '^memory_read_limit$'):
            r.read(CLIENT)


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.region = dict(base=0x10000, allocation=0x10000, size=4096,
                           protect=4, state=0x1000, type=0x20000)

    def test_commit_readable_region_is_required_and_cannot_cross_end(self):
        self.assertTrue(u.readable_region(0x10000, 8, self.region))
        self.assertFalse(u.readable_region(0x10FFF, 8, self.region))
        for change in ({'protect':1}, {'protect':0x104}, {'protect':0x10},
                       {'state':0x10000}, {'type':0x40000}, {'size':-1}):
            with self.subTest(change=change):
                self.assertFalse(u.readable_region(0x10000, 8, dict(self.region, **change)))

    def test_image_reads_require_exact_image_allocation(self):
        self.assertFalse(u.readable_region(0x10000, 8, self.region, image_base=0x10000))
        image = dict(self.region, type=0x1000000)
        self.assertTrue(u.readable_region(0x10000, 8, image, image_base=0x10000))
        self.assertFalse(u.readable_region(0x10000, 8, image, image_base=0x20000))

    def test_malformed_values_fail_closed(self):
        for region in (None, {}, dict(self.region, protect='4'), dict(self.region, size=True)):
            self.assertFalse(u.readable_region(0x10000, 8, region))
