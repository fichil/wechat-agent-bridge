"""Encrypted local content and separate durable, content-free execution journal."""

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .console_models import ChatMessage, ConsoleError
from .protection import Protector


def digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class ConsoleStore:
    def __init__(self, path: str | Path, protector: Protector):
        self.protector = protector
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS records (bucket TEXT, key TEXT, payload BLOB NOT NULL,
            PRIMARY KEY(bucket,key));
          CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT, conversation TEXT, event TEXT UNIQUE, payload BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS seen (event TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, account TEXT,
            conversation TEXT, payload BLOB NOT NULL, mime TEXT);
          CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, fingerprint TEXT,
            status TEXT, reason TEXT, attempted INTEGER, created REAL);
          CREATE TABLE IF NOT EXISTS calls (created REAL);
        """)
        with self.db:
            columns = {row[1] for row in self.db.execute('PRAGMA table_info(messages)')}
            if 'display_order' not in columns:
                self.db.execute('ALTER TABLE messages ADD COLUMN display_order INTEGER')
                self.db.execute('UPDATE messages SET display_order=seq')

    def _pack(self, value) -> bytes:
        return self.protector.protect(json.dumps(value, ensure_ascii=False).encode())

    def _unpack(self, value: bytes):
        return json.loads(self.protector.unprotect(value))

    def put(self, bucket: str, key: str, value) -> None:
        payload = self._pack(value)
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (bucket, digest(key), payload))

    def get(self, bucket: str, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT payload FROM records WHERE bucket=? AND key=?", (bucket, digest(key))).fetchone()
            return self._unpack(row[0]) if row else default

    def values(self, bucket: str) -> list:
        with self.lock:
            return [self._unpack(row[0]) for row in self.db.execute("SELECT payload FROM records WHERE bucket=?", (bucket,))]

    def append(self, message: ChatMessage, *, older=False) -> bool:
        event = digest(message.account_id, message.conversation_id, message.id)
        payload = self._pack(message.to_dict())
        with self.lock, self.db:
            fresh = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (event,)).rowcount
            if not fresh:
                return False
            account, chat = digest(message.account_id), digest(message.account_id, message.conversation_id)
            edge = self.db.execute('SELECT '+('min' if older else 'max')+
                '(display_order) FROM messages WHERE account=? AND conversation=?', (account, chat)).fetchone()[0]
            position = (edge or 0) + (-1 if older else 1)
            self.db.execute("INSERT INTO messages(account,conversation,event,payload,display_order) VALUES (?,?,?,?,?)",
                            (account, chat, event, payload, position))
        return True

    def messages(self, account: str, conversation: str, limit=50, before: int | None = None, after: int | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.lock:
            incremental = after is not None
            scope = (digest(account), digest(account, conversation))
            if incremental:
                rows = self.db.execute('SELECT seq,payload,display_order FROM messages WHERE account=? AND conversation=? AND seq>? ORDER BY seq LIMIT ?', (*scope, after, limit)).fetchall()
            else:
                boundary = 2**63-1
                if before is not None:
                    anchor = self.db.execute('SELECT display_order FROM messages WHERE account=? AND conversation=? AND seq=?', (*scope, before)).fetchone()
                    if anchor is None:
                        return []
                    boundary = anchor[0]
                rows = self.db.execute('SELECT seq,payload,display_order FROM messages WHERE account=? AND conversation=? AND display_order<? ORDER BY display_order DESC LIMIT ?', (*scope, boundary, limit)).fetchall()
            return [{**self._unpack(payload), "seq": seq, "display_order": position}
                    for seq, payload, position in (rows if incremental else reversed(rows))]

    def asset(self, account: str, conversation: str, asset_id: str, data: bytes | None = None, mime=""):
        key = digest(account, conversation, asset_id)
        with self.lock, self.db:
            if data is not None:
                self.db.execute("INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?)",
                                (key, digest(account), digest(account, conversation), self.protector.protect(data), mime))
                return None
            row = self.db.execute("SELECT payload,mime FROM assets WHERE id=?", (key,)).fetchone()
            return (self.protector.unprotect(row[0]), row[1]) if row else None

    def selected_messages(self, account, conversation, events):
        with self.lock:
            result = []
            for event in events:
                row = self.db.execute('SELECT seq,payload FROM messages WHERE account=? AND conversation=? AND event=?',
                    (digest(account), digest(account, conversation), digest(account, conversation, event))).fetchone()
                if row is None:
                    raise ConsoleError('reply_recovery_message_missing')
                result.append({**self._unpack(row[1]), 'seq': row[0]})
            return sorted(result, key=lambda row: row['seq'])

    def register_reply_recovery(self, job):
        """Commit the task and per-event tombstones together, before any API call."""
        account, conversation = job['account_id'], job['conversation_id']
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous = self.get('job', job['id'])
            if previous:
                if any(previous.get(k) != job.get(k) for k in ('account_id', 'conversation_id', 'events', 'recovery_id')):
                    raise ConsoleError('reply_recovery_id_conflict')
                return previous, True
            for event in job['events']:
                if self.get('reply_recovery_event', digest(account, conversation, event)):
                    raise ConsoleError('reply_recovery_event_already_claimed')
            self.db.execute('INSERT INTO records VALUES (?,?,?)', ('job', digest(job['id']), self._pack(job)))
            for event in job['events']:
                self.db.execute('INSERT INTO records VALUES (?,?,?)', ('reply_recovery_event',
                    digest(digest(account, conversation, event)), self._pack({'job_id': job['id']})))
            return job, False

    def clear(self, account: str, conversation: str | None = None) -> None:
        """Keep seen/actions tombstones so deletion cannot cause a repeated send."""
        with self.lock, self.db:
            if conversation is None:
                self.db.execute("DELETE FROM messages WHERE account=?", (digest(account),))
                self.db.execute("DELETE FROM assets WHERE account=?", (digest(account),))
            else:
                scope = digest(account, conversation)
                self.db.execute("DELETE FROM messages WHERE conversation=?", (scope,))
                self.db.execute("DELETE FROM assets WHERE conversation=?", (scope,))
            for bucket in ("memory", "job", "extraction", "coverage"):
                for key, blob in self.db.execute("SELECT key,payload FROM records WHERE bucket=?", (bucket,)).fetchall():
                    value = self._unpack(blob)
                    if value.get("account_id") == account and (conversation is None or value.get("conversation_id") == conversation):
                        self.db.execute("DELETE FROM records WHERE bucket=? AND key=?", (bucket, key))

    def reserve_call(self, now: float, hourly: int, daily: int) -> dict:
        with self.lock, self.db:
            counts = self.usage(now)
            if counts["hour"] >= hourly or counts["day"] >= daily:
                raise ConsoleError("provider_budget_exhausted")
            self.db.execute("INSERT INTO calls VALUES (?)", (now,))
            self.db.execute("DELETE FROM calls WHERE created<?", (now-86400,))
        return counts

    def usage(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            return {name: self.db.execute("SELECT count(*) FROM calls WHERE created>?", (now-span,)).fetchone()[0]
                    for name, span in (("hour", 3600), ("day", 86400))}

    def claim(self, request_id: str, account: str, conversation: str, text: str):
        key, fingerprint = digest(request_id), digest(account, conversation, text)
        with self.lock, self.db:
            row = self.db.execute("SELECT fingerprint,status,reason,attempted FROM actions WHERE id=?", (key,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ConsoleError("request_id_conflict")
                return {"status": "unknown" if row[1] == "attempted" else row[1], "reason": row[2], "attempted": bool(row[3]), "replayed": True}
            self.db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)", (key, fingerprint, "attempted", "attempt_recorded", 1, time.time()))
        return None

    def finish(self, request_id: str, result: dict):
        with self.lock, self.db:
            self.db.execute("UPDATE actions SET status=?,reason=?,attempted=? WHERE id=?",
                            (result["status"], result["reason"], int(result["attempted"]), digest(request_id)))

    def outcomes(self) -> list[dict]:
        with self.lock:
            return [{"status": "unknown" if s == "attempted" else s, "reason": r, "attempted": bool(a), "created": t}
                    for s, r, a, t in self.db.execute("SELECT status,reason,attempted,created FROM actions ORDER BY created DESC LIMIT 30")]

    def unresolved(self, request_id: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT status FROM actions WHERE id=?", (digest(request_id),)).fetchone()
            return bool(row and row[0] in ("attempted", "unknown"))

    def close(self):
        with self.lock:
            self.db.close()
