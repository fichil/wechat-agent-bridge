"""Offline, verified snapshots. Import never resumes old AI/send work."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from uuid import uuid4

FILES = ('bridge.sqlite3', 'auth.sqlite3')
MAX_BYTES = 10 * 1024**3


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def offline(data):
    """Use the backend's ownership lock, plus a maintenance lock."""
    data = Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for name in ('portable-maintenance.lock', 'bridge.sqlite3.lock'):
            stream = (data / name).open('a+b')
            handles.append(stream)
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('请先停止使用这个数据目录的后端；未修改任何数据。') from None
        yield data
    finally:
        for stream in reversed(handles):
            stream.close()


def validate_db(path, name):
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('数据库完整性检查失败：' + name)
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('数据库关联检查失败：' + name)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'accounts', 'messages', 'conversation_preferences', 'ai_sessions', 'deliveries'} if name == FILES[0] else {'administrator', 'sessions', 'attempts'}
        if not required <= tables:
            raise ValueError('不是有效的 Bridge 数据库：' + name)
        if name == FILES[0] and db.execute('PRAGMA user_version').fetchone()[0] != 8:
            raise ValueError('迁移包仅支持数据库版本 8；请先使用匹配版本升级。')


def snapshot(source, target):
    with closing(sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dest:
            src.backup(dest)


def export_data(data, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('目标文件已存在，请选择新的迁移包文件名。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with offline(data) as data, tempfile.TemporaryDirectory(prefix='bridge-export-', dir=output.parent) as temporary:
        temp = Path(temporary)
        for name in FILES:
            if not (data / name).is_file():
                raise ValueError('源数据不完整，缺少 ' + name)
            snapshot(data / name, temp / name)
            validate_db(temp / name, name)
        manifest = dict(format='wechat-agent-bridge-migration', version=1, database_version=8,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        files={name: dict(sha256=sha(temp/name), size=(temp/name).stat().st_size) for name in FILES})
        archive = temp / 'migration.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipout:
            zipout.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for name in FILES:
                zipout.write(temp / name, name)
        # Exclusive destination creation, including races with another export.
        with archive.open('rb') as source, output.open('xb') as target:
            shutil.copyfileobj(source, target)
    return dict(path=str(output), sha256=sha(output), size=output.stat().st_size)


def quarantine(db):
    """Preserve history/config, invalidate live eligibility and unfinished work."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with db:
        for rid, payload in db.execute("SELECT run_id,payload FROM ai_runs WHERE status IN ('queued','running')").fetchall():
            value = json.loads(payload)
            value.update(status='failed', error='ai_interrupted', message='数据已迁移，旧任务未自动恢复。', completed_at=timestamp)
            db.execute("UPDATE ai_runs SET status='failed',payload=? WHERE run_id=?", (json.dumps(value, ensure_ascii=False), rid))
        db.execute("UPDATE ai_message_state SET status='failed' WHERE status='processing'")
        db.execute('UPDATE ai_sessions SET epoch=epoch+1,auto_pending=0,paused=1')
        db.execute("UPDATE deliveries SET status='cancelled',code='migration_cancelled' WHERE status IN ('queued','failed')")
        db.execute("UPDATE deliveries SET status='unknown',code='send_interrupted' WHERE status='sending'")
        db.execute("UPDATE delivery_attempts SET status='unknown',code='send_interrupted',completed_at=? WHERE status='sending'", (timestamp,))
        for rid, payload in db.execute("SELECT round_id,payload FROM group_rounds WHERE status='active'").fetchall():
            value = json.loads(payload); value['status'] = 'cancelled'
            db.execute("UPDATE group_rounds SET status='cancelled',payload=? WHERE round_id=?", (json.dumps(value, ensure_ascii=False), rid))
        db.execute("UPDATE accounts SET source_state='unknown'")
        db.execute('DELETE FROM cursors')


def import_data(archive, data):
    data = Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    with offline(data), tempfile.TemporaryDirectory(prefix='import-', dir=data) as temporary:
        temp = Path(temporary)
        with zipfile.ZipFile(archive) as zin:
            infos = zin.infolist()
            if len(infos) != 3 or {i.filename for i in infos} != {*FILES, 'manifest.json'}:
                raise ValueError('迁移包文件清单不合法。')
            if sum(i.file_size for i in infos) > MAX_BYTES or zin.getinfo('manifest.json').file_size > 16384:
                raise ValueError('迁移包超过大小限制。')
            manifest = json.loads(zin.read('manifest.json'))
            if manifest.get('format') != 'wechat-agent-bridge-migration' or manifest.get('version') != 1 or manifest.get('database_version') != 8 or set(manifest.get('files', {})) != set(FILES):
                raise ValueError('迁移包格式或版本不支持。')
            for name in FILES:
                with zin.open(name) as source, (temp/name).open('xb') as target:
                    shutil.copyfileobj(source, target)
                expected = manifest['files'][name]
                if (temp/name).stat().st_size != expected['size'] or sha(temp/name) != expected['sha256']:
                    raise ValueError('迁移包校验失败：' + name)
                validate_db(temp/name, name)
        with closing(sqlite3.connect(temp / FILES[0])) as db:
            quarantine(db)
        with closing(sqlite3.connect(temp / FILES[1])) as db:
            with db:
                db.execute('DELETE FROM sessions'); db.execute('DELETE FROM attempts')
                db.execute('UPDATE administrator SET generation=generation+1')
        for name in FILES:
            validate_db(temp/name, name)
        marker = dict(imported_at=datetime.now(timezone.utc).isoformat(), source_sha256=sha(archive),
                      message='请重新填写 API 密钥、验证微信发送后，点击恢复迁移。')
        (temp/'migration-pending.json').write_text(json.dumps(marker, ensure_ascii=False), encoding='utf-8')
        backups = data / 'migration-backups' / ('before-import-' + uuid4().hex)
        backups.mkdir(parents=True)
        replace_names = [*FILES, 'migration-pending.json', 'native-send-acceptance.json',
                         *(name+suffix for name in FILES for suffix in ('-wal', '-shm'))]
        moved, installed = [], []
        try:
            for name in replace_names:
                if (data/name).exists():
                    os.replace(data/name, backups/name); moved.append(name)
            for name in (*FILES, 'migration-pending.json'):
                os.replace(temp/name, data/name); installed.append(name)
        except BaseException:
            for name in reversed(installed):
                (data/name).unlink(missing_ok=True)
            for name in moved:
                os.replace(backups/name, data/name)
            raise
    return dict(backup=str(backups), migration_pending=True)


def resume_data(data, ready_accounts):
    """Caller has verified live identities and local acceptance before stopping."""
    data = Path(data).resolve()
    with offline(data):
        if not (data/'migration-pending.json').exists():
            raise ValueError('没有等待恢复的迁移。')
        with closing(sqlite3.connect(data/FILES[0])) as db:
            db.row_factory = sqlite3.Row
            enabled = db.execute('SELECT account_id,conversation_id FROM conversation_preferences WHERE ai_enabled=1').fetchall()
            if not {r['account_id'] for r in enabled} <= set(ready_accounts):
                raise ValueError('仍有启用 AI 的账号未完成在线检查和本机发送验收。')
            with db:
                quarantine(db)
                for aid, cid in enabled:
                    marks = {r['shard']+'|'+r['table_name']:dict(generation=r['generation'], upper=max(r['last_id'], r['upper_id'] or 0))
                             for r in db.execute('SELECT * FROM sync_state WHERE account_id=? AND source_id=(SELECT source_id FROM conversations WHERE conversation_id=?)',(aid,cid))}
                    db.execute('INSERT OR REPLACE INTO delivery_sessions VALUES(?,?,?,?,0)',
                               (aid,cid,datetime.now(timezone.utc).isoformat(),json.dumps(marks)))
                db.execute('UPDATE ai_sessions SET paused=0,auto_pending=0')
        (data/'migration-pending.json').unlink()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('--data', required=True)
    parser.add_argument('--file', required=True)
    args = parser.parse_args()
    result = export_data(args.data,args.file) if args.action=='export' else import_data(args.file,args.data)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
