"""v12: conversation-owned role derivations and an exact default-prompt upgrade."""
from datetime import datetime, timezone

from .ai_instructions_v11 import V11_DEFAULT_INSTRUCTIONS
from .ai_execution_models import DEFAULT_INSTRUCTIONS


ROLE_EVOLUTION_SCHEMA = (
    'CREATE TABLE role_card_derivations('
    'card_code TEXT PRIMARY KEY REFERENCES role_cards(card_code) ON DELETE CASCADE, '
    'account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, '
    'source_card_code TEXT NOT NULL, initial_name TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)), '
    'FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id) ON DELETE CASCADE)',
    'CREATE INDEX role_derivation_owner ON role_card_derivations(account_id,conversation_id)',
    'CREATE UNIQUE INDEX role_derivation_active ON role_card_derivations(account_id,conversation_id) WHERE active=1',
)


def upgrade_v12(db):
    if db.execute('PRAGMA user_version').fetchone()[0] == 11:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for sql in ROLE_EVOLUTION_SCHEMA:
                db.execute(sql)
            db.execute('UPDATE ai_settings SET instructions=?,revision=revision+1 WHERE instructions=?',
                       (DEFAULT_INSTRUCTIONS, V11_DEFAULT_INSTRUCTIONS))
            db.execute('INSERT INTO schema_migrations VALUES(12,?)', (datetime.now(timezone.utc).isoformat(),))
            db.execute('PRAGMA user_version=12')
