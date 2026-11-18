def remove_v5(db, preferences=True):
    remove_v7(db)
    for table in ("diagnostic_details", "diagnostic_logs"):
        db.execute("DROP TABLE IF EXISTS " + table)
    db.execute("DELETE FROM schema_migrations WHERE version=6")
    for table in ("ai_run_messages", "ai_message_state", "ai_runs", "ai_sessions", "api_tasks", "api_verifications", "ai_settings"):
        db.execute("DROP TABLE " + table)
    if preferences:
        db.execute("ALTER TABLE conversation_preferences DROP COLUMN memory_revision")
    db.execute("DELETE FROM schema_migrations WHERE version=5")
    db.execute("PRAGMA user_version=4")
    db.commit()


def remove_v7(db):
    remove_v8(db)
    for table in ('delivery_attempts','deliveries','reply_triggers','delivery_sessions'):
        db.execute('DROP TABLE IF EXISTS '+table)
    db.execute('DELETE FROM schema_migrations WHERE version=7')


def remove_v8(db):
    remove_v9(db)
    for table in ('group_round_messages', 'group_rounds', 'group_message_eligibility'):
        db.execute('DROP TABLE IF EXISTS '+table)
    columns = {r[1] for r in db.execute('PRAGMA table_info(conversation_preferences)')}
    for column in ('scan_interval_seconds', 'group_proactive_message_threshold'):
        if column in columns:
            db.execute('ALTER TABLE conversation_preferences DROP COLUMN '+column)
    db.execute('DELETE FROM schema_migrations WHERE version=8')


def remove_v9(db):
    remove_v10(db)
    db.execute('DROP TABLE IF EXISTS compatibility_state')
    db.execute('DELETE FROM schema_migrations WHERE version=9')


def remove_v10(db):
    remove_v11(db)
    for table in ('notification_outbox','account_observations','notification_settings','ai_request_snapshots'):
        db.execute('DROP TABLE IF EXISTS '+table)
    if 'ai_max_retries' in {r[1] for r in db.execute('PRAGMA table_info(conversation_preferences)')}:
        db.execute('ALTER TABLE conversation_preferences DROP COLUMN ai_max_retries')
    db.execute('DELETE FROM schema_migrations WHERE version=10')


def remove_v11(db):
    remove_v12(db)
    columns = {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}
    for name in ('native_evidence','media_id','kind'):
        if name in columns: db.execute('ALTER TABLE deliveries DROP COLUMN '+name)
    for table in ('reply_candidates','stickers','reply_media'):
        db.execute('DROP TABLE IF EXISTS '+table)
    db.execute("DELETE FROM metadata WHERE key='builtin_stickers_v1'")
    db.execute('DELETE FROM schema_migrations WHERE version=11')


def remove_v12(db):
    remove_v13(db)
    db.execute('DROP TABLE IF EXISTS role_card_derivations')
    db.execute('DELETE FROM schema_migrations WHERE version=12')


def remove_v13(db):
    """Construct a real pre-voice fixture, including the old delivery CHECK."""
    import re
    if 'audio_id' not in {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}: return
    db.commit()
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    db.execute('PRAGMA foreign_keys=OFF')
    with db:
        db.execute('ALTER TABLE deliveries DROP COLUMN audio_id')
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='deliveries'").fetchone()[0]
        indexes = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='deliveries' AND type IN ('index','trigger') AND sql IS NOT NULL")]
        sql = re.sub(r'^CREATE TABLE (?:"deliveries"|deliveries)', 'CREATE TABLE old_deliveries', sql)
        db.execute(sql.replace("CHECK(kind IN ('text','image','sticker','voice'))", "CHECK(kind IN ('text','image','sticker'))"))
        db.execute('INSERT INTO old_deliveries SELECT * FROM deliveries ORDER BY rowid')
        db.execute('DROP TABLE deliveries')
        db.execute('ALTER TABLE old_deliveries RENAME TO deliveries')
        for statement in indexes: db.execute(statement)
        for table in ('voice_preparations','reply_audio','speech_profiles'): db.execute('DROP TABLE IF EXISTS '+table)
        db.execute('DELETE FROM schema_migrations WHERE version=13')
        db.execute('PRAGMA user_version=12')
    db.execute('PRAGMA foreign_keys='+str(foreign_keys))
