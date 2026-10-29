import sqlite3
import time
import unittest
from unittest.mock import Mock

from wechat_account_probe.models import ProbeError
from wechat_conversations.reader import project_sessions, classify, memory_database, SessionReader


def session(username, **values):
    return dict(username=username, unread_count=2, summary="合成消息摘要", last_timestamp=1780000000,
                sort_timestamp=1780000000, is_hidden=0, **values)


def contact(**values):
    return dict(nick_name="相同的合成昵称 @ 普通名字", flag=3, verify_flag=0, **values)


class ProjectionTests(unittest.TestCase):
    def test_seven_categories_and_container_children(self):
        users = ["brandservicesessionholder", "brandsessionholder", "notifymessage", "filehelper",
                 "@placeholder_foldgroup", "gh_3dfda90e39d6", "user@openim", "normal", "weixin",
                 "folded@chatroom", "gh_service", "gh_official"]
        contacts = {u: contact() for u in users}
        contacts["folded@chatroom"]["flag"] = 0x10000002
        biz = {"gh_service": {"type": 1}, "gh_official": {"type": 0},
               "gh_3dfda90e39d6": {"type": 1}, "weixin": {"type": 1}}
        items, issues = project_sessions([session(u) for u in users], contacts, biz)
        self.assertEqual(len(items), 9)
        self.assertEqual(len({i["category"] for i in items}), 9)
        self.assertEqual(sum(i["is_container"] for i in items), 3)
        self.assertFalse(issues)
        self.assertEqual(classify("normal", contacts["normal"], {})[0], "contact")
        self.assertEqual(classify("weixin", {}, {"type": 1})[0], "system")
        self.assertEqual(classify("gh_3dfda90e39d6", {}, {"type": 1})[0], "wechat_pay")

    def test_missing_metadata_and_missing_container_are_explicit(self):
        rows = [session("folded@chatroom"), session("missing"), session("no_flag@chatroom")]
        items, issues = project_sessions(rows, {"folded@chatroom": {"flag": 0x10000000}}, {})
        self.assertEqual(len(items), 2)
        self.assertIn("folded_chats_container_missing", issues)
        self.assertIn("conversation_classification_incomplete", issues)
        self.assertEqual(items[0]["category"], "unknown")

    def test_same_names_remain_distinct_and_database_sort_is_preserved(self):
        rows = [session("first"), session("second"), session("hidden")]
        rows[1]["sort_timestamp"] += 10
        rows[2]["is_hidden"] = 1
        items, issues = project_sessions(rows, {u: contact() for u in ("first", "second", "hidden")}, {})
        self.assertEqual([i["source_id"] for i in items], ["second", "first"])
        self.assertEqual(items[0]["name"], items[1]["name"])
        self.assertFalse(issues)

    def test_invalid_fields_are_not_fabricated_and_duplicate_ids_fail(self):
        row = session("user")
        row.update(unread_count=-1, last_timestamp=10**99, summary=b"bad", sort_timestamp=None)
        items, issues = project_sessions([row], {"user": contact()}, {})
        self.assertIsNone(items[0]["unread_count"])
        self.assertIsNone(items[0]["summary"])
        self.assertIsNone(items[0]["last_message_at"])
        self.assertEqual(len(issues), 4)
        with self.assertRaisesRegex(ProbeError, "session_identity_invalid"):
            project_sessions([row, row], {}, {})

    def test_read_only_memory_database_denies_unrelated_queries(self):
        db = sqlite3.connect(":memory:")
        db.executescript("CREATE TABLE allowed(value TEXT); INSERT INTO allowed VALUES ('synthetic'); CREATE TABLE secret(value TEXT);")
        image = db.serialize()
        db.close()
        with memory_database(image, {"allowed": {"value"}}, lambda: None) as db:
            self.assertEqual(db.execute("SELECT value FROM allowed").fetchone()[0], "synthetic")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("SELECT value FROM secret")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM allowed")
        with self.assertRaisesRegex(ProbeError, "conversation_schema_unsupported"):
            with memory_database(image, {"allowed": {"missing"}}, lambda: None):
                pass

    def test_reader_queries_only_summary_databases_and_rechecks_binding(self):
        images = {}
        for name, sql in {
            "session/session.db": "CREATE TABLE SessionTable(username TEXT,unread_count INTEGER,summary TEXT,last_timestamp INTEGER,sort_timestamp INTEGER,is_hidden INTEGER); INSERT INTO SessionTable VALUES ('user',3,'合成摘要',1780000000,1780000000,0); CREATE TABLE SessionNoContactInfoTable(username TEXT,session_title TEXT);",
            "contact/contact.db": "CREATE TABLE contact(username TEXT,remark TEXT,nick_name TEXT,flag INTEGER,verify_flag INTEGER); INSERT INTO contact VALUES ('user','','合成好友',3,0); CREATE TABLE biz_info(username TEXT,type INTEGER);",
            "head_image/head_image.db": "CREATE TABLE head_image(username TEXT,image_buffer BLOB);",
        }.items():
            db = sqlite3.connect(":memory:")
            db.executescript(sql)
            images[name] = db.serialize()
            db.close()
        reader = SessionReader(None, "synthetic")
        reader.bind = Mock()
        reader.memory = Mock()
        reader.image = Mock(side_effect=lambda name: images[name])
        result = reader.read(time.monotonic() + 60)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["items"][0]["name"], "合成好友")
        self.assertEqual(reader.bind.call_count, 2)
        self.assertEqual(reader.image.call_count, 3)


if __name__ == "__main__":
    unittest.main()
