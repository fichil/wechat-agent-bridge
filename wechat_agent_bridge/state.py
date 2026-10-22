"""SQLite idempotency journal; contains hashes and outcomes, never message text.

An ATTEMPTED record is committed *before* calling the adapter. After a crash,
that record is returned as UNKNOWN and must never trigger an automatic retry.
This journal is operational state, not conversation memory.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

from .domain import ActionRequest, ActionResult, ActionStatus, MessageEvent


def _digest(*values: str) -> str:
    data = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class StateStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS events (event_key TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS actions (
                request_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                attempted INTEGER NOT NULL
            );
        """)

    def remember_event(self, event: MessageEvent) -> bool:
        key = _digest(event.target.account_id, event.target.conversation_id, event.message_id)
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO events(event_key) VALUES (?)", (key,),
            )
        return cursor.rowcount == 1

    def claim(self, request: ActionRequest) -> ActionResult | None:
        key = _digest(request.request_id)
        fingerprint = _digest(
            request.target.account_id, request.target.conversation_id,
            request.operation, request.text,
        )
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO actions VALUES (?, ?, ?, ?, ?)",
                (key, fingerprint, ActionStatus.ATTEMPTED.value, "attempt_recorded", 1),
            )
            if cursor.rowcount == 1:
                return None
            row = self._db.execute(
                "SELECT fingerprint, status, reason, attempted FROM actions WHERE request_key = ?",
                (key,),
            ).fetchone()
        if row[0] != fingerprint:
            return ActionResult(
                request.request_id, ActionStatus.DENIED, "request_id_conflict", replayed=True,
            )
        result = ActionResult(request.request_id, ActionStatus(row[1]), row[2], bool(row[3]), True)
        if result.status is ActionStatus.ATTEMPTED:
            result = replace(result, status=ActionStatus.UNKNOWN, reason="previous_attempt_unresolved")
        return result

    def finish(self, result: ActionResult) -> None:
        with self._db:
            cursor = self._db.execute(
                "UPDATE actions SET status = ?, reason = ?, attempted = ? WHERE request_key = ?",
                (result.status.value, result.reason, int(result.attempted), _digest(result.request_id)),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("action was not claimed")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
