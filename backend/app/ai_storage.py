"""Global connections/defaults and effective conversation configuration."""
from datetime import datetime, timezone
from uuid import uuid4

from .ai_models import PRESETS
from .conversations import ConversationError
from .role_storage import conflict
from . import secret_protection


AI_SCHEMA = (
    "CREATE TABLE api_profiles(profile_id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,"
    "protocol TEXT NOT NULL,base_url TEXT NOT NULL,model TEXT NOT NULL,anthropic_version TEXT NOT NULL,"
    "workspace_id TEXT NOT NULL,secret BLOB NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)",
    "CREATE TABLE ai_defaults(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
    "role_card_code TEXT REFERENCES role_cards(card_code) ON DELETE RESTRICT,"
    "api_profile_id TEXT REFERENCES api_profiles(profile_id) ON DELETE RESTRICT,revision INTEGER NOT NULL)",
    "INSERT INTO ai_defaults VALUES(1,(SELECT card_code FROM role_cards ORDER BY created_at,card_code LIMIT 1),NULL,0)",
    "ALTER TABLE conversation_preferences ADD COLUMN api_profile_id TEXT REFERENCES api_profiles(profile_id) ON DELETE RESTRICT",
    "CREATE INDEX preferences_api ON conversation_preferences(api_profile_id)",
)


class AiStoreMixin:
    def _defaults(self):
        row = dict(self.db.execute("SELECT role_card_code,api_profile_id,revision FROM ai_defaults WHERE singleton=1").fetchone())
        return row

    def ai_defaults(self):
        return self.call(self._defaults)

    def _ai_notify(self):
        with self.db:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE conversation_id IN (SELECT conversation_id FROM conversation_preferences WHERE ai_enabled=1)")
            events = [self._ai_touch(r[0], r[1]) for r in self.db.execute("SELECT account_id,conversation_id FROM conversation_preferences").fetchall()]
        for event in events:
            self.notify(event)
        self.notify(dict(type="changed", resources=["api_profiles", "ai_defaults", "role_cards", "conversation_preferences", "conversations"]))

    def _usage(self, field, identity):
        default = self._defaults()[field]
        return self.db.execute(f"SELECT count(*) FROM conversation_preferences WHERE {field}=? "
                               f"OR ({field} IS NULL AND ai_enabled=1 AND ?=?)", (identity, default, identity)).fetchone()[0]

    def _profile(self, identity):
        row = self.db.execute("SELECT * FROM api_profiles WHERE profile_id=?", (identity,)).fetchone()
        if row is None:
            raise ConversationError("api_profile_not_found", "API 配置不存在，请重新选择。", 404)
        result = dict(row)
        secret = result.pop("secret")
        return dict(**result, is_default=self._defaults()["api_profile_id"] == identity,
                    usage_count=self._usage("api_profile_id", identity), has_key=bool(secret),
                    key_readable=secret_protection.verify(secret) if secret else False,
                    verification=self._verification(identity, result["revision"]))

    def api_profiles(self):
        def query():
            ids = self.db.execute("SELECT profile_id FROM api_profiles ORDER BY created_at,profile_id").fetchall()
            return dict(items=[self._profile(row[0]) for row in ids], presets=PRESETS)
        return self.call(query)

    def api_profile(self, identity):
        return self.call(self._profile, identity)

    def save_api_profile(self, fields, key=None, identity=None, expected_revision=None, verification=None, task=None):
        def write():
            timestamp = datetime.now(timezone.utc).isoformat()
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if identity:
                    previous = self._profile(identity)
                    if previous["revision"] != expected_revision:
                        conflict()
                    # Never include a decrypted key in result dictionaries or events.
                    assignments = ",".join(f"{name}=?" for name in fields)
                    self.db.execute(f"UPDATE api_profiles SET {assignments},revision=revision+1,updated_at=? WHERE profile_id=?",
                                    (*fields.values(), timestamp, identity))
                    if key is not None:
                        self.db.execute("UPDATE api_profiles SET secret=? WHERE profile_id=?", (secret_protection.protect(key), identity))
                    code = identity
                else:
                    code = "API_" + uuid4().hex
                    first = not self.db.execute("SELECT 1 FROM api_profiles LIMIT 1").fetchone()
                    self.db.execute("INSERT INTO api_profiles(profile_id,name,provider,protocol,base_url,model,anthropic_version,"
                                    "workspace_id,secret,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                                    (code, *(fields[name] for name in ("name", "provider", "protocol", "base_url", "model", "anthropic_version", "workspace_id")),
                                     secret_protection.protect(key), timestamp, timestamp))
                    if first:
                        self.db.execute("UPDATE ai_defaults SET api_profile_id=?,revision=revision+1 WHERE singleton=1", (code,))
                if verification:
                    import json
                    revision = self.db.execute("SELECT revision FROM api_profiles WHERE profile_id=?", (code,)).fetchone()[0]
                    self.db.execute("INSERT OR REPLACE INTO api_verifications VALUES(?,?,?)", (code, revision, json.dumps(verification)))
                result = self._profile(code)
                if task is not None:
                    task.update(status="completed", profile_id=code, profile=result, verification=verification, completed_at=timestamp)
                    self.db.execute("UPDATE api_tasks SET payload=? WHERE task_id=?", (json.dumps(task), task["task_id"]))
            self._ai_notify()
            return result
        return self.call(write)

    def delete_api_profile(self, identity, expected_revision):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                profile = self._profile(identity)
                if profile["revision"] != expected_revision:
                    conflict()
                if profile["usage_count"]:
                    raise ConversationError("api_profile_in_use", f"API 配置正被 {profile['usage_count']} 个会话使用，请先更换配置或解除引用。", 409)
                self.db.execute("UPDATE ai_defaults SET api_profile_id=NULL,revision=revision+1 WHERE api_profile_id=?", (identity,))
                self.db.execute("DELETE FROM api_profiles WHERE profile_id=?", (identity,))
            self._ai_notify()
            return dict(profile_id=identity, deleted=True)
        return self.call(write)

    def save_ai_defaults(self, expected_revision, patch):
        def write():
            changed = False
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                previous = self._defaults()
                if previous["revision"] != expected_revision:
                    conflict()
                if "role_card_code" in patch:
                    self._card(patch["role_card_code"], False)
                if "api_profile_id" in patch:
                    if not self._profile(patch["api_profile_id"])["key_readable"]:
                        raise ConversationError("api_key_unreadable", "密钥无法读取，请在 Agent 中重新填写。", 409)
                current = {**previous, **patch}
                if current != previous:
                    self.db.execute("UPDATE ai_defaults SET role_card_code=?,api_profile_id=?,revision=revision+1 WHERE singleton=1",
                                    (current["role_card_code"], current["api_profile_id"]))
                    changed = True
                result = self._defaults()
            if changed:
                self._ai_notify()
            return result
        return self.call(write)

    def _effective(self, preferences):
        defaults = self._defaults()
        role = preferences["role_card_code"] or defaults["role_card_code"]
        profile = preferences["api_profile_id"] or defaults["api_profile_id"]
        issues = []
        role_name = self._card(role, False)["name"] if role else None
        if not role:
            issues.append(dict(code="default_role_card_missing", message="未配置默认角色卡，请选择角色卡或先设置全局默认角色卡。"))
        api = self._profile(profile) if profile else None
        if not api:
            issues.append(dict(code="default_api_profile_missing", message="未配置默认 API，请选择 API 配置或先在 Agent 中设置全局默认 API。"))
        elif not api["key_readable"]:
            issues.append(dict(code="api_key_unreadable", message="API 密钥无法读取，请在 Agent 中重新填写。"))
        elif api["verification"]["status"] != "passed":
            issues.append(dict(code="api_profile_unverified", message="API 尚未通过可用性测试，请在 Agent 中测试后再启用。"))
        return dict(role_card_code=role, role_card_name=role_name, api_profile_id=profile,
                    api_profile_name=api["name"] if api else None,
                    role_source="explicit" if preferences["role_card_code"] else "default",
                    api_source="explicit" if preferences["api_profile_id"] else "default",
                    ready=not issues, issues=issues)

    def _validate_ai(self, preferences):
        effective = self._effective(preferences)
        if effective["issues"]:
            issues = effective["issues"]
            raise ConversationError(issues[0]["code"] if len(issues) == 1 else "ai_configuration_missing",
                                    "；".join(issue["message"] for issue in issues), 409)
