from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.storage import Store
from backend.app.services import browser_values
from backend.app.conversations import ConversationError
from test_conversations import accounts, rows, Reader


def card(name="合成角色"):
    return dict(spec="chara_card_v2", spec_version="2.0", vendor={"keep": [1, True, None]}, data=dict(
        name=name, description="  第一行\n第二行  ", personality="", scenario="", first_mes="", mes_example="",
        creator_notes="作者说明", system_prompt="{{original}}\n{{char}}", post_history_instructions="",
        alternate_greetings=["你好", "欢迎"], tags=["测试"], creator="作者", character_version="1",
        extensions={"vendor/unknown": {"nested": [1, {"value": False}]}}, unknown_field="保留",
        character_book=dict(extensions={"extra": []}, entries=[dict(keys=["厦门"], content="长文本", enabled=True,
            insertion_order=1, extensions={"any": True}, unknown_entry={"keep": "yes"})])))


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.sqlite3"
        self.app = create_app(accounts, database_path=self.path, conversation_reader=Reader(), monitor=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        self.client.__enter__()
        self.store = self.app.state.store
        self.ids = [a["account_id"] for a in self.store.save_accounts(browser_values(accounts()))["accounts"]]
        self.cids = []
        for aid in self.ids:
            self.store.save_conversations(aid, dict(items=rows(1), status="complete", issues=[]))
            self.cids.append(self.store.conversations(aid)["items"][0]["conversation_id"])

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def post(self, path, **body):
        return self.client.post("/api/v1/" + path, json=body)

    def create(self, document=None):
        response = self.post("role-cards/create", card=card() if document is None else document)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def preferences(self, index=0):
        return self.post("conversation-preferences/query", account_id=self.ids[index], conversation_id=self.cids[index]).json()

    def update(self, revision=0, index=0, **fields):
        return self.post("conversation-preferences/update", account_id=self.ids[index], conversation_id=self.cids[index], expected_revision=revision, **fields)

    def test_v2_roundtrip_duplicate_names_updates_and_conflicts(self):
        original = card()
        first, second = self.create(original), self.create(original)
        self.assertNotEqual(first["card_code"], second["card_code"])
        self.assertTrue(first["card_code"].startswith("RC_"))
        self.assertEqual(first["card"], original)
        self.assertEqual(len(self.post("role-cards/query").json()["items"]), 2)
        changed = deepcopy(original); changed["data"]["name"] = "更新角色"
        updated = self.post("role-cards/update", card_code=first["card_code"], expected_revision=1, card=changed)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 2)
        stale = self.post("role-cards/update", card_code=first["card_code"], expected_revision=1, card=original)
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "revision_conflict"))
        self.assertEqual(self.post("role-cards/get", card_code=first["card_code"]).json()["card"], changed)

    def test_defaults_partial_changes_preserve_memory_and_account_isolation(self):
        self.assertEqual(self.preferences(), dict(account_id=self.ids[0], conversation_id=self.cids[0], ai_enabled=False, role_card_code=None, memory="", revision=0))
        role = self.create()
        memory = "  中文记忆\n\n" + "长文本\t " * 25000
        saved = self.update(role_card_code=role["card_code"], memory=memory).json()
        self.assertEqual(saved["revision"], 1)
        enabled = self.update(1, ai_enabled=True).json()
        self.assertEqual(enabled["memory"], memory)
        self.assertTrue(enabled["ai_enabled"])
        disabled = self.update(2, ai_enabled=False).json()
        self.assertEqual(disabled["memory"], memory)
        cleared_role = self.update(3, role_card_code=None).json()
        self.assertEqual(cleared_role["memory"], memory)
        self.assertIsNone(cleared_role["role_card_code"])
        self.assertEqual(self.preferences(1)["memory"], "")
        self.assertEqual(self.update(4, memory="").json()["memory"], "")
        self.store.save_conversations(self.ids[0], dict(items=rows(1), status="complete", issues=[]))
        self.assertEqual(self.preferences()["revision"], 5)
        row = self.post("conversations/query", account_id=self.ids[0]).json()["items"][0]
        self.assertEqual((row["ai_enabled"], row["preferences_revision"]), (False, 5))
        self.assertNotIn("memory", row)

    def test_used_card_deletion_is_blocked_and_unbinding_allows_deletion(self):
        role = self.create(); code = role["card_code"]
        self.update(role_card_code=code)
        self.update(index=1, role_card_code=code)
        self.assertEqual(self.post("role-cards/get", card_code=code).json()["usage_count"], 2)
        blocked = self.post("role-cards/delete", card_code=code, expected_revision=1)
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "role_card_in_use"))
        self.update(1, role_card_code=None)
        self.update(1, index=1, role_card_code=None)
        self.assertEqual(self.post("role-cards/delete", card_code=code, expected_revision=1).json(), dict(card_code=code, deleted=True))
        self.assertEqual(self.update(2, role_card_code=code).status_code, 404)

    def test_concurrent_binding_and_deletion_never_leave_dangling_reference(self):
        role = self.create(); code = role["card_code"]
        def bind():
            try: return self.store.save_preferences(self.ids[0], self.cids[0], 0, dict(role_card_code=code))
            except ConversationError as exc: return exc.code
        def delete():
            try: return self.store.delete_role_card(code, 1)
            except ConversationError as exc: return exc.code
        with ThreadPoolExecutor(2) as executor:
            one, two = executor.submit(bind), executor.submit(delete)
            results = [one.result(), two.result()]
        self.assertEqual(sum(isinstance(result, str) for result in results), 1)
        self.assertEqual(self.store.call(lambda: self.store.db.execute("PRAGMA foreign_key_check").fetchall()), [])

    def test_invalid_v2_types_json_and_cross_account_access(self):
        for field, value in [("spec", "chara_card_v3"), ("spec_version", "3.0")]:
            doc = card(); doc[field] = value
            self.assertEqual(self.post("role-cards/create", card=doc).status_code, 422)
        for field, value in [("name", "  "), ("tags", "bad"), ("description", 1), ("extensions", [])]:
            doc = card(); doc["data"][field] = value
            self.assertEqual(self.post("role-cards/create", card=doc).status_code, 422)
        malformed = self.client.post("/api/v1/role-cards/create", content="{broken", headers={"Content-Type": "application/json"})
        self.assertEqual((malformed.status_code, set(malformed.json())), (422, {"code", "message"}))
        self.assertEqual(self.client.post("/api/v1/role-cards/query", content="{}").status_code, 415)
        for fields in ({}, {"memory": None}, {"ai_enabled": None}, {"ai_enabled": 1}, {"role_card_code": "missing"}):
            self.assertIn(self.update(**fields).status_code, (422, 404))
        self.assertEqual(self.post("conversation-preferences/query", account_id=self.ids[1], conversation_id=self.cids[0]).status_code, 404)
        self.assertEqual(self.post("conversation-preferences/update", account_id=self.ids[1], conversation_id=self.cids[0], expected_revision=0, memory="wrong").status_code, 404)

    def test_stale_preferences_and_failed_write_do_not_overwrite_or_notify(self):
        self.update(memory="original")
        failed = self.update(memory="stale")
        self.assertEqual(failed.json()["code"], "revision_conflict")
        events = []; self.store.notify = events.append
        self.store.call(lambda: self.store.db.execute("CREATE TRIGGER fail_pref BEFORE UPDATE ON conversation_preferences BEGIN SELECT RAISE(ABORT,'synthetic'); END"))
        self.assertEqual(self.update(1, memory="lost").status_code, 503)
        self.assertEqual(self.preferences()["memory"], "original")
        self.assertEqual(events, [])

    def test_notifications_are_global_for_cards_scoped_for_preferences_and_contain_no_content(self):
        events = []; self.store.notify = events.append
        role = self.create()
        self.assertNotIn("account_id", events[0])
        self.update(memory="private-memory", role_card_code=role["card_code"])
        scoped = next(e for e in events if "conversation_preferences" in e["resources"])
        self.assertEqual(scoped["account_id"], self.ids[0])
        self.assertEqual(scoped["conversation_ids"], [self.cids[0]])
        self.assertNotIn("private-memory", json.dumps(events))
        self.assertNotIn("合成角色", json.dumps(events, ensure_ascii=False))

    def test_restart_preserves_roles_preferences_without_source(self):
        role = self.create()
        self.update(role_card_code=role["card_code"], ai_enabled=True, memory="\n saved \n")
        self.store.close()
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.role_card(role["card_code"])["card"], card())
            self.assertEqual(reopened.preferences(self.ids[0], self.cids[0])["memory"], "\n saved \n")
            self.assertTrue(reopened.conversations(self.ids[0])["items"][0]["ai_enabled"])
        finally: reopened.close()


class RoleMigrationTests(unittest.TestCase):
    def test_v2_upgrade_failure_rolls_back_and_retries_without_losing_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.sqlite3"
            store = Store(path)
            aid = store.save_accounts(browser_values(accounts()))["accounts"][0]["account_id"]
            store.save_conversations(aid, dict(items=rows(1), status="complete", issues=[]))
            cid = store.conversations(aid)["items"][0]["conversation_id"]
            store.close()
            with closing(sqlite3.connect(path)) as db:
                db.executescript("DROP TABLE conversation_preferences; DROP TABLE role_cards; DROP INDEX conversation_owner; DELETE FROM schema_migrations WHERE version=3; PRAGMA user_version=2;")
            from backend.app.storage import ROLE_SCHEMA
            with patch("backend.app.storage.ROLE_SCHEMA", (*ROLE_SCHEMA, "INVALID SQL")):
                with self.assertRaises(ConversationError): Store(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='role_cards'").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM conversations").fetchone()[0], 1)
            store = Store(path)
            try:
                self.assertEqual(store.preferences(aid, cid)["memory"], "")
                self.assertEqual(store.call(lambda: store.db.execute("PRAGMA user_version").fetchone()[0]), 3)
            finally: store.close()
