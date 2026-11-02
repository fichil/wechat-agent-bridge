"""Additive v10 schema; the v9 bridge release can retain and read this database."""
from datetime import datetime, timezone

RELIABILITY_SCHEMA = (
    'ALTER TABLE conversation_preferences ADD ai_max_retries INTEGER NOT NULL DEFAULT 10 CHECK(ai_max_retries BETWEEN 0 AND 100)',
    'CREATE TABLE ai_request_snapshots(run_id TEXT PRIMARY KEY REFERENCES ai_runs ON DELETE CASCADE, payload TEXT NOT NULL)',
    'CREATE TABLE notification_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, payload TEXT NOT NULL, secret BLOB NOT NULL)',
    "INSERT INTO notification_settings VALUES(1,0,'{}',X'')",
    'CREATE TABLE account_observations(account_id TEXT PRIMARY KEY REFERENCES accounts ON DELETE CASCADE, state TEXT NOT NULL, last_online_at REAL, abnormal_since REAL, event_id TEXT)',
    'CREATE TABLE notification_outbox(notification_id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts ON DELETE CASCADE, kind TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL, settings_revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)',
    'CREATE INDEX notification_due ON notification_outbox(status,next_at)',
)


def upgrade_v10(db):
    if db.execute('PRAGMA user_version').fetchone()[0] == 9:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for statement in RELIABILITY_SCHEMA:
                db.execute(statement)
            db.execute('INSERT INTO schema_migrations VALUES(10,?)', (datetime.now(timezone.utc).isoformat(),))
            db.execute('PRAGMA user_version=10')
