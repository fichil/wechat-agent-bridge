"""Synthetic TDH shapes only; no installed driver, ETW or window calls."""
import copy
import unittest

from scripts.review_message_event_source import SCHEMA, checked_layout


def event_fixture():
    return {"id": 60, "version": 0, "channel": 21, "level": 4, "opcode": 1,
            "task_id": 60, "keyword": "0x400000000400000",
            "fields": [{"name": name, "in_type": kind, "count_or_index": 1,
                        "length_or_index": size, "top_level": True, "flags": 0}
                       for name, kind, size in SCHEMA]}


class MessageEventSourceTests(unittest.TestCase):
    def test_packed_pid_tid_offsets_have_no_c_structure_padding(self):
        layout = checked_layout(event_fixture())
        fields = {field["name"]: field for field in layout["fields"]}
        self.assertEqual(layout["bytes"], 53)
        self.assertEqual(fields["pidReceiver"]["offset"], 45)
        self.assertEqual(fields["tidReceiver"]["offset"], 49)

    def test_wrong_version_or_pointer_width_rejected(self):
        event = event_fixture()
        event["version"] = 1
        with self.assertRaisesRegex(ValueError, "identity"):
            checked_layout(event)
        with self.assertRaisesRegex(ValueError, "pointer_width"):
            checked_layout(event_fixture(), 4)

    def test_missing_or_reordered_identity_fields_rejected(self):
        event = event_fixture()
        for change in ("missing", "reordered"):
            sample = copy.deepcopy(event)
            if change == "missing":
                sample["fields"].pop()
            else:
                sample["fields"][-2:] = reversed(sample["fields"][-2:])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "schema"):
                checked_layout(sample)

    def test_variable_or_pointer_receiver_id_rejected(self):
        for key, value in (("in_type", 16), ("flags", 2), ("count_or_index", 2),
                           ("length_or_index", 8), ("top_level", False)):
            event = event_fixture()
            event["fields"][-1][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "schema"):
                checked_layout(event)


if __name__ == "__main__":
    unittest.main()
