import io
import base64
import hashlib
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import zstandard
from PIL import Image
from wechat_account_probe.models import ProbeError
from wechat_messages.content import decode_text, parse_content, protobuf, packed_transcript, MAX_CONTENT
from wechat_messages.reader import MessageReader, row_key
from wechat_messages.images import decrypt_image, verified_image, image_names, read_image, MAX_IMAGE


def shard_image(source, rows, names):
    db = sqlite3.connect(":memory:")
    table = MessageReader.table(source)
    db.execute('CREATE TABLE Name2Id(user_name TEXT,is_session INTEGER)')
    db.executemany('INSERT INTO Name2Id(rowid,user_name) VALUES (?,?)', names)
    db.execute(f'CREATE TABLE "{table}"(local_id INTEGER,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content,compress_content,packed_info_data)')
    db.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?,?,?,?,?,?)', rows)
    image = db.serialize(); db.close()
    return image


class MemoryCache:
    def __init__(self, images):
        self.images = images
    @contextmanager
    def database(self, name):
        db = sqlite3.connect(":memory:")
        try:
            if isinstance(self.images[name], Exception):
                raise self.images[name]
            db.deserialize(self.images[name]); db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()


class MessageReaderTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(wxid="self")
        self.reader = MessageReader(self.session)
        self.source = "synthetic@chatroom"
        db = sqlite3.connect(":memory:")
        db.execute('CREATE TABLE contact(username TEXT,alias TEXT,nick_name TEXT,remark TEXT)')
        db.executemany('INSERT INTO contact VALUES (?,?,?,?)', [
            ("self", "my_alias", "同名", "不能当昵称"), ("other", None, "同名", "备注")])
        images = {"contact/contact.db": db.serialize()}; db.close()
        images["message/message_0.db"] = shard_image(self.source, [
            (1, 9007199254740993, 1, 10, 2, 1780000000, "other:\n第一条", None, None),
            (2, 4, 1, 10, 1, 1780000000, "自己", None, None)], [(1, "self"), (2, "other")])
        images["message/message_1.db"] = shard_image(self.source, [
            (1, 5, 1, 10, 4, 1780000000, "另一分片自己", None, None),
            (2, 6, 34, 11, 99, 1780000001, '<msg><voicemsg voicelength="1200"/></msg>', None, None)], [(4, "self"), (2, "other")])
        self.images = images
        self.reader.cache = MemoryCache(images)
        self.reader.shards = lambda: ["message/message_0.db", "message/message_1.db"]

    def read(self, **kwargs):
        return self.reader.read(dict(source_id=self.source, page_size=50, **kwargs), time.monotonic()+10)

    def test_shard_local_sender_own_ids_nicknames_and_unknown(self):
        result = self.read()
        self.assertEqual([r["is_self"] for r in result["items"]], [False, True, True, None])
        self.assertEqual(result["items"][0]["content"]["text"], "第一条")
        self.assertEqual(result["items"][1]["sender"]["nickname"], "同名")
        self.assertEqual(result["items"][1]["sender"]["remark"], "不能当昵称")
        self.assertEqual(result["items"][0]["_ref"]["server_id"], "9007199254740993")
        self.assertIsNone(result["items"][-1]["sender"]["wxid"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.session.bind.call_count, 2)

    def test_keyset_ties_shards_older_and_newer_without_loss(self):
        request = dict(source_id=self.source, page_size=2)
        latest = self.reader.read(request, 100)
        anchor = latest["items"][0]["_key"]
        older = self.reader.read(dict(request, direction="older", boundary=anchor, upper=latest["items"][-1]["_key"]), 100)
        combined = older["items"]+latest["items"]
        self.assertEqual(len({tuple(r["_key"]) for r in combined}), 4)
        self.assertFalse(older["has_more"])
        newer = self.reader.read(dict(request, direction="newer", boundary=older["items"][-1]["_key"]), 100)
        self.assertEqual([r["_key"] for r in newer["items"]], [r["_key"] for r in latest["items"]])

    def test_failed_shard_is_partial_not_empty_and_identity_failure_aborts(self):
        self.images["message/message_1.db"] = ProbeError("database_page_hmac_failed")
        self.assertIn("database_page_hmac_failed", self.read()["issues"])
        self.images["message/message_1.db"] = ProbeError("account_changed_during_read")
        with self.assertRaisesRegex(ProbeError, "account_changed"):
            self.read()

    def test_new_inserts_with_lower_sort_key_and_inserts_during_paging(self):
        latest = self.read()
        original = [(1, 1, 1, 10, 2, 1780000000, "old", None, None)]
        def insert_through(last):
            self.images["message/message_0.db"] = shard_image(self.source, original + [
                (i, i, 1, 10, 2, 1780000000, "new", None, None) for i in range(2, last+1)], [(2, "other")])
        insert_through(4)
        request = dict(source_id=self.source, page_size=1, direction="newer", watermarks=latest["watermarks"])
        first = self.reader.read(request, 100)
        self.assertEqual(first["items"][0]["_ref"]["local_id"], 3)
        self.assertTrue(first["has_more"])
        insert_through(5)
        second = self.reader.read(dict(request, batch_watermarks=first["watermarks"], boundary=first["items"][-1]["_key"]), 100)
        self.assertEqual(second["items"][0]["_ref"]["local_id"], 4)
        self.assertFalse(second["has_more"])
        third = self.reader.read(dict(request, watermarks=second["watermarks"]), 100)
        self.assertEqual(third["items"][0]["_ref"]["local_id"], 5)
        self.assertLess(third["items"][0]["sort_key"], latest["items"][-1]["sort_key"])

    def test_byte_limited_pages_keep_pagination_continuous(self):
        keys, boundary = [], None
        with patch("wechat_messages.reader.MAX_PAGE_BYTES", 600):
            for _ in range(6):
                result = self.read(direction="older", boundary=boundary)
                keys += [tuple(item["_key"]) for item in result["items"]]
                if not result["has_more"]:
                    break
                boundary = result["items"][0]["_key"]
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)


class ContentTests(unittest.TestCase):
    def test_zstd_unicode_newlines_and_bounded_decompression(self):
        content = "你好\n😀"
        self.assertEqual(decode_text(zstandard.ZstdCompressor().compress(content.encode())), content)
        with self.assertRaises(ValueError):
            decode_text(zstandard.ZstdCompressor().compress(b"a"*(MAX_CONTENT+1)))

    def test_voice_xml_and_packed_existing_text_missing_invalid(self):
        packed = b"\x2a\x07\x12\x05hello"
        self.assertEqual(packed_transcript(packed), "hello")
        for content, expected, source in [('<msg><voicetrans transtext="XML文字"/><voicemsg voicelength="2500"/></msg>', "XML文字", "wechat_xml"), ("", "hello", "wechat_packed_info")]:
            kind, result, _ = parse_content(dict(local_type=34, message_content=content, packed_info_data=packed))
            self.assertEqual((kind, result["transcript"], result["transcript_source"]), ("voice", expected, source))
        self.assertIsNone(packed_transcript(b"\x2a\xff"))
        self.assertEqual(parse_content(dict(local_type=34))[1]["transcript_status"], "not_available")

    def test_file_metadata_and_markup_are_not_executed(self):
        xml = '<msg><appmsg><title>测试.pdf</title><type>6</type><appattach><fileext>pdf</fileext></appattach></appmsg></msg>'
        result = parse_content(dict(local_type=49, message_content=xml))[1]
        self.assertEqual((result["filename"], result["format"]), ("测试.pdf", "pdf"))
        text = '<script>alert(1)</script>'
        self.assertEqual(parse_content(dict(local_type=1, message_content=text))[1]["text"], text)
        with self.assertRaises(ValueError): protobuf(b"\x00")

    def test_image_v2_cipher_and_mime_corruption_limit(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        output = io.BytesIO(); Image.new("RGB", (2, 2), "green").save(output, format="PNG")
        plain = output.getvalue(); key = b"0123456789abcdef"
        pad = 16-len(plain)%16
        enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        import struct
        encrypted = b"\x07\x08\x56\x32\x08\x07"+struct.pack("<II", len(plain), 0)+b"\0"+enc.update(plain+bytes([pad])*pad)+enc.finalize()
        result = decrypt_image(encrypted, lambda: (key, 123))
        self.assertEqual(result, plain)
        self.assertEqual(verified_image(result), "image/png")
        with self.assertRaises(ProbeError): verified_image(plain[:20])
        with self.assertRaisesRegex(ProbeError, "image_size_limit"): verified_image(bytes(MAX_IMAGE+1))
        name = b"a"*32
        self.assertEqual(image_names(dict(packed_info_data=b"\x1a\x20"+name)), ["a"*32])

    def test_runtime_has_no_ui_injection_or_network_fallbacks(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in (Path(__file__).resolve().parents[2]/"wechat_messages").glob("*.py"))
        for forbidden in ("SetForegroundWindow", "SetCursorPos", "SendInput", "WriteProcessMemory", "CreateRemoteThread", "SetClipboardData", "pyautogui", "uiautomation", "frida", "requests.", "httpx."):
            self.assertNotIn(forbidden, source)

    def test_local_images_prefer_original_then_fallback_with_explicit_failures(self):
        output = io.BytesIO(); Image.new("RGB", (2, 2), "green").save(output, format="PNG")
        data = output.getvalue()
        with tempfile.TemporaryDirectory() as temp:
            session = Mock(directory=Path(temp))
            reader = Mock(session=session)
            folder = Path(temp)/"msg"/"attach"/hashlib.md5(b"chat").hexdigest()/"2026-10"/"Img"
            folder.mkdir(parents=True)
            packed, xml = "a"*32, "b"*32
            row = dict(packed_info_data=b"\x1a\x20"+packed.encode(), message_content=f'<msg><img md5="{xml}"/></msg>')
            thumbnail, original = folder/(packed+"_t.dat"), folder/(xml+"_h.dat")
            thumbnail.write_bytes(data); original.write_bytes(data)
            result = read_image(reader, "chat", row)
            self.assertEqual((result["quality"], result["mime_type"]), ("original", "image/png"))
            self.assertEqual(base64.b64decode(result["data_base64"], validate=True), data)
            original.write_bytes(b"broken")
            self.assertEqual(read_image(reader, "chat", row)["quality"], "thumbnail")
            thumbnail.unlink()
            self.assertEqual(read_image(reader, "chat", row)["reason"], "image_format_unsupported")
            original.write_bytes(bytes(MAX_IMAGE+1025))
            self.assertEqual(read_image(reader, "chat", row)["reason"], "image_size_limit")
            original.unlink()
            self.assertEqual(read_image(reader, "chat", row)["reason"], "image_not_downloaded")


if __name__ == "__main__":
    unittest.main()
