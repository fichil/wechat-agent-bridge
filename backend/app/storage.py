"""Local archive. All SQL, transactions and checkpoints belong to one thread."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .conversations import ConversationError
from .role_storage import RoleStoreMixin, ROLE_SCHEMA
from .ai_storage import AiStoreMixin, AI_SCHEMA
from .ai_execution_storage import AiExecutionStoreMixin, EXECUTION_SCHEMA
from .ai_execution_models import DEFAULT_INSTRUCTIONS
from .log_storage import LogStoreMixin, LOG_SCHEMA
from .delivery_storage import DeliveryStoreMixin, DELIVERY_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / ".local" / "bridge.sqlite3"
IMAGE_LIMIT = 512 * 1024 * 1024
PAGE_BYTES = 16 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def order_key(key):
    return f"{key[0]+2**63:020d}.{key[1]+2**63:020d}.{key[2].encode().hex()}.{key[3]+2**63:020d}"


def bounded_page(rows, size):
    selected, total = [], 0
    for row in rows:
        length = len(row["payload"].encode("utf-8"))
        if len(selected) >= size or (selected and total + length > PAGE_BYTES):
            break
        selected.append(row)
        total += length
    return selected, len(selected) < len(rows)


SCHEMA = """
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE accounts(account_id TEXT PRIMARY KEY, wxid TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
 source_state TEXT NOT NULL DEFAULT 'unknown', last_seen_at TEXT, last_synced_at TEXT, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
 source_id TEXT NOT NULL, payload TEXT NOT NULL, category TEXT NOT NULL, sort_timestamp INTEGER NOT NULL,
 last_synced_at TEXT, active INTEGER NOT NULL DEFAULT 1, UNIQUE(account_id,source_id));
CREATE INDEX conversation_order ON conversations(account_id,active,sort_timestamp DESC,conversation_id);
CREATE TABLE messages(message_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
 conversation_id TEXT NOT NULL REFERENCES conversations, identity_key TEXT NOT NULL, server_id TEXT NOT NULL,
 shard TEXT NOT NULL, generation TEXT NOT NULL, local_id INTEGER NOT NULL, sort_key TEXT NOT NULL,
 payload TEXT NOT NULL, source_ref TEXT NOT NULL, source_key TEXT NOT NULL, updated_at TEXT NOT NULL, change_seq INTEGER NOT NULL,
 UNIQUE(account_id,conversation_id,identity_key));
CREATE INDEX message_order ON messages(account_id,conversation_id,sort_key,message_id);
CREATE INDEX message_source ON messages(account_id,shard,generation,local_id);
CREATE INDEX message_reconcile ON messages(account_id,conversation_id,shard,local_id DESC);
CREATE INDEX message_changes ON messages(account_id,conversation_id,change_seq,message_id);
CREATE TABLE sync_state(account_id TEXT NOT NULL REFERENCES accounts, shard TEXT NOT NULL, table_name TEXT NOT NULL,
 generation TEXT NOT NULL, source_id TEXT, last_id INTEGER NOT NULL, upper_id INTEGER,
 signature TEXT, started_at TEXT NOT NULL, baseline_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id,shard,table_name));
CREATE TABLE history_ranges(account_id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations,
 lower_key TEXT NOT NULL, upper_key TEXT NOT NULL, PRIMARY KEY(account_id,conversation_id,lower_key,upper_key));
CREATE TABLE media_cache(cache_key TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
 kind TEXT NOT NULL, mime_type TEXT NOT NULL, data BLOB NOT NULL, size INTEGER NOT NULL,
 digest TEXT NOT NULL, quality TEXT, used_at REAL NOT NULL);
CREATE INDEX media_lru ON media_cache(kind,used_at);
CREATE TABLE conversation_settings(account_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE sync_jobs(task_id TEXT PRIMARY KEY, account_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE cursors(token TEXT PRIMARY KEY, account_id TEXT NOT NULL, conversation_id TEXT,
 kind TEXT NOT NULL, payload TEXT NOT NULL, expires REAL NOT NULL);
"""


class Store(DeliveryStoreMixin, LogStoreMixin, AiExecutionStoreMixin, AiStoreMixin, RoleStoreMixin):
    def __init__(self, path=DEFAULT_PATH, settings=None, image_limit=IMAGE_LIMIT):
        self.path, self.image_limit = Path(path) if str(path) != ":memory:" else path, image_limit
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-db")
        self.notify = lambda event: None
        try:
            self.call(self._open, settings)
        except Exception:
            def cleanup():
                if getattr(self, "db", None):
                    self.db.close()
                if getattr(self, "file_lock", None):
                    self.file_lock.close()
            self.executor.submit(cleanup).result()
            self.executor.shutdown()
            raise

    def call(self, method, *args):
        try:
            return self.executor.submit(method, *args).result()
        except sqlite3.Error:
            raise ConversationError("storage_unavailable", "本地数据库暂不可用，已有数据未被清空。", 503) from None

    def _open(self, settings):
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            import os
            self.file_lock = self.path.with_suffix(self.path.suffix+".lock").open("a+b")
            self.file_lock.seek(0, 2)
            if self.file_lock.tell() == 0:
                self.file_lock.write(b"0")
                self.file_lock.flush()
            self.file_lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file_lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ConversationError("storage_in_use", "数据库已由另一个后端占用，请使用单个后端进程。", 503) from None
        self.db = sqlite3.connect(str(self.path), timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-16384")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6, 7):
            self.db.close()
            raise ConversationError("storage_version_unsupported", "数据库版本高于当前程序，请使用匹配版本。", 503)
        if 0 < version < 7 and str(self.path) != ':memory:':
            backup_dir = self.path.parent / 'migration-backups'
            backup_dir.mkdir(exist_ok=True)
            backup_path = backup_dir / ('before-v7-' + uuid4().hex + '.sqlite3')
            with closing(sqlite3.connect(backup_path)) as backup:
                self.db.backup(backup)
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ConversationError('backup_failed', '升级前备份未通过校验。', 503)
        if not version:
            # Explicit transaction: a failed migration leaves no half-created schema.
            try:
                self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                self.db.execute("INSERT INTO schema_migrations VALUES(1,?)", (now(),))
                self.db.execute("PRAGMA user_version=1")
                self.db.commit()
            except Exception:
                self.db.rollback()
                self.db.close()
                raise
        if version < 2:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("CREATE INDEX message_pending_transcripts ON messages(account_id,conversation_id,shard,generation,updated_at) "
                    "WHERE json_extract(payload,'$.type')='voice' AND json_extract(payload,'$.content.transcript') IS NULL")
                self.db.execute("INSERT INTO schema_migrations VALUES(2,?)", (now(),))
                self.db.execute("PRAGMA user_version=2")
        if version < 3:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                for statement in ROLE_SCHEMA:
                    self.db.execute(statement)
                self.db.execute("INSERT INTO schema_migrations VALUES(3,?)", (now(),))
                self.db.execute("PRAGMA user_version=3")
        if version < 4:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                for statement in AI_SCHEMA:
                    self.db.execute(statement)
                self.db.execute("INSERT INTO schema_migrations VALUES(4,?)", (now(),))
                self.db.execute("PRAGMA user_version=4")
        if version < 5:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                for statement in EXECUTION_SCHEMA:
                    self.db.execute(statement)
                self.db.execute("INSERT INTO ai_settings VALUES(1,?,0)", (DEFAULT_INSTRUCTIONS,))
                self.db.execute("INSERT INTO ai_sessions(account_id,conversation_id,auto_pending) SELECT account_id,conversation_id,ai_enabled FROM conversation_preferences")
                self.db.execute("INSERT INTO schema_migrations VALUES(5,?)", (now(),))
                self.db.execute("PRAGMA user_version=5")
        if version < 6:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                for statement in LOG_SCHEMA:
                    self.db.execute(statement)
                self._log_migrate()
                self.db.execute("INSERT INTO schema_migrations VALUES(6,?)", (now(),))
                self.db.execute("PRAGMA user_version=6")
        if version < 7:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                for statement in DELIVERY_SCHEMA:
                    self.db.execute(statement)
                for row in self.db.execute('SELECT account_id,conversation_id FROM conversation_preferences WHERE ai_enabled=1').fetchall():
                    self._delivery_enable(*row)
                self.db.execute('INSERT INTO schema_migrations VALUES(7,?)', (now(),))
                self.db.execute('PRAGMA user_version=7')
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            self.db.close()
            raise ConversationError("storage_corrupt", "数据库校验失败，请保留原文件后恢复备份。", 503)
        try:
            with self.db:
                self._ai_recover()
                self._delivery_recover()
                self._log_recover()
                if settings and not self.db.execute("SELECT 1 FROM metadata WHERE key='settings_imported'").fetchone():
                    for key, value in settings._load().items():
                        self.db.execute("INSERT OR IGNORE INTO conversation_settings VALUES(?,?)", (key, dump(value)))
                    self.db.execute("INSERT INTO metadata VALUES('settings_imported','1')")
                self.db.execute("UPDATE accounts SET source_state='unknown'")
                for row in self.db.execute("SELECT * FROM sync_jobs").fetchall():
                    job = json.loads(row["payload"])
                    if job["status"] in ("queued", "running"):
                        job.update(status="failed", error="sync_interrupted", completed_at=now())
                        self.db.execute("UPDATE sync_jobs SET payload=? WHERE task_id=?", (dump(job), row["task_id"]))
        except Exception:
            self.db.close()
            raise

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        def finish():
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self.db.close()
            if getattr(self, "file_lock", None):
                self.file_lock.close()
        try:
            self.call(finish)
        finally:
            self.executor.shutdown()

    def _changed(self, account_id, resources, conversations=None):
        self.db.execute("UPDATE accounts SET revision=revision+1 WHERE account_id=?", (account_id,))
        revision = self.db.execute("SELECT revision FROM accounts WHERE account_id=?", (account_id,)).fetchone()[0]
        return dict(type="changed", account_id=account_id, revision=revision,
                    resources=resources, conversation_ids=(conversations or [])[:100])

    def _avatar(self, key, aid, avatar):
        if avatar:
            data = base64.b64decode(avatar["data_base64"], validate=True)
            self.db.execute("INSERT OR REPLACE INTO media_cache VALUES(?,?,?,?,?,?,?,?,?)",
                (key, aid, "avatar", avatar["mime_type"], data, len(data), hashlib.sha256(data).hexdigest(), None, time.time()))

    def _get_avatar(self, key):
        row = self.db.execute("SELECT mime_type,data FROM media_cache WHERE cache_key=?", (key,)).fetchone()
        return dict(mime_type=row[0], data_base64=base64.b64encode(row[1]).decode()) if row else None

    def save_accounts(self, snapshot):
        def write():
            events, found = [], []
            with self.db:
                for item in snapshot["accounts"]:
                    if not item.get("wxid") or item["status"] not in ("complete", "partial"):
                        continue
                    old = self.db.execute("SELECT account_id FROM accounts WHERE wxid=?", (item["wxid"],)).fetchone()
                    aid = old[0] if old else uuid4().hex
                    value = dict(item, account_id=aid, instances=[])
                    avatar = value.pop("avatar", None)
                    self.db.execute("INSERT INTO accounts(account_id,wxid,payload,last_seen_at,source_state) VALUES(?,?,?,?,'online') "
                        "ON CONFLICT(wxid) DO UPDATE SET payload=excluded.payload,last_seen_at=excluded.last_seen_at,source_state='online'",
                        (aid, item["wxid"], dump(value), now()))
                    self._avatar("account:"+aid, aid, avatar)
                    item["account_id"] = aid
                    found.append(aid)
                    events.append(self._changed(aid, ["accounts"]))
                state = "offline" if snapshot["summary"]["coverage_complete"] else "unknown"
                for row in self.db.execute("SELECT account_id,source_state FROM accounts").fetchall():
                    if row[0] not in found and row[1] != state:
                        self.db.execute("UPDATE accounts SET source_state=? WHERE account_id=?", (state, row[0]))
                        events.append(self._changed(row[0], ["accounts"]))
            for event in events:
                self.notify(event)
            return snapshot
        return self.call(write)

    def _account(self, aid):
        row = self.db.execute("SELECT * FROM accounts WHERE account_id=?", (aid,)).fetchone()
        if not row:
            raise ConversationError("account_not_found", "账号不存在。", 404)
        value = json.loads(row["payload"])
        value.update(avatar=self._get_avatar("account:"+aid), source_state=row["source_state"],
            last_seen_at=row["last_seen_at"], last_synced_at=row["last_synced_at"], revision=row["revision"])
        baseline = self.db.execute("SELECT started_at,signature FROM sync_state WHERE account_id=? AND shard='@monitor'", (aid,)).fetchone()
        pending = self.db.execute("SELECT 1 FROM sync_state WHERE account_id=? AND upper_id IS NOT NULL LIMIT 1", (aid,)).fetchone()
        status = self.db.execute("SELECT value FROM metadata WHERE key=?", ("monitor_status:"+aid,)).fetchone()
        initialized = {r[0] for r in self.db.execute("SELECT shard FROM sync_state WHERE account_id=? AND table_name=''",(aid,)).fetchall()}
        initializing = bool(set(json.loads(baseline[1]))-initialized) if baseline else row["source_state"]=="online"
        value.update(monitoring_started_at=baseline[0] if baseline else None, sync_pending=bool(pending) or initializing,
                     sync_issues=json.loads(status[0]) if status else [])
        if self.db.execute("SELECT 1 FROM sync_state WHERE account_id=? AND table_name<>'' AND source_id IS NULL LIMIT 1",(aid,)).fetchone():
            value["sync_issues"]=sorted(set(value["sync_issues"]+["monitor_source_unresolved"]))
        return value

    def account(self, aid):
        return self.call(self._account, aid)

    def accounts(self):
        return self.call(lambda: [self._account(r[0]) for r in self.db.execute("SELECT account_id FROM accounts ORDER BY rowid").fetchall()])

    def set_state(self, aid, state):
        def write():
            if self._account(aid)["source_state"] == state:
                return
            with self.db:
                self.db.execute("UPDATE accounts SET source_state=? WHERE account_id=?", (state, aid))
                event = self._changed(aid, ["accounts"])
            self.notify(event)
        self.call(write)

    def sync_error(self, aid, reason):
        def write():
            key = "sync_error:"+aid
            old = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            if (old[0] if old else "") == (reason or ""):
                return
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key,reason or ""))
                event = self._changed(aid,["conversations"])
            self.notify(event)
        self.call(write)

    def settings(self, wxid, value=None):
        from .conversation_settings import ConversationSettingsStore
        from .conversation_models import Exclusions
        key = ConversationSettingsStore.key(wxid)
        def query():
            if value is not None:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO conversation_settings VALUES(?,?)", (key, dump(value)))
            row = self.db.execute("SELECT payload FROM conversation_settings WHERE account_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else Exclusions().model_dump()
        return self.call(query)

    def _conversation(self, aid, cid):
        row = self.db.execute("SELECT * FROM conversations WHERE account_id=? AND conversation_id=?", (aid, cid)).fetchone()
        if not row:
            raise ConversationError("conversation_not_found", "会话不存在。", 404)
        return row

    def conversation(self, aid, cid):
        return self.call(lambda: dict(self._conversation(aid, cid)))

    def _ensure_conversation(self, aid, source):
        row = self.db.execute("SELECT conversation_id FROM conversations WHERE account_id=? AND source_id=?", (aid, source)).fetchone()
        if row:
            return row[0]
        cid = uuid4().hex
        payload = dict(conversation_id=cid, name=source, category="unknown", is_container=False,
                       summary=None, last_message_at=None, unread_count=None, avatar=None)
        self.db.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?,?,1)", (cid, aid, source, dump(payload), "unknown", 0, now()))
        return cid

    def save_conversations(self, aid, result):
        def write():
            changed = False
            with self.db:
                # Classification/metadata failures must not delete older readable conversations.
                seen = []
                for item in result["items"]:
                    cid = self._ensure_conversation(aid, item["source_id"])
                    old = self._conversation(aid, cid)
                    value = {k: v for k, v in item.items() if k not in ("source_id", "sort_timestamp", "avatar")}
                    value["conversation_id"] = cid
                    sort = item.get("sort_timestamp", 0)
                    payload = dump(value)
                    changed |= old["payload"] != payload or not old["active"] or old["sort_timestamp"] != sort
                    self.db.execute("UPDATE conversations SET payload=?,category=?,sort_timestamp=?,last_synced_at=?,active=1 WHERE conversation_id=?",
                        (payload, value["category"], sort, now(), cid))
                    if item.get("avatar") != self._get_avatar("conversation:"+cid) and item.get("avatar"):
                        changed = True
                        self._avatar("conversation:"+cid, aid, item["avatar"])
                    seen.append(cid)
                self.db.execute("UPDATE accounts SET last_synced_at=? WHERE account_id=?", (now(), aid))
                if result["status"] == "complete":
                    for old in self.db.execute("SELECT conversation_id FROM conversations WHERE account_id=? AND active=1", (aid,)).fetchall():
                        if old[0] not in seen and not self.db.execute("SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1", (old[0],)).fetchone():
                            self.db.execute("UPDATE conversations SET active=0 WHERE conversation_id=?", (old[0],))
                            changed = True
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("conversations:"+aid, dump(dict(status=result["status"], issues=result["issues"]))))
                event = self._changed(aid, ["conversations"]) if changed else None
            if event:
                self.notify(event)
        self.call(write)

    def _cursor(self, aid, cid, kind, payload):
        token = uuid4().hex
        self.db.execute("DELETE FROM cursors WHERE expires<?", (time.time(),))
        self.db.execute("DELETE FROM cursors WHERE token IN (SELECT token FROM cursors ORDER BY expires DESC LIMIT -1 OFFSET 10000)")
        self.db.execute("INSERT INTO cursors VALUES(?,?,?,?,?,?)", (token, aid, cid, kind, dump(payload), time.time()+86400))
        return token

    def _decode(self, token, aid, cid, kind):
        if token is None:
            return {}
        row = self.db.execute("SELECT * FROM cursors WHERE token=?", (token,)).fetchone()
        if not row or (row["account_id"], row["conversation_id"], row["kind"]) != (aid, cid, kind) or row["expires"] < time.time():
            raise ConversationError("cursor_expired", "游标已失效，请重新加载。")
        return json.loads(row["payload"])

    def conversations(self, aid, cursor=None, size=50):
        def query():
            account = self._account(aid)
            from .conversation_settings import ConversationSettingsStore
            from .conversation_models import Exclusions
            setting = self.db.execute("SELECT payload FROM conversation_settings WHERE account_key=?", (ConversationSettingsStore.key(account["wxid"]),)).fetchone()
            rules = json.loads(setting[0]) if setting else Exclusions().model_dump()
            anchor = self._decode(cursor, aid, None, "conversations")
            if anchor and anchor["rules"] != rules:
                raise ConversationError("cursor_expired", "过滤条件已变化，请重新加载。")
            # Immutable bounded ID snapshot: later refreshes cannot move pagination membership.
            ids = anchor.get("ids")
            if ids is None:
                rows = self.db.execute("SELECT conversation_id,category FROM conversations WHERE account_id=? AND active=1 ORDER BY sort_timestamp DESC,conversation_id", (aid,)).fetchall()
                ids = [r[0] for r in rows if not rules.get(r[1], False)]
                total_raw = len(rows)
            else:
                total_raw = anchor["raw"]
            start = anchor.get("offset", 0)
            items = []
            preferences = {r["conversation_id"]: r for r in self.db.execute(
                "SELECT conversation_id,ai_enabled,revision,role_card_code,api_profile_id FROM conversation_preferences WHERE account_id=? AND conversation_id IN ("
                + ",".join("?" for _ in ids[start:start+size]) + ")", (aid, *ids[start:start+size])).fetchall()}
            for cid in ids[start:start+size]:
                row = self._conversation(aid, cid)
                item = json.loads(row["payload"])
                item["avatar"] = self._get_avatar("conversation:"+cid)
                preference = preferences.get(cid)
                item["ai_enabled"] = bool(preference["ai_enabled"]) if preference else False
                item["preferences_revision"] = preference["revision"] if preference else 0
                item["ai_configuration_ready"] = self._effective(preference)["ready"] if item["ai_enabled"] else None
                counts = self._ai_counts(aid, cid)
                item.update(ai_pending_count=counts["pending_count"], ai_waiting_count=counts["waiting_count"],
                            ai_paused=bool(self._ai_session(aid,cid)["paused"]), memory_revision=self._preferences(aid,cid)["memory_revision"])
                latest_run = self.db.execute("SELECT status FROM ai_runs WHERE account_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1", (aid,cid)).fetchone()
                item["ai_run_status"] = latest_run[0] if latest_run else None
                items.append(item)
            with self.db:
                next_cursor = self._cursor(aid, None, "conversations", dict(ids=ids, offset=start+size, rules=rules, raw=total_raw, revision=anchor.get("revision", account["revision"]))) if start+size < len(ids) else None
            meta = self.db.execute("SELECT value FROM metadata WHERE key=?", ("conversations:"+aid,)).fetchone()
            extra = json.loads(meta[0]) if meta else dict(status="partial", issues=[])
            failure = self.db.execute("SELECT value FROM metadata WHERE key=?", ("sync_error:"+aid,)).fetchone()
            failed = bool(failure and failure[0])
            if failed:
                extra["issues"] = sorted(set(extra["issues"]+[failure[0]]))
            return dict(account_id=aid, snapshot_id=str(anchor.get("revision", account["revision"])), observed_at=account["last_synced_at"] or now(),
                items=items, total=len(ids), unfiltered_total=total_raw, next_cursor=next_cursor, applied_exclusions=rules,
                stale=failed or account["source_state"] != "online", last_synced_at=account["last_synced_at"], source_state=account["source_state"],
                coverage="complete" if meta and extra["status"] == "complete" else "partial" if meta else "not_synced",
                needs_sync=not bool(meta), revision=account["revision"], **extra)
        return self.call(query)

    def _upsert_message(self, aid, cid, item, generation="legacy", incremental=False):
        row = dict(item)
        ref, key = row.pop("_ref"), row.pop("_key")
        generation = ref.get("generation", generation)
        current = self.db.execute("SELECT s.generation FROM sync_state s JOIN conversations c ON c.account_id=s.account_id AND c.source_id=s.source_id WHERE c.conversation_id=? AND s.shard=?", (cid,ref["shard"])).fetchone()
        if current and current[0].split(":")[0] == generation:
            generation = current[0]
            ref = dict(ref,generation=generation)
        sid = str(ref.get("server_id") or "0")
        valid_sid = sid.isdecimal() and int(sid) > 0
        identity = "s:"+sid if valid_sid else dump([generation, ref["shard"], ref["local_id"]])
        old = self.db.execute("SELECT message_id,payload,source_ref FROM messages WHERE account_id=? AND conversation_id=? AND identity_key=?", (aid, cid, identity)).fetchone()
        mid = old[0] if old else uuid4().hex
        row.update(message_id=mid, sort_key=order_key(key))
        payload = dump(row)
        if old and old[1] == payload and old[2] == dump(ref):
            self._delivery_note_message(aid, cid, row, ref, incremental)
            # Rotate pending transcript checks without emitting a content change.
            self.db.execute("UPDATE messages SET updated_at=? WHERE message_id=?", (now(), mid))
            return False
        revision = self.db.execute("SELECT revision+1 FROM accounts WHERE account_id=?",(aid,)).fetchone()[0]
        self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,conversation_id,identity_key) DO UPDATE SET "
            "shard=excluded.shard,generation=excluded.generation,local_id=excluded.local_id,sort_key=excluded.sort_key,payload=excluded.payload,source_ref=excluded.source_ref,source_key=excluded.source_key,updated_at=excluded.updated_at,change_seq=excluded.change_seq",
            (mid, aid, cid, identity, sid, ref["shard"], generation, ref["local_id"], row["sort_key"], payload, dump(ref), dump(key), now(),revision))
        self._ai_note_message(aid, cid, row)
        self._delivery_note_message(aid, cid, row, ref, incremental)
        return True

    def _range(self, aid, cid, lower, upper):
        rows = self.db.execute("SELECT lower_key,upper_key FROM history_ranges WHERE account_id=? AND conversation_id=? AND lower_key<=? AND upper_key>=?", (aid, cid, upper, lower)).fetchall()
        for lo, hi in rows:
            lower, upper = min(lower, lo), max(upper, hi)
            self.db.execute("DELETE FROM history_ranges WHERE account_id=? AND conversation_id=? AND lower_key=? AND upper_key=?", (aid, cid, lo, hi))
        self.db.execute("INSERT OR REPLACE INTO history_ranges VALUES(?,?,?,?)", (aid, cid, lower, upper))

    def history_body(self, aid, cid, cursor, size):
        def query():
            source = self._conversation(aid, cid)["source_id"]
            value = self._decode(cursor, aid, cid, "messages")
            boundary = value.get("source_boundary")
            return dict(source_id=source, page_size=size, direction="older" if boundary else "latest", boundary=boundary, upper=None)
        return self.call(query)

    def save_history(self, aid, cid, result, body):
        def write():
            changed = False
            with self.db:
                for item in result["items"]:
                    changed |= self._upsert_message(aid, cid, item)
                if not result.get("retry_required", bool(result.get("issues"))):
                    keys = [order_key(i["_key"]) for i in result["items"]]
                    lower = min(keys) if keys and result["has_more"] else ""
                    upper = order_key(body["boundary"]) if body.get("boundary") else "~"
                    self._range(aid, cid, lower, upper)
                self.db.execute("UPDATE conversations SET last_synced_at=? WHERE conversation_id=?", (now(), cid))
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("history:"+cid, dump(dict(issues=result["issues"], status=result["status"]))))
                event = self._changed(aid, ["messages"], [cid])
            self.notify(event)
        self.call(write)

    def messages(self, aid, cid, cursor=None, size=50):
        def query():
            account, conv = self._account(aid), self._conversation(aid, cid)
            anchor = self._decode(cursor, aid, cid, "messages")
            if anchor.get("mode") == "changes":
                upper = anchor.get("upper") or account["revision"]
                after = anchor["after"]
                changed = self.db.execute("SELECT payload,change_seq,message_id FROM messages WHERE account_id=? AND conversation_id=? AND (change_seq,message_id)>(?,?) AND change_seq<=? ORDER BY change_seq,message_id LIMIT ?", (aid,cid,*after,upper,size+1)).fetchall()
                changed, more = bounded_page(changed, size)
                next_after=[changed[-1]["change_seq"],changed[-1]["message_id"]] if more else [upper,"~"]
                with self.db:
                    newer=self._cursor(aid,cid,"messages",dict(mode="changes",after=next_after,upper=upper if more else None))
                items=sorted((self._ai_message(json.loads(r["payload"]),aid,cid) for r in changed),key=lambda item:(item["sort_key"],item["message_id"]))
                return dict(account_id=aid,conversation_id=cid,snapshot_id=str(upper),observed_at=now(),items=items,
                    older_cursor=None,newer_cursor=newer,has_more=more,issues=[],status="complete",needs_sync=False,
                    coverage="partial",last_synced_at=conv["last_synced_at"],source_state=account["source_state"],revision=upper)
            boundary = anchor.get("boundary")
            where, params = "account_id=? AND conversation_id=?", [aid, cid]
            if boundary:
                where += " AND (sort_key,message_id)<(?,?)"
                params.extend(boundary)
            point = boundary[0] if boundary else "~"
            segment = self.db.execute("SELECT lower_key,upper_key FROM history_ranges WHERE account_id=? AND conversation_id=? AND lower_key<? AND upper_key>=? ORDER BY lower_key LIMIT 1", (aid, cid, point, point)).fetchone()
            if segment:
                where += " AND sort_key>=?"
                params.append(segment[0])
            rows = self.db.execute("SELECT * FROM messages WHERE "+where+" ORDER BY sort_key DESC,message_id DESC LIMIT ?", (*params, size+1)).fetchall()
            rows, more = bounded_page(rows, size)
            needs = not bool(segment) or (not more and len(rows) < size and bool(segment[0]))
            # A disconnected archive island is useful but explicitly incomplete.
            older_exists = more or not segment or bool(segment[0])
            with self.db:
                if not segment:
                    older = cursor or self._cursor(aid, cid, "messages", anchor)
                elif rows:
                    last = rows[-1]
                    older = self._cursor(aid, cid, "messages", dict(boundary=[last["sort_key"],last["message_id"]], source_boundary=json.loads(last["source_key"]))) if older_exists else None
                else:
                    older = cursor if older_exists else None
                newer = self._cursor(aid, cid, "messages", dict(mode="changes",after=[account["revision"],"~"]))
            extra = self.db.execute("SELECT value FROM metadata WHERE key=?", ("history:"+cid,)).fetchone()
            issues = json.loads(extra[0])["issues"] if extra else []
            return dict(account_id=aid, conversation_id=cid, snapshot_id=str(account["revision"]),
                observed_at=conv["last_synced_at"] or now(), items=[self._ai_message(json.loads(r["payload"]),aid,cid) for r in reversed(rows)],
                older_cursor=older, newer_cursor=newer, has_more=older_exists, issues=issues,
                status="partial" if needs or issues else "complete", needs_sync=needs,
                coverage="complete" if segment and not segment[0] else "partial" if rows or segment else "not_synced",
                last_synced_at=conv["last_synced_at"], source_state=account["source_state"], revision=account["revision"])
        return self.call(query)

    def reference(self, aid, cid, mid):
        def query():
            row = self.db.execute("SELECT source_ref FROM messages WHERE account_id=? AND conversation_id=? AND message_id=?", (aid, cid, mid)).fetchone()
            if not row:
                raise ConversationError("message_not_found", "消息不存在。", 404)
            return json.loads(row[0])
        return self.call(query)

    def image(self, aid, cid, mid, value=None):
        self.reference(aid, cid, mid)
        def query():
            key = "image:"+mid
            event = None
            with self.db:
                if value and value["status"] == "available":
                    data = base64.b64decode(value["data_base64"], validate=True)
                    if len(data) > 8*1024*1024:
                        raise ConversationError("image_size_limit", "图片超过缓存上限。", 422)
                    self.db.execute("INSERT OR REPLACE INTO media_cache VALUES(?,?,?,?,?,?,?,?,?)", (key, aid, "image", value["mime_type"], data, len(data), hashlib.sha256(data).hexdigest(), value.get("quality"), time.time()))
                    total = self.db.execute("SELECT COALESCE(SUM(size),0) FROM media_cache WHERE kind='image'").fetchone()[0]
                    for old in self.db.execute("SELECT cache_key,size FROM media_cache WHERE kind='image' ORDER BY used_at").fetchall():
                        if total <= self.image_limit:
                            break
                        self.db.execute("DELETE FROM media_cache WHERE cache_key=?", (old[0],))
                        total -= old[1]
                    event = self._changed(aid, ["images"], [cid])
                    self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
                    self.db.execute("UPDATE messages SET change_seq=? WHERE message_id=?", (event["revision"], mid))
                    event["resources"].append("messages")
                row = self.db.execute("SELECT * FROM media_cache WHERE cache_key=? AND account_id=?", (key, aid)).fetchone()
                if row:
                    self.db.execute("UPDATE media_cache SET used_at=? WHERE cache_key=?", (time.time(), key))
            if event:
                self.notify(event)
            if not row:
                return dict(message_id=mid, status="unavailable", reason="image_not_cached", needs_sync=True)
            return dict(message_id=mid, status="available", mime_type=row["mime_type"], data_base64=base64.b64encode(row["data"]).decode(), quality=row["quality"], needs_sync=False)
        return self.call(query)

    def states(self, aid):
        def query():
            values = [dict(r) for r in self.db.execute("SELECT * FROM sync_state WHERE account_id=?", (aid,)).fetchall()]
            for state in values:
                if state.get("source_id"):
                    row = self.db.execute("SELECT conversation_id FROM conversations WHERE account_id=? AND source_id=?", (aid,state["source_id"])).fetchone()
                    if row:
                        params = (aid,row[0],state["shard"],state["generation"])
                        recent = [r[0] for r in self.db.execute("SELECT local_id FROM messages WHERE account_id=? AND conversation_id=? AND shard=? AND generation=? ORDER BY local_id DESC LIMIT 25", params).fetchall()]
                        pending = [r[0] for r in self.db.execute("SELECT local_id FROM messages WHERE account_id=? AND conversation_id=? AND shard=? AND generation=? AND json_extract(payload,'$.type')='voice' AND json_extract(payload,'$.content.transcript') IS NULL ORDER BY updated_at LIMIT 25", params).fetchall()]
                        state["reconcile_ids"] = list(dict.fromkeys(recent+pending))
            return values
        return self.call(query)

    def save_delta(self, aid, result):
        if result.get("unchanged"):
            return
        def write():
            changed = set()
            before = self._account(aid)
            with self.db:
                for group in result.get("groups", []):
                    source = group.get("source_id")
                    cid = self._ensure_conversation(aid, source) if source else None
                    if group.get("reset") and cid:
                        self.db.execute("DELETE FROM history_ranges WHERE conversation_id=?", (cid,))
                        changed.add(cid)
                    for item in group.get("items", []):
                        previous = self.db.execute('SELECT last_id,generation FROM sync_state WHERE account_id=? AND shard=? AND table_name=?', (aid,group['shard'],group['table_name'])).fetchone()
                        fresh = (not group.get('reset') and item['_ref']['local_id'] > (previous['last_id'] if previous and previous['generation']==group['generation'] else group.get('baseline_id',0)))
                        if cid and self._upsert_message(aid, cid, item, group["generation"], incremental=fresh):
                            changed.add(cid)
                    if cid and group.get("items"):
                        self.db.execute("UPDATE conversations SET last_synced_at=? WHERE conversation_id=?", (now(), cid))
                    self.db.execute("INSERT INTO sync_state VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,shard,table_name) DO UPDATE SET "
                        "generation=excluded.generation,source_id=excluded.source_id,last_id=excluded.last_id,upper_id=excluded.upper_id,signature=excluded.signature,baseline_id=excluded.baseline_id",
                        (aid,group["shard"],group["table_name"],group["generation"],source,group["last_id"],group.get("upper_id"),dump(group.get("signature")),now(),group.get("baseline_id",0)))
                if not result.get("unchanged"):
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("monitor_status:"+aid,dump(result.get("issues",[]))))
                if result.get("groups"):
                    self.db.execute("UPDATE accounts SET last_synced_at=? WHERE account_id=?", (now(), aid))
                after = self._account(aid)
                state_changed = any(before[k]!=after[k] for k in ("monitoring_started_at","sync_pending","sync_issues"))
                resources = (["messages", "conversations"] if changed else [])+(["accounts"] if state_changed else [])
                event = self._changed(aid, resources, sorted(changed)) if resources else None
            if event:
                self.notify(event)
        self.call(write)

    def job(self, task_id, value=None, kind=None):
        def query():
            if value is not None:
                saved = json.loads(dump(value))
                if saved.get("image"):
                    saved["image"].pop("data_base64", None)
                if saved.get("page"):
                    saved["page"]["_message_ids"] = [m["message_id"] for m in saved["page"]["items"]]
                    saved["page"]["items"] = []
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO sync_jobs VALUES(?,?,?,?,?)", (task_id,value.get("account_id"),kind,dump(saved),time.time()))
                    self.db.execute("DELETE FROM sync_jobs WHERE updated_at<? AND task_id<>?", (time.time()-86400,task_id))
                    self.db.execute("DELETE FROM sync_jobs WHERE task_id IN (SELECT task_id FROM sync_jobs WHERE json_extract(payload,'$.status') NOT IN ('queued','running') ORDER BY updated_at DESC LIMIT -1 OFFSET 256)")
            row = self.db.execute("SELECT payload FROM sync_jobs WHERE task_id=?", (task_id,)).fetchone()
            result = json.loads(row[0]) if row else None
            if result and result.get("page"):
                ids=result["page"].pop("_message_ids",[])
                result["page"]["items"]=[self._ai_message(json.loads(r[0]),result["account_id"],result["conversation_id"]) for mid in ids if (r:=self.db.execute("SELECT payload FROM messages WHERE message_id=? AND account_id=?",(mid,result["account_id"])).fetchone())]
            return result
        return self.call(query)
