"""Offline, targeted recovery: old group messages update memory without replying.

Preview: python -m backend.app.ai_input_recovery --database ... --account-id ...
         --conversation-id ... --expected-run-id ...
Apply adds --apply and requires the backend to have stopped gracefully.
"""
import argparse
from contextlib import closing, contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .storage import Store, dump
from .conversations import ConversationError


@contextmanager
def ownership_lock(path):
    handles = []
    try:
        for lock_path in (path.parent / 'portable-maintenance.lock', path.with_suffix(path.suffix + '.lock')):
            stream = lock_path.open('a+b')
            handles.append(stream)
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('请先正常停止使用此数据库的后端；未修改业务数据。') from None
        yield
    finally:
        for stream in reversed(handles):
            stream.close()


def inspect(db, aid, cid, expected):
    # Reuse query helpers without Store initialization, migrations or global recovery.
    reader = Store.__new__(Store)
    reader.db = db
    if db.execute('PRAGMA user_version').fetchone()[0] != 12:
        raise ValueError('仅支持当前 v12 数据库；不会自动迁移。')
    marker = 'ai-input-recovery:' + expected
    saved = db.execute('SELECT value FROM metadata WHERE key=?', (marker,)).fetchone()
    if saved:
        receipt = json.loads(saved[0])
        if (receipt['account_id'], receipt['conversation_id']) != (aid, cid):
            raise ValueError('恢复记录与指定账号或会话不一致。')
        return reader, dict(receipt, status='already_applied'), []
    prefs = reader._preferences(aid, cid)
    session = reader._ai_session(aid, cid)
    row = db.execute('SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1', (aid, cid)).fetchone()
    latest = json.loads(row[0]) if row else {}
    if (latest.get('run_id') != expected or latest.get('status') != 'failed'
            or latest.get('error') != 'ai_input_too_large' or not session['paused']
            or latest.get('epoch') != session['epoch'] or not prefs['ai_enabled']):
        raise ValueError('会话状态已变化，或最新任务不是预期的输入超限失败；未恢复。')
    if not reader._is_group(aid, cid):
        raise ValueError('此恢复命令仅用于群聊的历史记忆补录。')
    if db.execute("SELECT 1 FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running','suspended')", (aid, cid)).fetchone():
        raise ValueError('会话仍有在途或挂起任务；未恢复。')
    if reader._delivery_blocked(aid, cid):
        raise ValueError('会话存在待发送、失败或结果不确定的发送记录；请先核对。')
    reader._validate_ai(prefs)
    vision = reader._ai_vision(aid, cid)
    selected, waiting = [], 0
    for row in db.execute('SELECT * FROM messages WHERE account_id=? AND conversation_id=? ORDER BY sort_key,message_id', (aid, cid)):
        value, fingerprint, digest, state = reader._ai_item(row, vision)
        if state['status'] in ('pending', 'failed'):
            selected.append((value, fingerprint, digest))
        elif state['status'] != 'memorized':
            waiting += 1
    receipt = dict(status='preview', account_id=aid, conversation_id=cid, expected_run_id=expected,
                   pending_count=len(selected), waiting_count=waiting, epoch=session['epoch'],
                   memory_revision=prefs['memory_revision'],
                   memory_sha256=hashlib.sha256(prefs['memory'].encode('utf-8')).hexdigest())
    return reader, receipt, selected


def recover(database, aid, cid, expected, *, apply=False):
    path = Path(database).resolve(strict=True)
    with ownership_lock(path) if apply else nullcontext():
        with closing(sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            if not apply:
                db.execute('PRAGMA query_only=ON')
            # A consistent preview, with no migration, model request or service startup.
            db.execute('BEGIN')
            reader, receipt, selected = inspect(db, aid, cid, expected)
            db.rollback()
            if not apply or receipt['status'] == 'already_applied':
                return receipt
            backup_dir = path.parent / 'maintenance-backups'
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / ('ai-input-' + uuid4().hex + '.sqlite3')
            with closing(sqlite3.connect(backup)) as target:
                db.backup(target)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('恢复前备份校验失败；未修改业务数据。')
            with db:
                db.execute('BEGIN IMMEDIATE')
                reader, receipt, selected = inspect(db, aid, cid, expected)
                marker = 'ai-input-recovery:' + expected
                db.execute("UPDATE group_rounds SET status='cancelled' WHERE account_id=? AND conversation_id=? AND status='active'", (aid, cid))
                for table in ('reply_triggers', 'group_message_eligibility'):
                    db.execute('UPDATE ' + table + ' SET consumed_run_id=? WHERE account_id=? AND conversation_id=? AND epoch=? AND consumed_run_id IS NULL',
                               (marker, aid, cid, receipt['epoch']))
                # Late-arriving pre-recovery messages also cannot regain reply eligibility.
                reader._delivery_enable(aid, cid)
                cutoff = db.execute('SELECT enabled_at FROM delivery_sessions WHERE account_id=? AND conversation_id=?', (aid, cid)).fetchone()[0]
                round_id = uuid4().hex if selected else None
                if selected:
                    payload = dict(reply_trigger_ids=[], trigger_messages=[], message_count=len(selected), batch_count=0,
                                   memory_only=True, recovery_id=marker)
                    db.execute("INSERT INTO group_rounds VALUES(?,?,?,?,?,'active',?)", (round_id, aid, cid, receipt['epoch'], 'group_threshold', dump(payload)))
                    db.executemany('INSERT INTO group_round_messages VALUES(?,?,?,?,?,?,0)',
                                   ((round_id, v['message_id'], i, fp, digest, dump(v)) for i, (v, fp, digest) in enumerate(selected)))
                db.execute('UPDATE ai_sessions SET paused=0,auto_pending=1 WHERE account_id=? AND conversation_id=?', (aid, cid))
                receipt.update(status='applied', cutoff=cutoff, round_id=round_id, backup=str(backup))
                db.execute('INSERT INTO metadata VALUES(?,?)', (marker, dump(receipt)))
            return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--conversation-id', required=True)
    parser.add_argument('--expected-run-id', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = recover(args.database, args.account_id, args.conversation_id, args.expected_run_id, apply=args.apply)
    except (ValueError, OSError, sqlite3.Error, ConversationError) as error:
        print(json.dumps(dict(status='rejected', message=error.message if isinstance(error, ConversationError) else str(error)), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
