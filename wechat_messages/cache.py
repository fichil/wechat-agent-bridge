"""Bounded plaintext RAM cache; authenticated committed WAL updates only."""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3
import hashlib
import json
import time

from wechat_account_probe.models import ProbeError
from wechat_account_probe.sqlcipher import (PAGE, MAX_DATABASE, stable_snapshot,
    decrypt_snapshot, committed_frames, decrypt_page, logical_page_count)

MAX_CACHE = 384 * 1024 * 1024


def fingerprint(path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


@dataclass
class Entry:
    image: bytearray
    signature: tuple
    generation: bytes
    key: bytes
    salt: bytes


class DatabaseCache:
    def __init__(self, session, maximum=MAX_CACHE):
        self.session, self.maximum = session, maximum
        self.entries = OrderedDict()
        self.stats = dict(full_reads=0, wal_updates=0, hits=0)

    def close(self):
        self.entries.clear()

    def generation(self, relative):
        entry = self.entries[relative]
        return hashlib.sha256(json.dumps([list(entry.signature[0][:2]), entry.salt.hex()]).encode()).hexdigest()

    def trim(self):
        while self.entries and sum(len(e.image) for e in self.entries.values()) > self.maximum:
            self.entries.popitem(last=False)

    def image(self, relative):
        from wechat_account_probe.memory import database_key
        from wechat_account_probe.native import read_shared_file
        deadline = min(self.session.memory.deadline, time.monotonic()+60)

        def check():
            self.session.memory.check()
            if time.monotonic() >= deadline:
                raise ProbeError("message_shard_timeout")

        root = (self.session.directory / "db_storage").resolve()
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ProbeError("message_path_invalid")
        wal_path = path.with_name(path.name + "-wal")
        signature = fingerprint(path), fingerprint(wal_path)
        check()
        old = self.entries.get(relative)
        if old and old.signature == signature:
            self.entries.move_to_end(relative)
            self.stats["hits"] += 1
            return old.image
        if old and old.signature[0] == signature[0]:
            wal = read_shared_file(wal_path, MAX_DATABASE, optional=True, check=check)
            # A reset changes the WAL generation. Rebuild from the base in that case.
            if len(wal) >= 32 and wal[16:24] == old.generation:
                updates, count = committed_frames(wal, check)
                if count is not None:
                    decoded = {}
                    for number, page in updates.items():
                        check()
                        decoded[number] = decrypt_page(old.key, old.salt, page, number)
                    count = logical_page_count(decoded.get(1, old.image[:PAGE]), count)
                    if count * PAGE > MAX_DATABASE or any(
                            n not in decoded for n in range(len(old.image)//PAGE+1, count+1)):
                        raise ProbeError("wal_page_out_of_bounds")
                    # The authenticated committed prefix is already a coherent snapshot.
                    # A later append/checkpoint is observed on the next signature check.
                    old.image[count*PAGE:] = b""
                    if len(old.image) < count*PAGE:
                        old.image.extend(bytes(count*PAGE-len(old.image)))
                    for number, page in decoded.items():
                        if number <= count:
                            old.image[(number-1)*PAGE:number*PAGE] = page
                    old.image[18:20] = b"\x01\x01"
                    old.signature = signature
                    self.entries.move_to_end(relative)
                    self.trim()
                    self.stats["wal_updates"] += 1
                    return old.image
        self.entries.pop(relative, None)
        encrypted, wal = stable_snapshot(path, check)
        key, salt = database_key(self.session.memory, encrypted[:PAGE], check)
        image = decrypt_snapshot(encrypted, wal, key, salt, check)
        # stable_snapshot verified the copied base and WAL prefix before decryption.
        # Do not reject that valid snapshot merely because WeChat writes while keys
        # are being located. Keep the earlier signature, causing a conservative miss.
        current = fingerprint(path)
        if signature[0] is None or current is None or signature[0][:2] != current[:2]:
            raise ProbeError("database_snapshot_changed")
        while self.entries and sum(len(e.image) for e in self.entries.values()) + len(image) > self.maximum:
            self.entries.popitem(last=False)
        self.entries[relative] = Entry(image, signature, wal[16:24], key, salt)
        self.trim()
        self.stats["full_reads"] += 1
        return image

    @contextmanager
    def database(self, relative):
        db = sqlite3.connect(":memory:")
        try:
            db.deserialize(self.image(relative))
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: (self.session.memory.check(), 0)[1], 1000)
            yield db
        except sqlite3.Error:
            self.session.memory.check()
            raise ProbeError("message_query_failed") from None
        finally:
            db.close()
