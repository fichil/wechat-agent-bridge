"""Durable AI jobs and versioned message coverage, owned by Store's SQL thread."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import time
from uuid import uuid4
from .ai_execution_models import DEFAULT_INSTRUCTIONS
from .conversations import ConversationError
from .role_storage import conflict
from . import secret_protection

def stamp():
    return datetime.now(timezone.utc).isoformat()

def pack(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)

EXECUTION_SCHEMA = (
    "ALTER TABLE conversation_preferences ADD memory_revision INTEGER NOT NULL DEFAULT 0",
    "CREATE TABLE ai_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1), instructions TEXT NOT NULL, revision INTEGER NOT NULL)",
    "CREATE TABLE api_verifications(profile_id TEXT PRIMARY KEY REFERENCES api_profiles ON DELETE CASCADE, revision INTEGER NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE api_tasks(task_id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE ai_sessions(account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0, initial_seq INTEGER NOT NULL DEFAULT 0, auto_pending INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id,conversation_id), FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id))",
    "CREATE TABLE ai_runs(run_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(account_id,conversation_id,request_id), FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id))",
    "CREATE INDEX ai_runs_conversation ON ai_runs(account_id,conversation_id)",
    "CREATE UNIQUE INDEX ai_one_active ON ai_runs(account_id,conversation_id) WHERE status IN ('queued','running')",
    "CREATE TABLE ai_message_state(message_id TEXT PRIMARY KEY REFERENCES messages ON DELETE CASCADE, fingerprint TEXT NOT NULL, media_digest TEXT, status TEXT NOT NULL, run_id TEXT, attempted_at TEXT, memorized_at TEXT)",
    "CREATE TABLE ai_run_messages(run_id TEXT NOT NULL REFERENCES ai_runs ON DELETE CASCADE, message_id TEXT NOT NULL REFERENCES messages, fingerprint TEXT NOT NULL, PRIMARY KEY(run_id,message_id))",
)

class AiExecutionStoreMixin:
    def _ai_recover(self):
        for row in self.db.execute("SELECT * FROM ai_runs WHERE status IN ('queued','running')").fetchall():
            payload = json.loads(row["payload"])
            payload.update(status="failed", error="ai_interrupted", message="服务在调用完成前停止，请手动重试。", completed_at=stamp())
            self._ai_write_run(payload)
            self.db.execute("UPDATE ai_sessions SET paused=1 WHERE account_id=? AND conversation_id=?", (row["account_id"], row["conversation_id"]))
            self.db.execute("UPDATE ai_message_state SET status='failed' WHERE run_id=? AND status='processing'", (row["run_id"],))
        for row in self.db.execute("SELECT * FROM api_tasks").fetchall():
            task = json.loads(row["payload"])
            if task["status"] in ("queued", "running"):
                task.update(status="failed", error="ai_interrupted", message="服务已重启，配置未保存，请重新提交。", completed_at=stamp())
                self.db.execute("UPDATE api_tasks SET payload=? WHERE task_id=?", (pack(task), row["task_id"]))

    def ai_settings(self):
        return self.call(lambda: {**dict(self.db.execute("SELECT instructions,revision FROM ai_settings WHERE singleton=1").fetchone()), "default_instructions": DEFAULT_INSTRUCTIONS})

    def save_ai_settings(self, expected, instructions):
        if not instructions.strip():
            raise ConversationError("invalid_request", "AI 提示词不能为空。", 422)
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if self.db.execute("SELECT revision FROM ai_settings").fetchone()[0] != expected:
                    conflict()
                self.db.execute("UPDATE ai_settings SET instructions=?,revision=revision+1", (instructions,))
            self.notify(dict(type="changed", resources=["ai_settings"]))
        self.call(write)
        return self.ai_settings()

    def _verification(self, profile_id, revision):
        row = self.db.execute("SELECT payload FROM api_verifications WHERE profile_id=? AND revision=?", (profile_id, revision)).fetchone()
        return json.loads(row[0]) if row else dict(status="unverified", vision="unverified", output_mode="schema")

    def ai_secret(self, profile_id, expected=None):
        def query():
            profile = self._profile(profile_id)
            if expected is not None and profile["revision"] != expected:
                conflict()
            raw = self.db.execute("SELECT secret FROM api_profiles WHERE profile_id=?", (profile_id,)).fetchone()[0]
            return profile, secret_protection.reveal(raw)
        return self.call(query)

    def save_verification(self, profile_id, expected, verification, task=None):
        def write():
            with self.db:
                if self._profile(profile_id)["revision"] != expected:
                    conflict()
                self.db.execute("INSERT OR REPLACE INTO api_verifications VALUES(?,?,?)", (profile_id, expected, pack(verification)))
                if verification["status"] == "passed":
                    self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE conversation_id IN (SELECT conversation_id FROM conversation_preferences WHERE ai_enabled=1)")
                if task is not None:
                    task.update(status="completed", verification=verification, profile=self._profile(profile_id), completed_at=stamp())
                    self.db.execute("UPDATE api_tasks SET payload=? WHERE task_id=?", (pack(task), task["task_id"]))
            self._ai_notify()
        self.call(write)

    def api_task(self, task_id, value=None):
        def query():
            if value is not None:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO api_tasks VALUES(?,?)", (task_id, pack(value)))
                self.notify(dict(type="changed", resources=["api_tasks", "api_profiles"]))
            row = self.db.execute("SELECT payload FROM api_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise ConversationError("ai_task_not_found", "测试任务不存在。", 404)
            return json.loads(row[0])
        return self.call(query)

    def _ai_session(self, aid, cid):
        row = self.db.execute("SELECT * FROM ai_sessions WHERE account_id=? AND conversation_id=?", (aid, cid)).fetchone()
        return dict(row) if row else dict(epoch=0, initial_seq=0, auto_pending=0, paused=0)

    def _ai_preference_change(self, previous, current):
        aid, cid = current["account_id"], current["conversation_id"]
        self._delivery_preference_change(previous, current)
        self.db.execute("INSERT OR IGNORE INTO ai_sessions(account_id,conversation_id) VALUES(?,?)", (aid, cid))
        self._group_preferences_changed(previous, current)
        if current["ai_enabled"] and not previous["ai_enabled"]:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
        if any(previous[k] != current[k] for k in ("role_card_code", "api_profile_id")):
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
        if previous["ai_enabled"] and not current["ai_enabled"]:
            self.db.execute("UPDATE ai_sessions SET epoch=epoch+1,auto_pending=0 WHERE account_id=? AND conversation_id=?", (aid, cid))
            for row in self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running')", (aid, cid)).fetchall():
                run = json.loads(row[0])
                run.update(status="cancelled", completed_at=stamp(), message="AI 已关闭，结果不会应用。")
                self._ai_write_run(run)
                self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'", (run["run_id"],))
            self._ai_touch(aid, cid)

    def _ai_note_message(self, aid, cid, item):
        if item.get("is_self") is not True:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))

    def _ai_vision(self, aid, cid):
        prefs = self._preferences(aid, cid)
        pid = prefs["api_profile_id"] or self._defaults()["api_profile_id"]
        row = self.db.execute("SELECT revision FROM api_profiles WHERE profile_id=?", (pid,)).fetchone()
        return bool(row and self._verification(pid, row[0]).get("vision") == "supported")

    def _ai_item(self, row, vision, include_media=False):
        message = json.loads(row["payload"])
        mid = message["message_id"]
        state_row = self.db.execute("SELECT * FROM ai_message_state WHERE message_id=?", (mid,)).fetchone()
        state = dict(state_row) if state_row else {}
        content = message["content"]
        kind = message["type"]
        value = {k: message.get(k) for k in ("message_id", "sent_at", "sender", "is_self", "type")}
        value["time_status"] = "known" if value["sent_at"] else "unknown"
        value["content"] = {"text": content.get("transcript") if kind == "voice" else content.get("text")}
        status, reason, digest = "pending", None, None
        media = None
        if kind == "voice" and not content.get("transcript"):
            status, reason = "waiting_transcript", "等待微信语音转写"
        elif kind in ("image", "sticker"):
            media = self.db.execute("SELECT digest,mime_type,data FROM media_cache WHERE cache_key=? AND account_id=?", ("image:"+mid, row["account_id"])).fetchone()
            digest = media["digest"] if media else state.get("media_digest")
            value["content"] = {"media_digest": digest}
            if not vision:
                status, reason = "waiting_image", "所选 API 图片能力未通过测试"
            elif media is None:
                status, reason = "waiting_image", "等待图片读取"
        elif kind not in ("text", "system", "voice"):
            status, reason = "unsupported", "暂不支持此类消息"
        fingerprint = hashlib.sha256(pack(value).encode()).hexdigest()
        if state.get("fingerprint") == fingerprint and (state.get("status") in ("memorized", "processing") or (state.get("status") == "failed" and status == "pending")):
            status = state["status"]
            reason = None
        covered = state if state.get("fingerprint") == fingerprint else {}
        public = dict(status=status, run_id=covered.get("run_id"), attempted_at=covered.get("attempted_at"), memorized_at=covered.get("memorized_at"), reason=reason)
        if include_media and media is not None:
            value["content"]["_image_bytes"] = bytes(media["data"])
            value["content"]["_image_mime"] = media["mime_type"]
        return value, fingerprint, digest, public

    def _ai_message(self, message, aid, cid):
        row = dict(payload=pack(message), account_id=aid, conversation_id=cid)
        return {**message, "ai_state": self._ai_item(row, self._ai_vision(aid, cid))[3]}

    def _ai_counts(self, aid, cid):
        vision = self._ai_vision(aid, cid)
        counts = dict(pending_count=0, waiting_count=0)
        for row in self.db.execute("SELECT * FROM messages WHERE account_id=? AND conversation_id=?", (aid, cid)):
            status = self._ai_item(row, vision)[3]["status"]
            counts["pending_count" if status in ("pending", "failed", "processing") else "waiting_count"] += int(status != "memorized")
        return counts

    def _ai_touch(self, aid, cid, mids=None):
        event = self._changed(aid, ["messages", "conversations", "conversation_preferences", "ai_runs"], [cid])
        if mids is None:
            self.db.execute("UPDATE messages SET change_seq=? WHERE account_id=? AND conversation_id=?", (event["revision"], aid, cid))
        else:
            self.db.executemany("UPDATE messages SET change_seq=? WHERE message_id=? AND account_id=? AND conversation_id=?", ((event["revision"], mid, aid, cid) for mid in mids))
        return event

    def _ai_write_run(self, run):
        self.db.execute("UPDATE ai_runs SET status=?,payload=? WHERE run_id=?", (run["status"], pack(run), run["run_id"]))

    def ai_run(self, aid, cid, rid):
        def query():
            self._conversation(aid, cid)
            row = self.db.execute("SELECT payload FROM ai_runs WHERE run_id=? AND account_id=? AND conversation_id=?", (rid, aid, cid)).fetchone()
            if row is None:
                raise ConversationError("ai_run_not_found", "AI 任务不存在。", 404)
            return {**json.loads(row[0]), 'delivery': self._delivery_public(rid)}
        return self.call(query)

    def ai_runs(self, aid, cid):
        def query():
            self._conversation(aid, cid)
            rows = self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 50", (aid, cid)).fetchall()
            return dict(items=[{**json.loads(row[0]), 'delivery': self._delivery_public(json.loads(row[0])['run_id'])} for row in rows], **self._ai_counts(aid, cid), paused=bool(self._ai_session(aid, cid)["paused"]))
        return self.call(query)

    def ai_candidates(self):
        return self.call(lambda: [dict(row) for row in self.db.execute("SELECT s.account_id,s.conversation_id FROM ai_sessions s JOIN conversation_preferences p USING(account_id,conversation_id) WHERE p.ai_enabled=1 AND s.auto_pending=1 AND s.paused=0 AND NOT EXISTS(SELECT 1 FROM ai_runs r WHERE r.account_id=s.account_id AND r.conversation_id=s.conversation_id AND r.status IN ('queued','running')) ORDER BY EXISTS(SELECT 1 FROM reply_triggers t WHERE t.account_id=s.account_id AND t.conversation_id=s.conversation_id AND t.epoch=s.epoch AND t.consumed_run_id IS NULL) DESC,s.rowid") if not self._compatibility_blocked(row['account_id']) and not self._delivery_blocked(row['account_id'],row['conversation_id']) and self._ai_auto_allowed(row['account_id'],row['conversation_id']) and not self._delivery_debouncing(row['account_id'],row['conversation_id'])])

    def _ai_auto_allowed(self, aid, cid):
        return not self._is_group(aid, cid) or bool(self._group_reason(aid, cid))

    def ai_schedule_delay(self):
        def query():
            current = time.time()
            deadlines = [row[2]+1-current for row in self.db.execute("SELECT s.account_id,s.conversation_id,d.last_trigger_at FROM ai_sessions s JOIN conversation_preferences p USING(account_id,conversation_id) JOIN delivery_sessions d USING(account_id,conversation_id) WHERE p.ai_enabled=1 AND s.paused=0 AND s.auto_pending=1 AND NOT EXISTS(SELECT 1 FROM ai_runs r WHERE r.account_id=s.account_id AND r.conversation_id=s.conversation_id AND r.status IN ('queued','running'))") if row[2]+1>current and not self._compatibility_blocked(row[0]) and not self._delivery_blocked(row[0],row[1])]
            return max(.01, min([3]+deadlines))
        return self.call(query)

    def ai_enabled_sessions(self):
        return self.call(lambda: [dict(row) for row in self.db.execute("SELECT s.account_id,s.conversation_id FROM ai_sessions s JOIN conversation_preferences p USING(account_id,conversation_id) WHERE p.ai_enabled=1 AND s.paused=0") if not self._compatibility_blocked(row["account_id"])])

    def start_ai_run(self, aid, cid, request_id, trigger="manual"):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self._compatibility_require(aid)
                prefs = self._preferences(aid, cid)
                old = self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND request_id=?", (aid, cid, request_id)).fetchone()
                if old:
                    return json.loads(old[0])
                active = self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running')", (aid, cid)).fetchone()
                if active:
                    return json.loads(active[0])
                if not prefs["ai_enabled"]:
                    raise ConversationError("ai_disabled", "请先开启此会话的 AI。", 409)
                if self._delivery_blocked(aid,cid):
                    raise ConversationError('delivery_pending','请先等待或处理上一轮发送结果。',409)
                self._validate_ai(prefs)
                if trigger == "auto" and (not self._ai_auto_allowed(aid,cid) or (not self._group_round(aid,cid) and not self._ai_counts(aid,cid)["pending_count"])):
                    self.db.execute("UPDATE ai_sessions SET auto_pending=0 WHERE account_id=? AND conversation_id=?", (aid,cid))
                    return dict(run_id=request_id, account_id=aid, conversation_id=cid, status="empty", trigger=trigger, started_at=stamp(), message_count=0, memory_revision=prefs["memory_revision"])
                self.db.execute("INSERT OR IGNORE INTO ai_sessions(account_id,conversation_id) VALUES(?,?)", (aid, cid))
                if trigger != "auto":
                    self.db.execute("UPDATE ai_sessions SET paused=0,auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
                run = dict(run_id=uuid4().hex, account_id=aid, conversation_id=cid, status="queued", trigger=trigger,
                           started_at=stamp(), message_count=0, memory_revision=prefs["memory_revision"], epoch=self._ai_session(aid, cid)["epoch"])
                group = self._group_prepare_round(aid,cid) if self._is_group(aid,cid) else None
                if group:
                    self._group_run_fields(run,group,False)
                self.db.execute("INSERT INTO ai_runs VALUES(?,?,?,?,?,?)", (run["run_id"], aid, cid, request_id, run["status"], pack(run)))
                event = self._ai_touch(aid, cid, [])
            self.notify(event)
            return run
        return self.call(write)

    def claim_ai_run(self, aid, cid, rid):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                run = json.loads(self.db.execute("SELECT payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()[0])
                if run["status"] != "queued":
                    return None
                if self._compatibility_blocked(aid) or not self._group_run_allowed(run) or (run['trigger']=='auto' and not self._ai_auto_allowed(aid,cid)):
                    run.update(status='cancelled', completed_at=stamp(), message='当前群聊触发条件已失效。')
                    self._ai_write_run(run)
                    return None
                prefs = self._preferences(aid, cid)
                self._validate_ai(prefs)
                effective = self._effective(prefs)
                profile = self._profile(effective["api_profile_id"])
                settings = dict(self.db.execute("SELECT instructions,revision FROM ai_settings").fetchone())
                card = self._card(effective["role_card_code"])
                session = self._ai_session(aid, cid)
                if not self.db.execute("SELECT 1 FROM ai_runs WHERE account_id=? AND conversation_id=? AND json_extract(payload,'$.snapshot_taken')=1 LIMIT 1", (aid,cid)).fetchone():
                    session["initial_seq"] = self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM messages WHERE account_id=? AND conversation_id=?", (aid,cid)).fetchone()[0]
                    self.db.execute("UPDATE ai_sessions SET initial_seq=? WHERE account_id=? AND conversation_id=?", (session["initial_seq"],aid,cid))
                selected, images, more = [], 0, False
                group = self._group_round(aid,cid) if run.get('round_id') else None
                for row in (() if group else self.db.execute("SELECT rowid AS ai_ordinal,* FROM messages WHERE account_id=? AND conversation_id=? ORDER BY sort_key,message_id", (aid, cid))):
                    value, fingerprint, digest, status = self._ai_item(row, profile["verification"].get("vision") == "supported", True)
                    if status["status"] not in ("pending", "failed"):
                        continue
                    is_image = value["type"] in ("image", "sticker")
                    if len(selected) >= 50 or (is_image and images >= 4):
                        more = True
                        break
                    selected.append((value, fingerprint, digest, row["ai_ordinal"]))
                    images += int(is_image)
                if group:
                    selected, more = self._group_batch(group)
                    self._group_run_fields(run,group,not more)
                self.db.execute("UPDATE ai_sessions SET auto_pending=? WHERE account_id=? AND conversation_id=?", (int(more), aid,cid))
                run.update(status="running" if selected else "empty", snapshot_taken=bool(selected), message_count=len(selected), memory_revision=prefs["memory_revision"],
                           epoch=session["epoch"], historical=bool(selected and all(s[3] <= session["initial_seq"] for s in selected)),
                           profile_id=profile["profile_id"], profile_revision=profile["revision"], role_card_code=card["card_code"],
                           role_revision=card["revision"], settings_revision=settings["revision"])
                if not selected:
                    run["completed_at"] = stamp()
                    self.db.execute("UPDATE ai_sessions SET auto_pending=0 WHERE account_id=? AND conversation_id=?", (aid, cid))
                reply_ids = self._reply_ids(aid,cid,{s[0]['message_id'] for s in selected})
                run['reply_trigger_ids'] = reply_ids
                if group:
                    run['reply_trigger_ids'] = group['reply_trigger_ids']
                    run['historical'] = False
                if reply_ids:
                    run['historical'] = False
                self._ai_write_run(run)
                for value, fingerprint, digest, _ in selected:
                    mid = value["message_id"]
                    self.db.execute("INSERT OR REPLACE INTO ai_message_state VALUES(?,?,?,'processing',?,NULL,NULL)", (mid, fingerprint, digest, rid))
                    self.db.execute("INSERT INTO ai_run_messages VALUES(?,?,?)", (rid, mid, fingerprint))
                event = self._ai_touch(aid, cid, [s[0]["message_id"] for s in selected])
                raw = self.db.execute("SELECT secret FROM api_profiles WHERE profile_id=?", (profile["profile_id"],)).fetchone()[0]
                result = dict(profile=profile, encrypted_key=bytes(raw), envelope=dict(request_id=rid, instructions=settings["instructions"],
                    character_card=card["card"], memory=prefs["memory"], messages=[s[0] for s in selected],
                    **self._ai_reply_envelope(run), deliveries=self._delivery_context(aid,cid))) if selected else None
            self.notify(event)
            return result
        return self.call(write)

    def _ai_reply_envelope(self, run):
        if run.get('round_id'):
            return dict(reply_allowed=run['round_final'], reply_target_ids=run['reply_trigger_ids'],
                        trigger_reason=run['trigger_reason'], trigger_messages=run['trigger_messages'],
                        round_id=run['round_id'], round_final=run['round_final'], batch_index=run['batch_index'])
        return dict(reply_allowed=bool(run.get('reply_trigger_ids')), reply_target_ids=run.get('reply_trigger_ids',[]))

    def ai_reply_envelope(self, aid, cid, rid):
        return self._ai_reply_envelope(self.ai_run(aid,cid,rid))

    def trim_ai_run(self, aid, cid, rid, keep):
        def write():
            with self.db:
                run = json.loads(self.db.execute("SELECT payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()[0])
                removed = [r[0] for r in self.db.execute("SELECT message_id FROM ai_run_messages WHERE run_id=?", (rid,)) if r[0] not in keep]
                for mid in removed:
                    self.db.execute("DELETE FROM ai_run_messages WHERE run_id=? AND message_id=?", (rid, mid))
                    self.db.execute("UPDATE ai_message_state SET status='pending' WHERE message_id=? AND run_id=?", (mid, rid))
                if removed:
                    self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid,cid))
                run["message_count"] = len(keep)
                if run.get('round_id'):
                    remaining = self.db.execute('SELECT count(*) FROM group_round_messages WHERE round_id=? AND done=0',(run['round_id'],)).fetchone()[0]
                    run['round_final'] = remaining == len(keep)
                else:
                    run['reply_trigger_ids'] = self._reply_ids(aid,cid,keep)
                self._ai_write_run(run)
                event = self._ai_touch(aid, cid, removed)
            self.notify(event)
        self.call(write)

    def mark_ai_attempt(self, aid, cid, rid):
        def write():
            with self.db:
                row = self.db.execute("SELECT status,payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()
                run = json.loads(row[1])
                if row[0] != "running" or self._compatibility_blocked(aid) or not self._group_run_allowed(run):
                    return False
                self.db.execute("UPDATE ai_message_state SET attempted_at=? WHERE run_id=? AND status='processing'", (stamp(), rid))
            return True
        return self.call(write)

    def finish_ai_run(self, aid, cid, rid, result=None, error=None):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                run = json.loads(self.db.execute("SELECT payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()[0])
                if run["status"] not in ("queued", "running"):
                    return run
                prefs, session = self._preferences(aid, cid), self._ai_session(aid, cid)
                if self._compatibility_blocked(aid) or not prefs["ai_enabled"] or session["epoch"] != run["epoch"] or not self._group_run_allowed(run):
                    run.update(status="cancelled", message="AI 已关闭，结果未应用。")
                elif prefs["memory_revision"] != run["memory_revision"]:
                    run.update(status="conflicted", error="ai_memory_conflict", message="记忆已被手工修改，本次结果未覆盖记忆，请重试。")
                elif error:
                    run.update(status="failed", error=error.code, message=error.message)
                else:
                    run.update(status="completed", reply=result["reply"], memory_revision=prefs["memory_revision"]+1)
                    self._queue_delivery(run)
                    self._group_finish_batch(run)
                    self.db.execute("UPDATE conversation_preferences SET memory=?,memory_revision=memory_revision+1 WHERE account_id=? AND conversation_id=?", (result["memory"], aid, cid))
                run["completed_at"] = stamp()
                self._ai_write_run(run)
                success = run["status"] == "completed"
                self.db.execute("UPDATE ai_message_state SET status=?,memorized_at=? WHERE run_id=? AND status='processing'", ("memorized" if success else "pending" if run["status"] == "cancelled" else "failed", stamp() if success else None, rid))
                if success:
                    self._delivery_reopen_pending(aid,cid)
                self.db.execute("UPDATE ai_sessions SET paused=?,auto_pending=CASE WHEN ? THEN auto_pending ELSE 0 END WHERE account_id=? AND conversation_id=?", (int(run["status"] in ("failed", "conflicted")), int(success), aid, cid))
                mids = [r[0] for r in self.db.execute("SELECT message_id FROM ai_run_messages WHERE run_id=?", (rid,))]
                event = self._ai_touch(aid, cid, mids)
            self.notify(event)
            return run
        return self.call(write)

    def ai_missing_images(self, aid, cid, exclude=()):
        def query():
            if not self._ai_vision(aid, cid):
                return []
            exclusion = " AND m.message_id NOT IN ("+",".join("?" for _ in exclude)+")" if exclude else ""
            return [r[0] for r in self.db.execute("SELECT m.message_id FROM messages m LEFT JOIN media_cache c ON c.cache_key='image:'||m.message_id WHERE m.account_id=? AND m.conversation_id=? AND json_extract(m.payload,'$.type') IN ('image','sticker') AND c.cache_key IS NULL AND NOT EXISTS(SELECT 1 FROM ai_message_state s WHERE s.message_id=m.message_id AND s.status='memorized') "+exclusion+" ORDER BY m.sort_key LIMIT 4", (aid, cid, *exclude))]
        return self.call(query)
