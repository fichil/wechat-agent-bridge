"""Local-only role documents and preferences. Invoked on Store's owner thread."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from .conversations import ConversationError


ROLE_SCHEMA = (
    "CREATE TABLE role_cards(card_code TEXT PRIMARY KEY, name TEXT NOT NULL, card TEXT NOT NULL, "
    "revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX conversation_owner ON conversations(account_id,conversation_id)",
    "CREATE TABLE conversation_preferences(account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
    "ai_enabled INTEGER NOT NULL DEFAULT 0 CHECK(ai_enabled IN (0,1)), "
    "role_card_code TEXT REFERENCES role_cards(card_code) ON DELETE RESTRICT, memory TEXT NOT NULL DEFAULT '', "
    "revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id,conversation_id), "
    "FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id))",
    "CREATE INDEX preferences_role ON conversation_preferences(role_card_code)",
)


def conflict():
    raise ConversationError("revision_conflict", "内容已在其他位置更新，请重新加载后再保存。", 409)


class RoleStoreMixin:
    def _card(self, code, detail=True):
        row = self.db.execute("SELECT r.*, (SELECT count(*) FROM conversation_preferences p "
                              "WHERE p.role_card_code=r.card_code) AS usage_count FROM role_cards r WHERE card_code=?",
                              (code,)).fetchone()
        if row is None:
            raise ConversationError("role_card_not_found", "角色卡不存在，请重新选择。", 404)
        value = dict(row)
        raw = value.pop("card")
        if detail:
            value["card"] = json.loads(raw)
        return value

    def role_cards(self):
        def query():
            rows = self.db.execute("SELECT r.card_code,r.name,r.revision,r.created_at,r.updated_at, "
                                   "count(p.conversation_id) AS usage_count FROM role_cards r "
                                   "LEFT JOIN conversation_preferences p ON p.role_card_code=r.card_code "
                                   "GROUP BY r.card_code ORDER BY r.updated_at DESC,r.card_code").fetchall()
            return dict(items=[dict(row) for row in rows])
        return self.call(query)

    def role_card(self, code):
        return self.call(self._card, code)

    def save_role_card(self, card, code=None, expected_revision=None):
        def write():
            identity = code or "RC_" + uuid4().hex
            timestamp = datetime.now(timezone.utc).isoformat()
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if code:
                    previous = self._card(code, False)
                    if previous["revision"] != expected_revision:
                        conflict()
                    self.db.execute("UPDATE role_cards SET name=?,card=?,revision=revision+1,updated_at=? WHERE card_code=?",
                                    (card["data"]["name"], json.dumps(card, ensure_ascii=False, allow_nan=False), timestamp, code))
                else:
                    self.db.execute("INSERT INTO role_cards VALUES(?,?,?,1,?,?)",
                                    (identity, card["data"]["name"], json.dumps(card, ensure_ascii=False, allow_nan=False), timestamp, timestamp))
                result = self._card(identity)
            self.notify(dict(type="changed", resources=["role_cards"]))
            return result
        return self.call(write)

    def delete_role_card(self, code, expected_revision):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                card = self._card(code, False)
                if card["revision"] != expected_revision:
                    conflict()
                if card["usage_count"]:
                    raise ConversationError("role_card_in_use", f"角色卡正被 {card['usage_count']} 个会话使用，请先解除全部引用。", 409)
                self.db.execute("DELETE FROM role_cards WHERE card_code=?", (code,))
            self.notify(dict(type="changed", resources=["role_cards"]))
            return dict(card_code=code, deleted=True)
        return self.call(write)

    def _preferences(self, aid, cid):
        self._account(aid)
        self._conversation(aid, cid)
        row = self.db.execute("SELECT * FROM conversation_preferences WHERE account_id=? AND conversation_id=?", (aid, cid)).fetchone()
        if row:
            return {**dict(row), "ai_enabled": bool(row["ai_enabled"])}
        return dict(account_id=aid, conversation_id=cid, ai_enabled=False, role_card_code=None, memory="", revision=0)

    def preferences(self, aid, cid):
        return self.call(self._preferences, aid, cid)

    def save_preferences(self, aid, cid, expected_revision, patch):
        def write():
            event = None
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                previous = self._preferences(aid, cid)
                if previous["revision"] != expected_revision:
                    conflict()
                current = {**previous, **patch}
                if current["role_card_code"]:
                    self._card(current["role_card_code"], False)
                if current != previous:
                    current["revision"] += 1
                    self.db.execute("INSERT INTO conversation_preferences VALUES(?,?,?,?,?,?) "
                                    "ON CONFLICT(account_id,conversation_id) DO UPDATE SET ai_enabled=excluded.ai_enabled, "
                                    "role_card_code=excluded.role_card_code,memory=excluded.memory,revision=excluded.revision",
                                    (aid, cid, current["ai_enabled"], current["role_card_code"], current["memory"], current["revision"]))
                    event = self._changed(aid, ["conversation_preferences", "conversations"], [cid])
            if event:
                self.notify(event)
                if previous["role_card_code"] != current["role_card_code"]:
                    self.notify(dict(type="changed", resources=["role_cards"]))
            return current
        return self.call(write)
