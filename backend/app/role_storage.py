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
        value["usage_count"] = self._usage("role_card_code", code)
        value["is_default"] = self._defaults()["role_card_code"] == code
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
            return dict(items=[self._card(row["card_code"], False) for row in rows])
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
                    first = not self.db.execute("SELECT 1 FROM role_cards LIMIT 1").fetchone()
                    self.db.execute("INSERT INTO role_cards VALUES(?,?,?,1,?,?)",
                                    (identity, card["data"]["name"], json.dumps(card, ensure_ascii=False, allow_nan=False), timestamp, timestamp))
                    if first:
                        self.db.execute("UPDATE ai_defaults SET role_card_code=?,revision=revision+1 WHERE singleton=1", (identity,))
                result = self._card(identity)
            self._ai_notify()
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
                self.db.execute("UPDATE ai_defaults SET role_card_code=NULL,revision=revision+1 WHERE role_card_code=?", (code,))
                self.db.execute("DELETE FROM role_cards WHERE card_code=?", (code,))
            self._ai_notify()
            return dict(card_code=code, deleted=True)
        return self.call(write)

    def _preferences(self, aid, cid):
        self._account(aid)
        self._conversation(aid, cid)
        row = self.db.execute("SELECT * FROM conversation_preferences WHERE account_id=? AND conversation_id=?", (aid, cid)).fetchone()
        if row:
            return {**dict(row), "ai_enabled": bool(row["ai_enabled"])}
        return dict(account_id=aid, conversation_id=cid, ai_enabled=False, role_card_code=None, api_profile_id=None, memory="", revision=0, memory_revision=0,
                    scan_interval_seconds=2, group_proactive_message_threshold=0)

    def preferences(self, aid, cid):
        def query():
            value = self._preferences(aid, cid)
            return {**value, "effective": self._effective(value)}
        return self.call(query)

    def save_preferences(self, aid, cid, expected_revision, patch, expected_memory_revision=None):
        def write():
            event = None
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                previous = self._preferences(aid, cid)
                if previous["revision"] != expected_revision:
                    conflict()
                if "memory" in patch and previous["memory_revision"] != (expected_memory_revision or 0):
                    raise ConversationError("ai_memory_conflict", "记忆已更新，当前草稿保留，请重新加载后保存。", 409)
                current = {**previous, **patch}
                for field, low, high in (("scan_interval_seconds", 1, 3600), ("group_proactive_message_threshold", 0, 1000)):
                    if type(current[field]) is not int or not low <= current[field] <= high:
                        raise ConversationError("invalid_request", "扫描间隔或群聊条数设置无效。", 422)
                if current["group_proactive_message_threshold"] and not self._is_group(aid, cid):
                    raise ConversationError("invalid_request", "只有群聊可以设置主动调用条数。", 422)
                if current["role_card_code"]:
                    self._card(current["role_card_code"], False)
                if current["api_profile_id"]:
                    self._profile(current["api_profile_id"])
                if current["ai_enabled"] and (patch.get("ai_enabled") is True or any(
                        current[field] != previous[field] for field in ("role_card_code", "api_profile_id"))):
                    self._validate_ai(current)
                if current != previous:
                    current["revision"] += 1
                    current["memory_revision"] += int(current["memory"] != previous["memory"])
                    self.db.execute("INSERT INTO conversation_preferences(account_id,conversation_id,ai_enabled,role_card_code,memory,revision,api_profile_id,memory_revision,scan_interval_seconds,group_proactive_message_threshold) VALUES(?,?,?,?,?,?,?,?,?,?) "
                                    "ON CONFLICT(account_id,conversation_id) DO UPDATE SET ai_enabled=excluded.ai_enabled, "
                                    "role_card_code=excluded.role_card_code,memory=excluded.memory,revision=excluded.revision,api_profile_id=excluded.api_profile_id,memory_revision=excluded.memory_revision,scan_interval_seconds=excluded.scan_interval_seconds,group_proactive_message_threshold=excluded.group_proactive_message_threshold",
                                    (aid, cid, current["ai_enabled"], current["role_card_code"], current["memory"], current["revision"], current["api_profile_id"], current["memory_revision"], current["scan_interval_seconds"], current["group_proactive_message_threshold"]))
                    self._ai_preference_change(previous, current)
                    event = self._changed(aid, ["conversation_preferences", "conversations"], [cid])
            if event:
                self.notify(event)
                if any(previous[field] != current[field] for field in ("role_card_code", "api_profile_id", "ai_enabled")):
                    self.notify(dict(type="changed", resources=["role_cards", "api_profiles"]))
            return {**current, "effective": self._effective(current)}
        return self.call(write)
