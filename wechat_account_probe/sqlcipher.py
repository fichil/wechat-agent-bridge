"""In-memory SQLCipher 4 snapshots; no plaintext/key cache or message queries.

AES/page-HMAC layout adapted from wechatauto-replica (Apache-2.0).
See third_party/wechatauto-replica/NOTICE.md. WAL commit/checksum validation
and memory-only querying are local additions, not upstream guarantees.
"""
import hashlib
import hmac
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

from .models import ProbeError

PAGE = 4096
RESERVE = 80
MAX_DATABASE = 256 * 1024 * 1024


def mac_key(key, salt):
    return hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in salt), 2, dklen=32)


def verify_page(key, salt, page, number):
    if len(key) != 32 or len(salt) != 16 or len(page) != PAGE or number < 1:
        return False
    start = 16 if number == 1 else 0
    value = hmac.new(mac_key(key, salt), page[start:-64] + struct.pack('<I', number),
                     hashlib.sha512).digest()
    return hmac.compare_digest(value, page[-64:])


def decrypt_page(key, salt, page, number):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if not verify_page(key, salt, page, number):
        raise ProbeError('database_page_hmac_failed')
    start = 16 if number == 1 else 0
    decryptor = Cipher(algorithms.AES(key), modes.CBC(page[-80:-64])).decryptor()
    plain = decryptor.update(page[start:-80]) + decryptor.finalize()
    return (b'SQLite format 3\0' if number == 1 else b'') + plain + bytes(RESERVE)


def wal_checksum(data, endian, state=(0, 0)):
    if len(data) % 8:
        raise ProbeError('wal_checksum_alignment')
    s0, s1 = state
    for a, b in struct.iter_unpack(endian + 'II', data):
        s0 = (s0 + a + s1) & 0xffffffff
        s1 = (s1 + b + s0) & 0xffffffff
    return s0, s1


def committed_frames(wal, check=lambda: None):
    """Return latest committed pages. Uncommitted/stale-generation tails ignored."""
    if not wal:
        return {}, None
    if len(wal) < 32:
        raise ProbeError('wal_header_incomplete')
    magic, version, size = struct.unpack('>III', wal[:12])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or size != PAGE:
        raise ProbeError('wal_format_unsupported')
    endian = '<' if magic == 0x377f0682 else '>'
    state = wal_checksum(wal[:24], endian)
    if state != struct.unpack('>II', wal[24:32]):
        raise ProbeError('wal_header_checksum_failed')
    committed, pending, db_size = {}, {}, None
    for offset in range(32, len(wal) - (24 + PAGE) + 1, 24 + PAGE):
        check()
        header, page = wal[offset:offset+24], wal[offset+24:offset+24+PAGE]
        if header[8:16] != wal[16:24]:
            break  # checkpoint/reset left old-generation bytes in the tail
        state = wal_checksum(header[:8] + page, endian, state)
        if state != struct.unpack('>II', header[16:24]):
            raise ProbeError('wal_frame_checksum_failed')
        pgno, commit = struct.unpack('>II', header[:8])
        if pgno == 0 or pgno > MAX_DATABASE // PAGE or commit > MAX_DATABASE // PAGE:
            raise ProbeError('wal_page_out_of_bounds')
        pending[pgno] = page
        if commit:
            committed.update(pending)
            committed = {n: p for n, p in committed.items() if n <= commit}
            pending.clear()
            db_size = commit
    return committed, db_size


def decrypt_snapshot(database, wal, key, salt=None, check=lambda: None):
    check()
    if not database or len(database) % PAGE or len(database) > MAX_DATABASE:
        raise ProbeError('database_size_invalid')
    salt = salt or database[:16]
    if not verify_page(key, salt, database[:PAGE], 1):
        raise ProbeError('database_key_not_verified')
    updates, committed_size = committed_frames(wal, check)
    # Use only the latest version of each page, validating every page HMAC.
    count = committed_size or len(database) // PAGE
    output = bytearray()
    for pgno in range(1, count + 1):
        check()
        page = updates.get(pgno, database[(pgno-1)*PAGE:pgno*PAGE])
        output.extend(decrypt_page(key, salt, page, pgno))
    if len(output) < PAGE or output[16:18] != b'\x10\x00' or output[20] != RESERVE:
        raise ProbeError('database_header_invalid')
    output[18:20] = b'\x01\x01'  # standalone memory image: WAL already applied
    return output


def read_own_row(image, wxid):
    """The sole data query is one exact self-contact lookup; no contact export."""
    try:
        with closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(image)
            db.execute('PRAGMA temp_store=MEMORY')
            db.execute('PRAGMA query_only=ON')

            def authorize(action, arg1, arg2, _database, _trigger):
                allowed = (action == sqlite3.SQLITE_SELECT or
                           (action == sqlite3.SQLITE_READ and arg1 == 'contact' and
                            arg2 in ('username', 'alias', 'nick_name')))
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

            db.set_authorizer(authorize)
            rows = db.execute('SELECT username, alias, nick_name FROM contact '
                              'WHERE username=? LIMIT 2', (wxid,)).fetchall()
    except sqlite3.Error:
        raise ProbeError('self_contact_query_failed') from None
    if len(rows) != 1 or rows[0][0] != wxid:
        raise ProbeError('self_contact_missing_or_ambiguous')
    username, alias, nickname = rows[0]
    if any(value is not None and (not isinstance(value, str) or len(value) > 256 or
                                  '\0' in value) for value in rows[0]):
        raise ProbeError('self_contact_fields_invalid')
    return dict(wxid=username, wechat_id=alias or None, nickname=nickname or None)


def stable_snapshot(path, check=lambda: None, reader=None):
    """Re-read the captured prefix: append is safe; reset/checkpoint is retried.

    All handles are opened read-only with READ/WRITE/DELETE sharing by native.py.
    No SQLite connection is opened against the live encrypted files.
    """
    if reader is None:
        from .native import read_shared_file
        def reader(*args, **kwargs):
            return read_shared_file(*args, **kwargs, check=check)
    path = Path(path)
    wal_path = Path(str(path) + '-wal')
    for _attempt in range(3):
        check()
        try:
            db = reader(path, MAX_DATABASE)
            wal = reader(wal_path, MAX_DATABASE, optional=True)
            check()
            if (reader(path, MAX_DATABASE) == db and
                    reader(wal_path, MAX_DATABASE, prefix=len(wal), optional=True) == wal):
                return db, wal
        except FileNotFoundError:
            pass
    raise ProbeError('database_snapshot_changed')
