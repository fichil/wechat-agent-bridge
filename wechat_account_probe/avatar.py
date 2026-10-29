"""Read one account's local head_image BLOB and encode it for JSON.

Reference path/query and Base64 approach:
https://github.com/xiangruiai/xiangrui-toolkit/blob/main/skills/
%E5%86%85%E5%AE%B9%E8%AE%BE%E8%AE%A1/group-daily/scripts/extract_avatars.py
Independently implemented here: in-memory encrypted snapshots, bounded exact-self
queries, strict MIME handling, independent failure states and deadline checks.
"""
import base64
from contextlib import closing
import sqlite3
import time

from .models import ProbeError

MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_TIMEOUT = 15
FINAL_CHECK_RESERVE = 3
AVATAR_SOURCE = 'hmac_verified_current_account_head_image_row'
INSTANCE_ERRORS = {'process_exited', 'process_instance_changed',
                   'process_owner_changed', 'process_time_unreadable'}


def detect_mime(data):
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if len(data) >= 16 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        if data[12:16] in (b'VP8 ', b'VP8L', b'VP8X'):
            return 'image/webp'
    raise ProbeError('avatar_format_unsupported')


def encode_avatar(data):
    if not isinstance(data, bytes):
        raise ProbeError('avatar_storage_type_invalid')
    if not data:
        raise ProbeError('avatar_empty')
    if len(data) > MAX_AVATAR_BYTES:
        raise ProbeError('avatar_size_limit')
    return dict(mime_type=detect_mime(data),
                data_base64=base64.b64encode(data).decode('ascii'))


def read_avatar_row(image, wxid, check=lambda: None):
    """Never fetch an oversized BLOB or any other account's avatar."""
    check()
    try:
        with closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(image)
            db.execute('PRAGMA temp_store=MEMORY')
            db.execute('PRAGMA query_only=ON')

            def authorize(action, table, column, _database, _trigger):
                allowed = (action == sqlite3.SQLITE_SELECT or
                           (action == sqlite3.SQLITE_READ and table == 'head_image' and
                            column in ('username', 'image_buffer')) or
                           (action == sqlite3.SQLITE_FUNCTION and column in ('typeof', 'length')))
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

            db.set_authorizer(authorize)
            db.set_progress_handler(lambda: (check(), 0)[1], 1000)
            rows = db.execute(
                "SELECT typeof(image_buffer), length(image_buffer), "
                "CASE WHEN typeof(image_buffer)='blob' AND length(image_buffer)<=? "
                "THEN image_buffer ELSE NULL END FROM head_image WHERE username=? LIMIT 2",
                (MAX_AVATAR_BYTES, wxid)).fetchall()
    except sqlite3.Error:
        check()  # SQLite wraps an interrupted progress handler in OperationalError.
        raise ProbeError('avatar_query_failed') from None
    check()
    if not rows:
        raise ProbeError('avatar_not_cached')
    if len(rows) != 1:
        raise ProbeError('avatar_record_ambiguous')
    kind, size, data = rows[0]
    if kind == 'null' or size == 0:
        raise ProbeError('avatar_empty')
    if kind != 'blob':
        raise ProbeError('avatar_storage_type_invalid')
    if size > MAX_AVATAR_BYTES:
        raise ProbeError('avatar_size_limit')
    return encode_avatar(data)


def load_avatar(memory, directory, wxid, deadline, *, clock=time.monotonic):
    from .memory import database_key
    from .sqlcipher import stable_snapshot, decrypt_snapshot
    avatar_deadline = min(deadline-FINAL_CHECK_RESERVE, clock()+AVATAR_TIMEOUT)

    def check():
        memory.check()
        if clock() >= avatar_deadline:
            raise ProbeError('avatar_timeout')

    try:
        check()
        path = directory / 'db_storage' / 'head_image' / 'head_image.db'
        database, wal = stable_snapshot(path, check)
        key, salt = database_key(memory, database[:4096], check=check)
        check()
        image = decrypt_snapshot(database, wal, key, salt, check=check)
        avatar = read_avatar_row(image, wxid, check)
        return dict(avatar=avatar, avatar_status='available', avatar_reason='')
    except FileNotFoundError:
        return dict(avatar=None, avatar_status='missing', avatar_reason='avatar_database_missing')
    except ProbeError as exc:
        reason = str(exc)
        if reason in INSTANCE_ERRORS:
            raise
        status = 'missing' if reason in ('avatar_not_cached', 'avatar_empty') else 'unavailable'
        return dict(avatar=None, avatar_status=status, avatar_reason=reason)
    except Exception:
        return dict(avatar=None, avatar_status='unavailable', avatar_reason='avatar_read_failed')
