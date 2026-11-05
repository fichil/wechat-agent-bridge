"""One-shot maintenance for a disabled group, without starting workers or WeChat.

Back up and reserve the exact memory revision before any paid request. A crash
leaves the reservation in place; rerunning never repeats an uncertain request.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .app.ai_memory import MEMORY_INSTRUCTIONS, MEMORY_LIMIT
from .app.ai_provider import Provider
from .app.diagnostics import log_context
from .app.log_storage import LogStoreMixin
from .app import secret_protection


class MaintenanceLog(LogStoreMixin):
    def __init__(self, db): self.db = db
    def call(self, method, *args): return method(*args)
    def notify(self, event): pass
    def _log_prune(self): pass


def compact_disabled_group(db, aid, cid, *, provider=None, reveal=secret_protection.reveal, max_retries=2):
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    identity = uuid4().hex
    with db:
        db.execute('BEGIN IMMEDIATE')
        prefs = dict(db.execute('SELECT * FROM conversation_preferences WHERE account_id=? AND conversation_id=?', (aid, cid)).fetchone())
        conversation = db.execute('SELECT source_id FROM conversations WHERE account_id=? AND conversation_id=?', (aid, cid)).fetchone()
        if prefs['ai_enabled'] or not conversation[0].endswith('@chatroom'):
            raise ValueError('Maintenance requires a disabled group')
        if db.execute("SELECT 1 FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running','suspended')", (aid, cid)).fetchone():
            raise ValueError('Active work must finish or be cancelled before maintenance')
        marker = 'group-memory-maintenance:' + aid + ':' + cid + ':' + str(prefs['memory_revision'])
        if db.execute('SELECT 1 FROM metadata WHERE key=?', (marker,)).fetchone():
            raise ValueError('This memory revision already has a maintenance attempt; inspect its record before any further action')
        profile_id = prefs['api_profile_id'] or db.execute('SELECT api_profile_id FROM ai_defaults').fetchone()[0]
        profile = dict(db.execute('SELECT * FROM api_profiles WHERE profile_id=?', (profile_id,)).fetchone())
        encrypted_key = bytes(profile.pop('secret'))
        verification = json.loads(db.execute('SELECT payload FROM api_verifications WHERE profile_id=? AND revision=?', (profile_id, profile['revision'])).fetchone()[0])
        if verification['status'] != 'passed': raise ValueError('Verified profile required')
        record = dict(status='started', task_id=identity, account_id=aid, conversation_id=cid,
                      memory_revision=prefs['memory_revision'], original_memory=prefs['memory'],
                      started_at=datetime.now(timezone.utc).isoformat())
        db.execute('INSERT INTO metadata VALUES(?,?)', (marker, json.dumps(record, ensure_ascii=False)))
    logs = MaintenanceLog(db)
    task = dict(status='failed', account_id=aid, conversation_id=cid, operation='memory_compaction', trigger='manual')
    try:
        memory = prefs['memory']
        if len(memory) > MEMORY_LIMIT:
            envelope = dict(request_id=identity, instructions=MEMORY_INSTRUCTIONS, character_card={}, memory=memory,
                            messages=[], memory_max_chars=MEMORY_LIMIT, processing_type='memory_backfill',
                            role_card_edit_allowed=False, reply_allowed=False, reply_required=False,
                            reply_target_ids=[], trigger_messages=[])
            with log_context(logs, account_id=aid, conversation_id=cid, task_id=identity,
                             operation='memory_compaction', trigger='manual', http_attempt_count=1):
                result = (provider or Provider()).generate(profile, reveal(encrypted_key), envelope, verification['output_mode'])
            memory = result['memory']
            if not memory.strip() or len(memory) > MEMORY_LIMIT or result['reply'].strip() or result.get('role_card_update', '').strip():
                raise ValueError('Compaction did not obey the memory-only contract')
        with db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE conversation_preferences SET memory=?,memory_revision=memory_revision+?,ai_max_retries=?,revision=revision+1 '
                                 'WHERE account_id=? AND conversation_id=? AND ai_enabled=0 AND revision=? AND memory_revision=?',
                                 (memory, int(memory != prefs['memory']), max_retries, aid, cid, prefs['revision'], prefs['memory_revision'])).rowcount
            if changed != 1: raise ValueError('Conversation changed during compaction; result not applied')
            record.update(status='completed', completed_at=datetime.now(timezone.utc).isoformat(), memory_chars=len(memory))
            db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(record, ensure_ascii=False), marker))
        task['status'] = 'completed'
        return dict(status='completed', task_id=identity, before_chars=len(prefs['memory']), after_chars=len(memory),
                    ai_enabled=False, ai_max_retries=max_retries, backup_key=marker)
    except Exception:
        task['error'] = 'ai_memory_compaction_failed'
        with db:
            record.update(status='failed', completed_at=datetime.now(timezone.utc).isoformat())
            db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(record, ensure_ascii=False), marker))
        raise
    finally:
        logs.log_application(identity, task)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--conversation-id', required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=rw', uri=True, timeout=10) as db:
        result = compact_disabled_group(db, args.account_id, args.conversation_id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
