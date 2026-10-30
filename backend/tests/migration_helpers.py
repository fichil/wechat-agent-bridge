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
    for table in ('delivery_attempts','deliveries','reply_triggers','delivery_sessions'):
        db.execute('DROP TABLE IF EXISTS '+table)
    db.execute('DELETE FROM schema_migrations WHERE version=7')
