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
    db.execute('DROP TABLE IF EXISTS compatibility_state')
    db.execute('DELETE FROM schema_migrations WHERE version=9')
