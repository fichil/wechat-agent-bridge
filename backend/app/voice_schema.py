"""Transactional v13 audio storage; the compatibility release does not invoke it."""
from datetime import datetime, timezone
import re


VOICE_TABLES = (
    'CREATE TABLE speech_profiles(profile_id TEXT PRIMARY KEY REFERENCES api_profiles ON DELETE CASCADE,protocol TEXT NOT NULL,model TEXT NOT NULL)',
    'CREATE TABLE reply_audio(audio_id TEXT PRIMARY KEY,wav BLOB NOT NULL,silk BLOB NOT NULL,duration_ms INTEGER NOT NULL CHECK(duration_ms>0 AND duration_ms<=60000),sample_rate INTEGER NOT NULL,wav_sha256 TEXT NOT NULL,silk_sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL)',
    'CREATE TABLE voice_preparations(run_id TEXT PRIMARY KEY REFERENCES ai_runs ON DELETE CASCADE,payload TEXT NOT NULL)',
)


def upgrade_v13(db):
    if db.execute('PRAGMA user_version').fetchone()[0] != 12:
        return
    if db.in_transaction:
        raise RuntimeError('voice_migration_requires_transaction_boundary')
    # Rebuild the CHECK constraint without rewriting referencing foreign keys.
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for statement in VOICE_TABLES:
                db.execute(statement)
            original = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='deliveries'").fetchone()[0]
            indexes = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='deliveries' AND type IN ('index','trigger') AND sql IS NOT NULL")]
            columns = [r[1] for r in db.execute('PRAGMA table_info(deliveries)')]
            marker = "CHECK(kind IN ('text','image','sticker'))"
            if marker not in original:
                raise RuntimeError('unexpected_delivery_schema')
            definition = re.sub(r'^CREATE TABLE (?:"deliveries"|deliveries)', 'CREATE TABLE deliveries_voice_migration', original, count=1)
            definition = definition.replace(marker, "CHECK(kind IN ('text','image','sticker','voice'))")
            db.execute(definition)
            db.execute('ALTER TABLE deliveries_voice_migration ADD audio_id TEXT REFERENCES reply_audio')
            names = ','.join('"' + name + '"' for name in columns)
            db.execute(f'INSERT INTO deliveries_voice_migration({names}) SELECT {names} FROM deliveries ORDER BY rowid')
            db.execute('DROP TABLE deliveries')
            db.execute('ALTER TABLE deliveries_voice_migration RENAME TO deliveries')
            for statement in indexes:
                db.execute(statement)
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('voice_migration_foreign_key_failure')
            db.execute('INSERT INTO schema_migrations VALUES(13,?)', (datetime.now(timezone.utc).isoformat(),))
            db.execute('PRAGMA user_version=13')
    finally:
        db.execute('PRAGMA foreign_keys=' + str(foreign_keys))
