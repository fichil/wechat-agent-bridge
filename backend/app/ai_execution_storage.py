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
            if self._recover_retry(payload): continue
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
            for row in self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running','suspended')", (aid, cid)).fetchall():
                run = json.loads(row[0])
                run.update(status="cancelled", completed_at=stamp(), message="AI 已关闭，结果不会应用。")
                self._ai_write_run(run)
                self.db.execute('DELETE FROM ai_request_snapshots WHERE run_id=?', (run['run_id'],))
                self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'", (run["run_id"],))
            self._ai_touch(aid, cid)

    def _ai_note_message(self, aid, cid, item):
        if item.get("is_self") is not True:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))

    def _ai_vision(self, aid, cid):
        return bool(self._routing_profiles(self._preferences(aid, cid), vision=True))

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
            media = self.db.execute('SELECT digest,mime_type' + (',data' if include_media else '') +
                                    ' FROM media_cache WHERE cache_key=? AND account_id=?', ("image:"+mid, row["account_id"])).fetchone()
            digest = media["digest"] if media else state.get("media_digest")
            value["content"] = {"media_digest": digest}
            if not vision:
                status, reason = "waiting_image", "所选 API 图片能力未通过测试"
            elif media is None:
                status, reason = "waiting_image", "等待图片读取"
            if self._is_group(row['account_id'], row['conversation_id']):
                # Processing a placeholder covers this message, independently of
                # when its image cache becomes available or is evicted.
                digest = None
                value['content'] = {k: content[k] for k in ('description', 'filename', 'text') if content.get(k)}
                value['content']['visual_content'] = 'not_inspected'
                status, reason = 'pending', None
        elif kind not in ("text", "system", "voice"):
            status, reason = "unsupported", "暂不支持此类消息"
        if content.get('quoted_server_id'):
            value['content']['quoted_server_id'] = content['quoted_server_id']
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
        if run['status'] not in ('queued', 'running', 'suspended'):
            run['next_retry_at'] = None
            self.db.execute('DELETE FROM ai_request_snapshots WHERE run_id=?', (run['run_id'],))
            self.db.execute('DELETE FROM reply_candidates WHERE run_id=?', (run['run_id'],))
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
        return self.call(lambda: [dict(row) for row in self.db.execute("SELECT s.account_id,s.conversation_id,EXISTS(SELECT 1 FROM reply_triggers t WHERE t.account_id=s.account_id AND t.conversation_id=s.conversation_id AND t.epoch=s.epoch AND t.consumed_run_id IS NULL) AS reply_priority FROM ai_sessions s JOIN conversation_preferences p USING(account_id,conversation_id) WHERE p.ai_enabled=1 AND s.auto_pending=1 AND s.paused=0 AND NOT EXISTS(SELECT 1 FROM ai_runs r WHERE r.account_id=s.account_id AND r.conversation_id=s.conversation_id AND r.status IN ('queued','running')) ORDER BY EXISTS(SELECT 1 FROM reply_triggers t WHERE t.account_id=s.account_id AND t.conversation_id=s.conversation_id AND t.epoch=s.epoch AND t.consumed_run_id IS NULL) DESC,s.rowid") if not self._compatibility_blocked(row['account_id']) and self.configuration_ready(row['account_id'],row['conversation_id']) and not self._delivery_blocked(row['account_id'],row['conversation_id']) and self._ai_auto_allowed(row['account_id'],row['conversation_id']) and not self._delivery_debouncing(row['account_id'],row['conversation_id'])])

    def _ai_auto_allowed(self, aid, cid):
        if self._is_group(aid, cid) and not self._group_reply_rows(aid, cid):
            row = self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status='suspended' LIMIT 1", (aid, cid)).fetchone()
            if row and (json.loads(row[0]).get('next_retry_at') or 0) > time.time():
                return False
            group = self._group_round(aid, cid)
            if group and group.get('memory_only') and not self._group_memory_ready(group):
                return False
        return not self._is_group(aid, cid) or bool(self._group_reason(aid, cid))

    def ai_schedule_delay(self):
        def query():
            current = time.time()
            deadlines = [row[2]+1-current for row in self.db.execute("SELECT s.account_id,s.conversation_id,d.last_trigger_at FROM ai_sessions s JOIN conversation_preferences p USING(account_id,conversation_id) JOIN delivery_sessions d USING(account_id,conversation_id) WHERE p.ai_enabled=1 AND s.paused=0 AND s.auto_pending=1 AND NOT EXISTS(SELECT 1 FROM ai_runs r WHERE r.account_id=s.account_id AND r.conversation_id=s.conversation_id AND r.status IN ('queued','running'))") if row[2]+1>current and not self._compatibility_blocked(row[0]) and not self._delivery_blocked(row[0],row[1])]
            deadlines.extend(row[1]-current for row in self.db.execute("SELECT account_id,json_extract(payload,'$.next_retry_at') FROM ai_runs WHERE status='queued'") if row[1] and row[1]>current and not self._compatibility_blocked(row[0]))
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
                    existing = json.loads(active[0])
                    if not self._suspend_background(existing):
                        return existing
                if not prefs["ai_enabled"]:
                    raise ConversationError("ai_disabled", "请先开启此会话的 AI。", 409)
                if self._delivery_blocked(aid,cid):
                    raise ConversationError('delivery_pending','请先等待或处理上一轮发送结果。',409)
                self._validate_ai(prefs)
                realtime = self._is_group(aid, cid) and bool(self._group_reply_rows(aid, cid))
                if not realtime:
                    resumed = self._resume_background(aid, cid)
                    if resumed:
                        return resumed
                if trigger == "auto" and (not self._ai_auto_allowed(aid,cid) or (not self._group_round(aid,cid) and not self._ai_counts(aid,cid)["pending_count"])):
                    self.db.execute("UPDATE ai_sessions SET auto_pending=0 WHERE account_id=? AND conversation_id=?", (aid,cid))
                    return dict(run_id=request_id, account_id=aid, conversation_id=cid, status="empty", trigger=trigger, started_at=stamp(), message_count=0, memory_revision=prefs["memory_revision"])
                self.db.execute("INSERT OR IGNORE INTO ai_sessions(account_id,conversation_id) VALUES(?,?)", (aid, cid))
                if trigger != "auto":
                    self.db.execute("UPDATE ai_sessions SET paused=0,auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
                run = dict(run_id=uuid4().hex, account_id=aid, conversation_id=cid, status="queued", trigger=trigger,
                           started_at=stamp(), message_count=0, memory_revision=prefs["memory_revision"], epoch=self._ai_session(aid, cid)["epoch"],
                           attempt_count=0, max_retries=prefs['ai_max_retries'], next_retry_at=None)
                group = self._group_prepare_round(aid,cid) if self._is_group(aid,cid) else None
                if group:
                    self._group_run_fields(run,group,False)
                    if realtime:
                        run.update(processing_type='realtime_reply', trigger_reason='mention')
                self.db.execute("INSERT INTO ai_runs VALUES(?,?,?,?,?,?)", (run["run_id"], aid, cid, request_id, run["status"], pack(run)))
                event = self._ai_touch(aid, cid, [])
            self.notify(event)
            return run
        return self.call(write)

    def claim_ai_run(self, aid, cid, rid):
        def write():
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()
                if row is None: return None
                run = json.loads(row[0])
                if run["status"] != "queued":
                    return None
                if self._ai_terminate_invalid(run): return None
                if self._compatibility_blocked(aid): return None
                if self._suspend_background(run): return None
                resumed = self._ai_retry_snapshot(run)
                if resumed is not None: return resumed
                if self._compatibility_blocked(aid) or not self._group_run_allowed(run) or (run['trigger']=='auto' and not self._ai_auto_allowed(aid,cid)):
                    run.update(status='cancelled', completed_at=stamp(), message='当前群聊触发条件已失效。')
                    self._ai_write_run(run)
                    return None
                prefs = self._preferences(aid, cid)
                self._validate_ai(prefs)
                effective = self._effective(prefs)
                profiles = self._routing_profiles(prefs)
                realtime = run.get('processing_type') == 'realtime_reply'
                needs_vision = (bool(self._group_media_ids(aid, cid, self._group_live_rows(aid, cid)))
                                if self._is_group(aid, cid) else self._route_requires_vision(aid, cid))
                if any(p['verification'].get('vision') == 'supported' for p in profiles) and needs_vision:
                    profiles = [p for p in profiles if p['verification'].get('vision') == 'supported']
                if run.pop('resume_background', False) and run.get('route_candidates'):
                    profile = self._profile(run['profile_id'])
                else:
                    profile = profiles[0]
                    self._route_initialize(run, prefs, profiles)
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
                    if realtime:
                        selected, targets, context_only = self._group_reply_batch(aid, cid, profile['verification'].get('vision') == 'supported')
                        self._group_run_fields(run, group, False)
                        run.update(processing_type='realtime_reply', trigger_reason='mention', reply_trigger_ids=targets,
                                   context_only_ids=context_only, trigger_messages=[{**v, 'content': {k: val for k, val in v['content'].items() if not k.startswith('_')}}
                                       for v, _, _, _ in selected if v['message_id'] in targets])
                        more = True
                    else:
                        selected, more = self._group_batch(group)
                        self._group_run_fields(run,group,not more)
                        rows = [self.db.execute('SELECT * FROM messages WHERE message_id=?', (item[0]['message_id'],)).fetchone() for item in selected]
                        selected, context_only = self._group_attach_media(aid, cid, selected, rows, profile['verification'].get('vision') == 'supported')
                        run['context_only_ids'] = context_only
                self.db.execute("UPDATE ai_sessions SET auto_pending=? WHERE account_id=? AND conversation_id=?", (int(more), aid,cid))
                run.update(status="running" if selected else "empty", snapshot_taken=bool(selected), message_count=len(selected), memory_revision=prefs["memory_revision"],
                           epoch=session["epoch"], historical=bool(selected and all(s[3] <= session["initial_seq"] for s in selected)),
                           profile_id=profile["profile_id"], profile_revision=profile["revision"], profile_name=profile['name'], role_card_code=card["card_code"],
                           role_revision=card["revision"], settings_revision=settings["revision"])
                if not selected:
                    run["completed_at"] = stamp()
                    self.db.execute("UPDATE ai_sessions SET auto_pending=0 WHERE account_id=? AND conversation_id=?", (aid, cid))
                reply_ids = self._reply_ids(aid,cid,{s[0]['message_id'] for s in selected})
                if not group:
                    run['reply_trigger_ids'] = reply_ids
                else:
                    run['historical'] = False
                if reply_ids:
                    run['historical'] = False
                self._ai_write_run(run)
                for value, fingerprint, digest, _ in selected:
                    mid = value["message_id"]
                    if mid in run.get('context_only_ids', []):
                        continue
                    self.db.execute("INSERT OR REPLACE INTO ai_message_state VALUES(?,?,?,'processing',?,NULL,NULL)", (mid, fingerprint, digest, rid))
                    self.db.execute("INSERT INTO ai_run_messages VALUES(?,?,?)", (rid, mid, fingerprint))
                event = self._ai_touch(aid, cid, [s[0]["message_id"] for s in selected])
                raw = self.db.execute("SELECT secret FROM api_profiles WHERE profile_id=?", (profile["profile_id"],)).fetchone()[0]
                result = dict(profile=profile, encrypted_key=bytes(raw), envelope=dict(request_id=rid, instructions=settings["instructions"],
                    character_card=card["card"], memory=prefs["memory"], messages=[s[0] for s in selected],
                    role_card_edit_allowed=self._ai_role_edit_allowed(run),
                    **self._ai_reply_envelope(run), deliveries=self._delivery_context(aid,cid))) if selected else None
                if result and self._is_group(aid, cid):
                    result['envelope']['memory_max_chars'] = 4000
            self.notify(event)
            return result
        return self.call(write)

    def _ai_reply_envelope(self, run):
        kind = run.get('processing_type')
        if kind == 'realtime_reply':
            return dict(processing_type=kind, reply_allowed=True, reply_required=True,
                        reply_target_ids=run['reply_trigger_ids'], trigger_reason='mention',
                        trigger_messages=run['trigger_messages'], context_only_ids=run.get('context_only_ids', []))
        group = self._group_round(run['account_id'], run['conversation_id']) if run.get('round_id') else None
        if kind == 'memory_backfill' or (group and group.get('memory_only')):
            return dict(processing_type='memory_backfill', reply_allowed=False, reply_required=False,
                        reply_target_ids=[], trigger_messages=[], trigger_reason=run.get('trigger_reason'))
        if run.get('round_id'):
            return dict(reply_allowed=run['round_final'], reply_target_ids=run['reply_trigger_ids'],
                        reply_required=bool(run['round_final'] and run['trigger_reason'] == 'mention' and run['reply_trigger_ids']),
                        trigger_reason=run['trigger_reason'], trigger_messages=run['trigger_messages'],
                        round_id=run['round_id'], round_final=run['round_final'], batch_index=run['batch_index'],
                        context_only_ids=run.get('context_only_ids', []))
        return dict(reply_allowed=bool(run.get('reply_trigger_ids')), reply_target_ids=run.get('reply_trigger_ids',[]),
                    reply_required=bool(run.get('reply_trigger_ids')))

    def _ai_role_edit_allowed(self, run):
        envelope = self._ai_reply_envelope(run)
        return bool(not run.get('historical', True) and envelope['reply_allowed']
                    and envelope.get('processing_type') != 'memory_backfill')

    def ai_reply_envelope(self, aid, cid, rid):
        run = self.ai_run(aid, cid, rid)
        return self.call(lambda: {**self._ai_reply_envelope(run), 'role_card_edit_allowed': self._ai_role_edit_allowed(run)})

    def _trimmed_ai_run(self, run, keep):
        run = dict(run, message_count=len(keep))
        if run.get('processing_type') == 'realtime_reply':
            run['reply_trigger_ids'] = [mid for mid in run['reply_trigger_ids'] if mid in keep]
            run['trigger_messages'] = [m for m in run['trigger_messages'] if m['message_id'] in keep]
            run['context_only_ids'] = [mid for mid in run.get('context_only_ids', []) if mid in keep]
        elif run.get('round_id'):
            remaining = self.db.execute('SELECT count(*) FROM group_round_messages WHERE round_id=? AND done=0', (run['round_id'],)).fetchone()[0]
            run['context_only_ids'] = [mid for mid in run.get('context_only_ids', []) if mid in keep]
            run['round_final'] = remaining == len(keep - set(run.get('context_only_ids', [])))
        else:
            run['reply_trigger_ids'] = self._reply_ids(run['account_id'], run['conversation_id'], keep)
        return run

    def preview_ai_reply_envelope(self, aid, cid, rid, keep):
        def query():
            row = self.db.execute('SELECT payload FROM ai_runs WHERE run_id=? AND account_id=? AND conversation_id=?', (rid, aid, cid)).fetchone()
            run = self._trimmed_ai_run(json.loads(row[0]), keep)
            return {**self._ai_reply_envelope(run), 'role_card_edit_allowed': self._ai_role_edit_allowed(run)}
        return self.call(query)

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
                    run['split_reason'] = 'input_budget'
                run = self._trimmed_ai_run(run, keep)
                self._ai_write_run(run)
                event = self._ai_touch(aid, cid, removed)
            self.notify(event)
        self.call(write)

    def mark_ai_attempt(self, aid, cid, rid):
        def write():
            with self.db:
                row = self.db.execute("SELECT status,payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()
                if row is None: return False
                run = json.loads(row[1])
                if row[0] != "running" or self._ai_terminate_invalid(run):
                    return False
                if self._compatibility_blocked(aid):
                    run.update(status='queued',next_retry_at=time.time()+3)
                    self._ai_write_run(run)
                    return False
                run.update(attempt_count=run.get('attempt_count',0)+1, next_retry_at=None, error=None, message=None)
                run['http_attempt_count'] = run.get('http_attempt_count', 0) + 1
                run['request_started_at'] = stamp()
                run.setdefault('timings_ms', {}).pop('model_request', None)
                if run.get('processing_type') == 'realtime_reply' and run.get('reply_trigger_ids'):
                    placeholders = ','.join('?' for _ in run['reply_trigger_ids'])
                    observed = self.db.execute('SELECT min(observed_at) FROM group_message_eligibility WHERE message_id IN ('+placeholders+')', run['reply_trigger_ids']).fetchone()[0]
                    if observed is not None:
                        run['received_at'] = datetime.fromtimestamp(observed, timezone.utc).isoformat()
                        run.setdefault('timings_ms', {})['received_to_request'] = max(0, round((time.time()-observed)*1000, 3))
                if 'route_candidates' in run:
                    if run['profile_id'] in run['route_tried']:
                        run['retry_count'] += 1
                    else:
                        run['route_tried'].append(run['profile_id'])
                    run['profile_attempts'].append(dict(profile_id=run['profile_id'], profile_name=run['profile_name'],
                                                        attempt=run['attempt_count'], status='running', started_at=stamp()))
                self._ai_write_run(run)
                self.db.execute("UPDATE ai_message_state SET attempted_at=? WHERE run_id=? AND status='processing'", (stamp(), rid))
            return True
        return self.call(write)

    def finish_ai_run(self, aid, cid, rid, result=None, error=None, model_duration_ms=None):
        def write():
            nonlocal error
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT payload FROM ai_runs WHERE run_id=?", (rid,)).fetchone()
                if row is None: return None
                run = json.loads(row[0])
                if run["status"] not in ("queued", "running"):
                    return run
                if model_duration_ms is not None:
                    run.setdefault('timings_ms', {})['model_request'] = model_duration_ms
                prefs = self._preferences(aid, cid)
                if error is None and result is not None and not result['reply'].strip() and self._ai_reply_envelope(run)['reply_required']:
                    error = ConversationError('ai_empty_reply', '本轮需要回复，但没有可发送的内容。', 502)
                if self._ai_terminate_invalid(run):
                    pass
                elif not self._compatibility_capture_allowed(aid):
                    run.update(status="cancelled", error="compatibility_paused", message="微信身份或兼容检查尚未通过，本次结果未应用。")
                elif prefs["memory_revision"] != run["memory_revision"]:
                    run.update(status="conflicted", error="ai_memory_conflict", message="记忆已被手工修改，本次结果未覆盖记忆，请重试。")
                elif error:
                    if self._schedule_ai_retry(run,error):
                        event=self._ai_touch(aid,cid,[])
                        # Commit before notifying; no model worker sleeps during backoff.
                        self.db.commit()
                        self.notify(event)
                        return run
                    run.update(status="failed", error=error.code, message=error.message)
                else:
                    memory_only = self._ai_reply_envelope(run).get('processing_type') == 'memory_backfill'
                    parts = self._prepare_reply(run, '' if memory_only else result['reply'])
                    if not parts and self._ai_reply_envelope(run)['reply_required']:
                        raise ConversationError('ai_empty_reply', '本轮需要回复，但没有可发送的内容。', 502)
                    run['role_card_change'] = self._apply_ai_role_patch(run, result.get('role_card_update', ''))
                    run['reply_parts'] = parts
                    run['reply_format_version'] = 2
                    display = '\n'.join(p['text'] if p['kind']=='text' else '['+('表情' if p['kind']=='sticker' else '图片')+'：'+p['name']+']' for p in parts)
                    run.update(status="completed", reply=display, memory_revision=prefs["memory_revision"]+1)
                    self._route_attempt_result(run, 'completed')
                    self._queue_delivery(run)
                    if not run.get('reply'): run['reply_parts'] = []
                    self._group_finish_batch(run)
                    from .reply_media import summarize_memory
                    self.db.execute("UPDATE conversation_preferences SET memory=?,memory_revision=memory_revision+1 WHERE account_id=? AND conversation_id=?", (summarize_memory(result["memory"]), aid, cid))
                    self._advance_suspended_memory(aid, cid, prefs['memory_revision'], prefs['memory_revision'] + 1)
                run["completed_at"] = stamp()
                run['next_retry_at'] = None
                self.db.execute('DELETE FROM ai_request_snapshots WHERE run_id=?',(rid,))
                self.db.execute('DELETE FROM reply_candidates WHERE run_id=?',(rid,))
                self._media_collect()
                self._ai_write_run(run)
                success = run["status"] == "completed"
                self.db.execute("UPDATE ai_message_state SET status=?,memorized_at=? WHERE run_id=? AND status='processing'", ("memorized" if success else "pending" if run["status"] == "cancelled" else "failed", stamp() if success else None, rid))
                if success:
                    self._delivery_reopen_pending(aid,cid)
                self.db.execute("UPDATE ai_sessions SET paused=?,auto_pending=CASE WHEN ? THEN auto_pending ELSE 0 END WHERE account_id=? AND conversation_id=?", (int(run["status"] in ("failed", "conflicted")), int(success), aid, cid))
                mids = [r[0] for r in self.db.execute("SELECT message_id FROM ai_run_messages WHERE run_id=?", (rid,))]
                event = self._ai_touch(aid, cid, mids)
            self.notify(event)
            if run.get('role_card_change') and run['status'] == 'completed':
                self.notify(dict(type='changed', resources=['role_cards']))
            return run
        return self.call(write)

    def ai_missing_images(self, aid, cid, exclude=()):
        def query():
            if not self._ai_vision(aid, cid):
                return []
            if self._is_group(aid, cid):
                chosen = self._group_media_ids(aid, cid, self._group_live_rows(aid, cid))
                return [mid for mid in chosen if mid not in exclude and not self.db.execute(
                    'SELECT 1 FROM media_cache WHERE cache_key=?', ('image:' + mid,)).fetchone()]
            exclusion = " AND m.message_id NOT IN ("+",".join("?" for _ in exclude)+")" if exclude else ""
            return [r[0] for r in self.db.execute("SELECT m.message_id FROM messages m LEFT JOIN media_cache c ON c.cache_key='image:'||m.message_id WHERE m.account_id=? AND m.conversation_id=? AND json_extract(m.payload,'$.type') IN ('image','sticker') AND c.cache_key IS NULL AND NOT EXISTS(SELECT 1 FROM ai_message_state s WHERE s.message_id=m.message_id AND s.status='memorized') "+exclusion+" ORDER BY m.sort_key LIMIT 4", (aid, cid, *exclude))]
        return self.call(query)
