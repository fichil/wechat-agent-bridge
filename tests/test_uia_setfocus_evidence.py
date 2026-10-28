"""Pure synthetic headers/addresses; no native API, trace, window or process."""
import json
import unittest
from unittest.mock import Mock

from scripts import uia_setfocus_evidence as evidence

BASE = 0x180000000
MODULES = [(BASE, 0x500000, 'UIAutomationCore.dll'), (0x200000000, 0x1000, 'python312.dll')]


def meta(event_id, fields=None):
    return {'id': event_id, 'version': 0, 'channel': 17, 'level': 4,
            'opcode': 2 if event_id == 336 else 1, 'task_id': 335 if event_id in (335, 336) else event_id,
            'keyword': '0x4000000000010000', 'provider': 'Microsoft-Windows-UIAutomationCore',
            'fields': fields or []}


def schemas():
    return evidence.schema_table([meta(335), meta(336), meta(329), meta(509, [{'name': 'MethodName'}])])


def header(event_id, tick=1, **changes):
    value = {'provider': evidence.PROVIDER, 'pid': 51, 'tid': 52,
             'descriptor': evidence.descriptor(meta(event_id)), 'flags': 0x40,
             'data_length': 0, 'time': tick}
    value.update(changes)
    return value


def reader(event_id):
    return lambda: [BASE + rva for rva in evidence.CHAINS[event_id]] + [0x200000008]


def capture(**kwargs):
    return evidence.SetFocusEvidence(51, 52, schemas(), MODULES, **kwargs)


class SetFocusEvidenceTests(unittest.TestCase):
    def test_entry_and_exit_descriptors_match_exact_expected_bytes(self):
        table = schemas()
        self.assertEqual(evidence.descriptor(meta(335)), evidence.ENTRY)
        self.assertEqual(evidence.descriptor(meta(336)), evidence.EXIT)
        self.assertIn(evidence.ENTRY, table)
        self.assertIn(evidence.EXIT, table)

    def test_manifest_rejects_missing_duplicate_or_payload_setfocus_schema(self):
        variants = ([meta(335), meta(329)], [meta(335), meta(336), meta(335)],
                    [meta(335, [{'name': 'unexpected'}]), meta(336)])
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                evidence.schema_table(rows)

    def test_manifest_keeps_only_enabled_keyword_and_level(self):
        unrelated = meta(600)
        unrelated['keyword'] = '0x20000'
        verbose = dict(meta(601), level=5)
        table = evidence.schema_table([meta(335), meta(336), unrelated, verbose])
        self.assertEqual(set(table), {evidence.ENTRY, evidence.EXIT})

    def test_positive_pair_requires_real_ordered_chain_and_stores_only_rvas(self):
        value = capture()
        self.assertTrue(value.consume(header(335), reader(335)))
        self.assertTrue(value.consume(header(336, 2), reader(336)))
        report = value.finish(expect_focus=True, observation_complete=True)
        self.assertTrue(report['passed'])
        self.assertTrue(report['synchronous_chain_verified'])
        self.assertEqual((report['entries'], report['exits'], report['pairs']), (1, 1, 1))
        self.assertEqual(report['records'][0]['frames'][1],
                         {'module': 'uiautomationcore.dll', 'rva': 0x229928})
        self.assertNotIn(str(BASE + 0x229928), json.dumps(report))
        self.assertFalse(report['foreground_activation_verified'])
        self.assertFalse(report['live_client_verified'])

    def test_unrelated_known_events_do_not_call_reader_or_read_payload(self):
        value = capture()
        forbidden = Mock(side_effect=AssertionError('non-target stack forbidden'))
        data = header(509, data_length=64)
        data['data'] = object()
        self.assertTrue(value.consume(data, forbidden))
        forbidden.assert_not_called()
        report = value.finish(expect_focus=False, observation_complete=True)
        self.assertTrue(report['passed'])
        self.assertEqual(report['other_event_counts'], {'509': 1})
        self.assertEqual(report['records'], [])

    def test_no_events_does_not_qualify_negative_observation(self):
        self.assertFalse(capture().finish(expect_focus=False, observation_complete=True)['passed'])

    def test_known_other_thread_events_are_counted_without_payload_or_stack_reads(self):
        value = capture()
        forbidden = Mock(side_effect=AssertionError('other thread data forbidden'))
        self.assertTrue(value.consume(header(509, tid=60, data_length=64), forbidden))
        self.assertTrue(value.consume(header(329, 2, tid=61), forbidden))
        forbidden.assert_not_called()
        report = value.finish(expect_focus=False, observation_complete=True)
        self.assertFalse(report['passed'])  # Other threads do not establish the selected MTA's activity.
        self.assertEqual(report['other_events'], 0)
        self.assertEqual(report['other_thread_events'], 2)
        self.assertEqual(report['other_thread_event_counts'], {'509': 1, '329': 1})

    def test_other_thread_events_do_not_change_selected_thread_order_or_positive_chain(self):
        value = capture()
        value.consume(header(335, 10), reader(335))
        self.assertTrue(value.consume(header(329, 1, tid=60)))
        self.assertTrue(value.consume(header(336, 11), reader(336)))
        report = value.finish(expect_focus=True, observation_complete=True)
        self.assertTrue(report['passed'])
        self.assertEqual(report['other_thread_event_counts'], {'329': 1})

    def test_unknown_schema_or_setfocus_on_other_thread_still_rejects_before_stack(self):
        for event_id, reason in ((999, 'event_schema_unknown'), (335, 'setfocus_thread_mismatch'),
                                 (336, 'setfocus_thread_mismatch')):
            value, forbidden = capture(), Mock()
            self.assertFalse(value.consume(header(event_id, tid=60), forbidden))
            forbidden.assert_not_called()
            self.assertIn(reason, value.reasons)

    def test_other_thread_metadata_requires_valid_tid_and_never_accepts_foreign_process(self):
        for change in ({'tid': 0}, {'tid': True}, {'tid': 2**32}, {'pid': 99, 'tid': 60}):
            value = capture()
            self.assertFalse(value.consume(header(329, **change)))
            self.assertIn('event_source_mismatch', value.reasons)

    def test_entry_mode_never_reads_stack_or_claims_chain(self):
        value = capture(require_stack=False)
        forbidden = Mock(side_effect=AssertionError('entry mode stack forbidden'))
        value.consume(header(335), forbidden)
        value.consume(header(336, 2), forbidden)
        report = value.finish(expect_focus=True, observation_complete=True)
        forbidden.assert_not_called()
        self.assertTrue(report['passed'])
        self.assertFalse(report['synchronous_chain_verified'])

    def test_foreign_pid_tid_provider_or_descriptor_rejected_before_stack(self):
        variants = ({'pid': 9}, {'tid': 9}, {'pid': True}, {'provider': bytes(16)},
                    {'descriptor': evidence.ENTRY[:-1] + b'\x00'})
        for change in variants:
            with self.subTest(change=change):
                value, forbidden = capture(), Mock()
                self.assertFalse(value.consume(header(335, **change), forbidden))
                forbidden.assert_not_called()
                self.assertFalse(value.finish(expect_focus=False, observation_complete=True)['passed'])

    def test_unknown_schema_invalid_header_and_payload_fail_closed(self):
        variants = ({'descriptor': evidence.descriptor(meta(999))}, {'flags': 0x20},
                    {'flags': 0}, {'data_length': 1}, {'time': -1}, {'time': True})
        for change in variants:
            with self.subTest(change=change):
                value, forbidden = capture(), Mock()
                self.assertFalse(value.consume(header(335, **change), forbidden))
                forbidden.assert_not_called()

    def test_empty_other_schema_cannot_have_payload(self):
        value = capture()
        self.assertFalse(value.consume(header(329, data_length=1)))
        self.assertIn('empty_event_payload_mismatch', value.reasons)

    def test_missing_reversed_or_wrong_callsite_stack_rejected(self):
        stacks = ([], list(reversed(reader(335)())),
                  [BASE + rva for rva in evidence.CHAINS[335] if rva != 0x2269E1],
                  [BASE + rva + 1 for rva in evidence.CHAINS[335]], [BASE + 0x229928] * 65)
        for addresses in stacks:
            with self.subTest(addresses=addresses):
                value = capture()
                self.assertFalse(value.consume(header(335), lambda: addresses))
                self.assertFalse(value.finish(expect_focus=True, observation_complete=True)['passed'])

    def test_reader_exception_does_not_leak_exception_message(self):
        value = capture()
        value.consume(header(335), Mock(side_effect=RuntimeError('sensitive exception body')))
        report = value.finish(expect_focus=True, observation_complete=True)
        self.assertEqual(report['reasons'], {'stack_reader_failed': 1})
        self.assertNotIn('sensitive', json.dumps(report))

    def test_stack_address_must_map_to_one_known_module(self):
        value = capture()
        self.assertFalse(value.consume(header(335), lambda: reader(335)() + [0xDEADBEEF]))
        self.assertIn('stack_module_unknown', value.reasons)
        with self.assertRaises(ValueError):
            evidence.SetFocusEvidence(51, 52, schemas(), MODULES + [(BASE + 1, 20, 'overlap.dll')])

    def test_exit_without_entry_duplicate_entry_and_unclosed_entry_fail(self):
        value = capture()
        self.assertFalse(value.consume(header(336), reader(336)))
        value = capture()
        value.consume(header(335), reader(335))
        self.assertFalse(value.consume(header(335, 2), reader(335)))
        value = capture()
        value.consume(header(335), reader(335))
        self.assertFalse(value.finish(expect_focus=True, observation_complete=True)['passed'])

    def test_non_target_can_interleave_without_breaking_pair(self):
        value = capture()
        value.consume(header(335), reader(335))
        value.consume(header(329, 2))
        value.consume(header(336, 3), reader(336))
        self.assertTrue(value.finish(expect_focus=True, observation_complete=True)['passed'])

    def test_event_and_pair_limits_prevent_additional_stack_reads(self):
        value, forbidden = capture(max_events=1), Mock()
        value.consume(header(329))
        self.assertFalse(value.consume(header(335, 2), forbidden))
        forbidden.assert_not_called()
        value = capture(max_pairs=1)
        value.consume(header(335), reader(335))
        value.consume(header(336, 2), reader(336))
        self.assertFalse(value.consume(header(335, 3), forbidden))
        forbidden.assert_not_called()

    def test_phase_instances_are_independent_and_finished_instance_rejects_late_events(self):
        positive, negative = capture(), capture()
        positive.consume(header(335), reader(335))
        positive.consume(header(336, 2), reader(336))
        negative.consume(header(329))
        self.assertTrue(positive.finish(expect_focus=True, observation_complete=True)['passed'])
        self.assertTrue(negative.finish(expect_focus=False, observation_complete=True)['passed'])
        forbidden = Mock()
        self.assertFalse(negative.consume(header(335, 2), forbidden))
        forbidden.assert_not_called()
        self.assertFalse(negative.finish(expect_focus=False, observation_complete=True)['passed'])

    def test_lost_events_incomplete_observation_or_regressed_time_do_not_pass(self):
        for kwargs in ({'observation_complete': False}, {'observation_complete': True, 'events_lost': 1},
                       {'observation_complete': True, 'events_lost': False}):
            value = capture()
            value.consume(header(329))
            self.assertFalse(value.finish(expect_focus=False, **kwargs)['passed'])
        value = capture()
        value.consume(header(329, 2))
        self.assertFalse(value.consume(header(329, 1)))

    def test_failed_evidence_does_not_read_subsequent_target_stack(self):
        value = capture()
        value.consume(header(999))
        forbidden = Mock()
        self.assertFalse(value.consume(header(335, 2), forbidden))
        forbidden.assert_not_called()


if __name__ == '__main__':
    unittest.main()
