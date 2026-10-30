from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tests_account_probe.fixtures import KEY, SALT, WXID, encrypted_database, plain_database, wal
from wechat_account_probe.sqlcipher import read_own_row
from wechat_account_probe.models import ProbeError
from wechat_messages.cache import DatabaseCache


class CacheTests(unittest.TestCase):
    def test_append_during_key_lookup_keeps_verified_snapshot_and_catches_up(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/"db_storage/test.db"
            path.parent.mkdir(); path.write_bytes(encrypted_database())
            wal_path=Path(str(path)+"-wal")
            first=plain_database(alias="first")[4096:]
            second=plain_database(alias="second")[4096:]
            wal_path.write_bytes(wal([(2,2,first)]))
            session=Mock(directory=root);session.memory.deadline=time.monotonic()+60
            cache=DatabaseCache(session)
            def key(*_args):
                wal_path.write_bytes(wal([(2,2,first),(2,2,second)]))
                return KEY,SALT
            with patch("wechat_account_probe.memory.database_key",side_effect=key):
                self.assertEqual(read_own_row(cache.image("test.db"),WXID)["wechat_id"],"first")
                self.assertEqual(read_own_row(cache.image("test.db"),WXID)["wechat_id"],"second")

    def test_cache_capacity_includes_committed_wal_growth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"db_storage").mkdir()
            for name in ("a.db", "b.db"):
                (root/"db_storage"/name).write_bytes(encrypted_database())
            wal_path = root/"db_storage/a.db-wal"
            page = plain_database(alias="one")[4096:]
            wal_path.write_bytes(wal([(2, 2, page)]))
            session = Mock(directory=root); session.memory.deadline = time.monotonic()+60
            cache = DatabaseCache(session, maximum=4*4096)
            with patch("wechat_account_probe.memory.database_key", return_value=(KEY, SALT)):
                cache.image("a.db"); cache.image("b.db")
                header = bytearray(plain_database()[:4096])
                struct.pack_into('>I', header, 28, 3)
                wal_path.write_bytes(wal([(2, 2, page), (1, 0, header), (3, 3, bytes(4096))]))
                self.assertEqual(len(cache.image("a.db")), 3*4096)
                self.assertEqual(list(cache.entries), ["a.db"])
                self.assertLessEqual(sum(len(e.image) for e in cache.entries.values()), cache.maximum)

    def test_reuse_committed_wal_checkpoint_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "db_storage" / "test.db"
            path.parent.mkdir(); path.write_bytes(encrypted_database())
            wal_path = Path(str(path)+"-wal")
            first = plain_database(alias="one")[4096:]
            second = plain_database(alias="two")[4096:]
            third = plain_database(alias="uncommitted")[4096:]
            wal_path.write_bytes(wal([(2, 2, first)]))
            session = Mock(directory=root)
            session.memory.deadline = time.monotonic()+60
            cache = DatabaseCache(session)
            with patch("wechat_account_probe.memory.database_key", return_value=(KEY, SALT)):
                initial = cache.image("test.db")
                self.assertEqual(read_own_row(initial, WXID)["wechat_id"], "one")
                self.assertIs(initial, cache.image("test.db"))
                wal_path.write_bytes(wal([(2, 2, first), (2, 2, second), (2, 0, third)]))
                updated = cache.image("test.db")
                self.assertEqual(read_own_row(updated, WXID)["wechat_id"], "two")
                self.assertEqual(cache.stats, dict(full_reads=1, wal_updates=1, hits=1))
                previous = bytes(updated)
                broken = bytearray(wal([(2, 2, second)])); broken[-1] ^= 1
                wal_path.write_bytes(broken)
                with self.assertRaises(ProbeError): cache.image("test.db")
                self.assertEqual(bytes(updated), previous)
                path.write_bytes(encrypted_database(alias="checkpoint")); wal_path.write_bytes(b"")
                self.assertEqual(read_own_row(cache.image("test.db"), WXID)["wechat_id"], "checkpoint")
                self.assertEqual(cache.stats["full_reads"], 2)
                cache.close(); self.assertFalse(cache.entries)
