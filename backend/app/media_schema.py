"""Additive v11: immutable media, user library and per-request candidate snapshots."""
from datetime import datetime, timezone

MEDIA_SCHEMA = (
    'CREATE TABLE reply_media(media_id TEXT PRIMARY KEY,mime_type TEXT NOT NULL,data BLOB NOT NULL,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL UNIQUE,width INTEGER NOT NULL,height INTEGER NOT NULL,frame_count INTEGER NOT NULL)',
    'CREATE TABLE stickers(asset_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,enabled INTEGER NOT NULL,builtin INTEGER NOT NULL,source TEXT,revision INTEGER NOT NULL,original_media_id TEXT NOT NULL REFERENCES reply_media,send_media_id TEXT NOT NULL REFERENCES reply_media)',
    'CREATE TABLE reply_candidates(run_id TEXT NOT NULL REFERENCES ai_runs ON DELETE CASCADE,kind TEXT NOT NULL,reference TEXT NOT NULL,media_id TEXT NOT NULL REFERENCES reply_media,name TEXT NOT NULL,description TEXT NOT NULL,PRIMARY KEY(run_id,kind,reference))',
    "ALTER TABLE deliveries ADD kind TEXT NOT NULL DEFAULT 'text' CHECK(kind IN ('text','image','sticker'))",
    'ALTER TABLE deliveries ADD media_id TEXT REFERENCES reply_media',
    'ALTER TABLE deliveries ADD native_evidence TEXT',
)


def upgrade_v11(db):
    if db.execute('PRAGMA user_version').fetchone()[0] == 10:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for sql in MEDIA_SCHEMA: db.execute(sql)
            db.execute('INSERT INTO schema_migrations VALUES(11,?)', (datetime.now(timezone.utc).isoformat(),))
            db.execute('PRAGMA user_version=11')
