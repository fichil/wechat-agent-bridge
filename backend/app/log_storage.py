"""Bounded diagnostic archive, using the existing SQLite owner thread."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from uuid import uuid4
from .conversations import ConversationError
from .diagnostics import sanitize

LOG_LIMIT = 512 * 1024 * 1024
LOG_SCHEMA = (
    "CREATE TABLE diagnostic_logs(log_id TEXT PRIMARY KEY, account_id TEXT, conversation_id TEXT, category TEXT NOT NULL, task_id TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL, merge_key TEXT, payload TEXT NOT NULL)",
    "CREATE TABLE diagnostic_details(log_id TEXT PRIMARY KEY REFERENCES diagnostic_logs ON DELETE CASCADE, payload TEXT NOT NULL, size_bytes INTEGER NOT NULL)",
    "CREATE INDEX logs_account_order ON diagnostic_logs(account_id,category,updated_at DESC,log_id DESC)",
    "CREATE INDEX logs_task ON diagnostic_logs(task_id)",
    "CREATE INDEX logs_merge ON diagnostic_logs(merge_key)",
)

def stamp():
    return datetime.now(timezone.utc).isoformat()

def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

class LogStoreMixin:
    log_warning = None
    log_limit = LOG_LIMIT

    def _log_put(self, summary, details=None, merge_key=None):
        identity = summary.get("log_id")
        old = self.db.execute("SELECT payload FROM diagnostic_logs WHERE log_id=?", (identity,)).fetchone() if identity else None
        if merge_key and not old:
            old = self.db.execute("SELECT payload FROM diagnostic_logs WHERE merge_key=? AND active=0 ORDER BY updated_at DESC LIMIT 1", (merge_key,)).fetchone()
        value = json.loads(old[0]) if old else dict(log_id=identity or uuid4().hex, started_at=stamp(), count=0)
        if merge_key:
            value["count"] += 1
        elif not old:
            value["count"] = 1
        value.update(summary)
        value["updated_at"] = stamp()
        value.setdefault("status", "completed")
        value.setdefault("category", "source")
        value.setdefault("operation", "unknown")
        if value.get("conversation_id") and not value.get("conversation_name"):
            row = self.db.execute("SELECT payload FROM conversations WHERE account_id=? AND conversation_id=?", (value.get("account_id"),value["conversation_id"])).fetchone()
            if row:
                value["conversation_name"] = json.loads(row[0]).get("name")
        active = value["status"] == "running" or value.get("application_status") in ("queued", "running")
        self.db.execute("INSERT INTO diagnostic_logs VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(log_id) DO UPDATE SET updated_at=excluded.updated_at,status=excluded.status,active=excluded.active,payload=excluded.payload",
            (value["log_id"], value.get("account_id"), value.get("conversation_id"), value["category"], value.get("task_id"), value["started_at"], value["updated_at"], value["status"], int(active), merge_key, pack(value)))
        if details is not None:
            encoded = pack(details)
            self.db.execute("INSERT OR REPLACE INTO diagnostic_details VALUES(?,?,?)", (value["log_id"], encoded, len(encoded.encode())))
        return value["log_id"]

    def _log_prune(self):
        cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        self.db.execute("DELETE FROM diagnostic_logs WHERE active=0 AND updated_at<?", (cutoff,))
        total = self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM diagnostic_details").fetchone()[0]
        if total > self.log_limit:
            for row in self.db.execute("SELECT l.log_id,d.size_bytes FROM diagnostic_logs l JOIN diagnostic_details d USING(log_id) WHERE active=0 ORDER BY l.updated_at,l.log_id").fetchall():
                self.db.execute("DELETE FROM diagnostic_logs WHERE log_id=?", (row[0],))
                total -= row[1]
                if total <= self.log_limit:
                    break
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('logs_capacity_pruned',?)", (stamp(),))

    def log_write(self, summary, details=None, merge_key=None):
        # Diagnostic failures are isolated from model and source operations.
        try:
            summary, details = sanitize(summary), sanitize(details)
            def write():
                with self.db:
                    identity = self._log_put(summary, details, merge_key)
                    self._log_prune()
                self.notify(dict(type="changed", account_id=summary.get("account_id"), resources=["logs"]))
                return identity
            return self.call(write)
        except Exception:
            self.log_warning = "日志记录异常，部分诊断信息未保存；业务任务结果以任务记录为准。"
            return None

    def log_application(self, task_id, task, category="ai"):
        try:
            def write():
                with self.db:
                    rows = self.db.execute("SELECT payload FROM diagnostic_logs WHERE task_id=?", (task_id,)).fetchall()
                    progress = {k:task.get(k) for k in ('round_id','trigger_reason','batch_index','round_message_count','round_completed_count','round_final','chose_silence') if k in task}
                    if not rows:
                        value = {k:task.get(k) for k in ("account_id", "conversation_id", "trigger", "started_at", "profile_id")}
                        value.update(category=category, operation=task.get("operation", "conversation"), task_id=task_id,
                            status="failed" if task.get("error") else "completed", application_status=task["status"],
                            error=task.get("error"), message=task.get("message"))
                        value.update(progress)
                        self._log_put(sanitize(value), dict(note="本任务没有发起模型 HTTP 请求。", application_result=sanitize(task)))
                    else:
                        for row in rows:
                            value = json.loads(row[0])
                            self._log_put(dict(log_id=value["log_id"], application_status=task["status"], **progress,
                                profile_id=value.get("profile_id") or task.get("profile_id")))
                    self._log_prune()
                self.notify(dict(type="changed", account_id=task.get("account_id"), resources=["logs"]))
            self.call(write)
        except Exception:
            self.log_warning = "日志记录异常，部分诊断信息未保存；业务任务结果以任务记录为准。"

    def _log_migrate(self):
        for table in ("ai_runs", "api_tasks"):
            for row in self.db.execute("SELECT payload FROM "+table).fetchall():
                task = json.loads(row[0])
                last_time=task.get("completed_at") or task.get("started_at")
                if last_time and datetime.fromisoformat(last_time.replace("Z","+00:00")) < datetime.now(timezone.utc)-timedelta(days=7):
                    continue
                tid = task.get("run_id") or task["task_id"]
                value = {k:task.get(k) for k in ("account_id", "conversation_id", "started_at", "profile_id", "profile_revision", "trigger", "error", "message")}
                profile = task.get("profile") or {}
                if not profile and task.get("profile_id") and task.get("profile_revision"):
                    found = self.db.execute("SELECT name,provider,model,protocol,revision FROM api_profiles WHERE profile_id=? AND revision=?", (task["profile_id"],task["profile_revision"])).fetchone()
                    profile = dict(found) if found else {}
                value.update(log_id="legacy:"+tid, category="ai", operation=task.get("operation", "conversation"), task_id=tid,
                    legacy=True, status="failed" if task.get("error") else "running" if task["status"] in ("queued","running") else "completed",
                    application_status=task["status"], profile_name=profile.get("name"), provider=profile.get("provider"), model=profile.get("model"), protocol=profile.get("protocol"))
                identity=self._log_put(sanitize(value), dict(note="历史任务：请求 JSON、原始响应和输入快照未记录。", application_result=sanitize(task)))
                if last_time:
                    stored=json.loads(self.db.execute("SELECT payload FROM diagnostic_logs WHERE log_id=?",(identity,)).fetchone()[0])
                    stored["updated_at"]=last_time
                    self.db.execute("UPDATE diagnostic_logs SET updated_at=?,payload=? WHERE log_id=?",(last_time,pack(stored),identity))

    def _log_recover(self):
        for row in self.db.execute("SELECT payload FROM diagnostic_logs WHERE active=1").fetchall():
            value = json.loads(row[0])
            application = None
            for table, key in (("ai_runs","run_id"),("api_tasks","task_id")):
                task = self.db.execute(f"SELECT payload FROM {table} WHERE {key}=?",(value.get("task_id"),)).fetchone()
                if task:
                    raw = json.loads(task[0])
                    application = "interrupted" if raw.get("error")=="ai_interrupted" else raw["status"]
                    break
            value["application_status"] = application or "interrupted"
            if value["status"] == "running":
                value.update(status="interrupted", error="log_interrupted", message="服务在记录响应完成前停止；未自动重发。")
            self._log_put(value)
        self._log_prune()

    def logs(self, query):
        def read():
            self._account(query.account_id)
            with self.db:
                self._log_prune()
            filters = query.model_dump(mode="json", exclude={"cursor", "page_size"})
            fingerprint = hashlib.sha256(pack(filters).encode()).hexdigest()
            anchor = self._decode(query.cursor, query.account_id, None, "logs")
            if anchor and anchor.get("filters") != fingerprint:
                raise ConversationError("cursor_expired", "筛选条件已改变，请重新加载。", 409)
            where, args = ["category=?"], [query.category]
            if query.scope == "global": where.append("account_id IS NULL")
            elif query.scope == "account": where.append("account_id=?"); args.append(query.account_id)
            else: where.append("(account_id=? OR account_id IS NULL)"); args.append(query.account_id)
            for field in ("conversation_id", "status"):
                if getattr(query, field): where.append(field+"=?"); args.append(getattr(query,field))
            if query.profile_id: where.append("json_extract(payload,'$.profile_id')=?"); args.append(query.profile_id)
            for field, op in (("since",">="),("until","<=")):
                if getattr(query,field): where.append("julianday(started_at)"+op+"julianday(?)"); args.append(getattr(query,field).isoformat())
            if anchor: where.append("(updated_at,log_id)<(?,?)"); args.extend(anchor["after"])
            rows = self.db.execute("SELECT payload FROM diagnostic_logs WHERE "+" AND ".join(where)+" ORDER BY updated_at DESC,log_id DESC LIMIT ?", (*args,query.page_size+1)).fetchall()
            items = [json.loads(r[0]) for r in rows[:query.page_size]]
            cursor = None
            if len(rows)>query.page_size:
                with self.db: cursor=self._cursor(query.account_id,None,"logs",dict(filters=fingerprint,after=[items[-1]["updated_at"],items[-1]["log_id"]]))
            trimmed=self.db.execute("SELECT value FROM metadata WHERE key='logs_capacity_pruned'").fetchone()
            return dict(items=items,next_cursor=cursor,warning=self.log_warning or ("日志容量曾达到上限，已提前清理最旧记录。最近清理："+trimmed[0] if trimmed else None),
                size_bytes=self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM diagnostic_details").fetchone()[0],limit_bytes=self.log_limit)
        return self.call(read)

    def log_detail(self, aid, identity):
        def read():
            self._account(aid)
            row=self.db.execute("SELECT l.payload,d.payload FROM diagnostic_logs l JOIN diagnostic_details d USING(log_id) WHERE l.log_id=? AND (l.account_id=? OR l.account_id IS NULL)",(identity,aid)).fetchone()
            if row is None: raise ConversationError("log_not_found","日志不存在、已清理或不属于当前账号。",404)
            value=json.loads(row[0]); details=json.loads(row[1])
            if value.get("task_id"):
                for table, key in (("ai_runs","run_id"),("api_tasks","task_id")):
                    task=self.db.execute(f"SELECT payload FROM {table} WHERE {key}=?",(value["task_id"],)).fetchone()
                    if task:
                        raw=json.loads(task[0]); details["application_result"]=sanitize(raw)
                        value["application_status"]="interrupted" if raw.get("error")=="ai_interrupted" else raw["status"]
                        break
            return dict(value, details=details)
        return self.call(read)

    def clear_logs(self, aid, scope):
        def clear():
            self._account(aid)
            with self.db:
                cursor=self.db.execute("DELETE FROM diagnostic_logs WHERE active=0 AND "+("account_id IS NULL" if scope=="global" else "account_id=?"), () if scope=="global" else (aid,))
            self.notify(dict(type="changed", account_id=None if scope=="global" else aid, resources=["logs"]))
            return dict(deleted_count=cursor.rowcount)
        return self.call(clear)

    def ai_session_list(self, aid):
        def read():
            self._account(aid)
            items=[]
            for row in self.db.execute("SELECT conversation_id,payload FROM conversations WHERE account_id=? AND COALESCE(json_extract(payload,'$.is_container'),0)=0 ORDER BY sort_timestamp DESC",(aid,)):
                cid=row[0]; p=self._preferences(aid,cid)
                items.append(dict(account_id=aid,conversation_id=cid,name=json.loads(row[1])["name"],ai_enabled=p["ai_enabled"],memory_revision=p["memory_revision"],paused=bool(self._ai_session(aid,cid)["paused"]),**self._ai_counts(aid,cid)))
            return dict(items=items)
        return self.call(read)

    def ai_pending_messages(self, query):
        def read():
            aid,cid=query.account_id,query.conversation_id
            self._conversation(aid,cid)
            anchor=self._decode(query.cursor,aid,cid,"ai_messages")
            if anchor and anchor.get("status") != query.status: raise ConversationError("cursor_expired","筛选已改变，请重新加载。",409)
            items=[]; vision=self._ai_vision(aid,cid)
            for row in self.db.execute("SELECT * FROM messages WHERE account_id=? AND conversation_id=? AND (sort_key,message_id)>(?,?) ORDER BY sort_key,message_id",(aid,cid,*(anchor.get("after") or ["",""]))):
                state=self._ai_item(row,vision)[3]
                if state["status"]=="memorized" or (query.status and state["status"]!=query.status): continue
                items.append({**json.loads(row["payload"]),"ai_state":state})
                if len(items)>query.page_size: break
            cursor=None
            if len(items)>query.page_size:
                items=items[:query.page_size]
                with self.db: cursor=self._cursor(aid,cid,"ai_messages",dict(status=query.status,after=[items[-1]["sort_key"],items[-1]["message_id"]]))
            return dict(items=items,next_cursor=cursor)
        return self.call(read)
