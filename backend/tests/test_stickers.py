import base64
import hashlib
import io
import socket
import sqlite3
import time
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from PIL import Image
import zstandard

from wechat_account_probe.models import ProbeError
from wechat_messages.images import MAX_IMAGE, verified_image
from wechat_messages.sticker_download import StickerDownload, cdn_url
from wechat_messages.stickers import database_reference, read_sticker, sticker_reference
from test_messages import MemoryCache, shard_image
from wechat_messages.reader import MessageReader


def picture(fmt="GIF"):
    output = io.BytesIO()
    first = Image.new("RGBA", (12, 12), (255, 0, 0, 255))
    if fmt == "GIF":
        first.save(output, format=fmt, save_all=True,
                   append_images=[Image.new("RGBA", (12, 12), "blue")], duration=[120, 240], loop=0)
    else:
        first.putpixel((0, 0), (0, 0, 0, 0))
        first.save(output, format=fmt)
    return output.getvalue()


def row(data, **attrs):
    fields = dict(md5=hashlib.md5(data).hexdigest(), cdnurl="https://emoji.qpic.cn/original", **attrs)
    return dict(local_type=47, message_content='<msg><emoji ' + ' '.join(f'{k}="{v}"' for k, v in fields.items()) + '/></msg>')


class StickerTests(unittest.TestCase):
    def test_original_png_and_animated_gif_bytes_survive_json_base64(self):
        for fmt in ("PNG", "GIF", "WEBP"):
            data = picture(fmt)
            with patch("wechat_messages.stickers.StickerDownload") as downloader:
                downloader.return_value.fetch.return_value = data
                result = read_sticker(Mock(), row(data))
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["mime_type"], "image/" + fmt.lower())
            decoded = base64.b64decode(result["data_base64"], validate=True)
            self.assertEqual(decoded, data)
            if fmt == "GIF":
                with Image.open(io.BytesIO(decoded)) as image:
                    self.assertEqual(image.n_frames, 2)
                    self.assertEqual(image.info["duration"], 120)
                    image.seek(1)
                    self.assertEqual(image.info["duration"], 240)
            elif fmt == "PNG":
                with Image.open(io.BytesIO(decoded)) as image:
                    self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_compressed_group_xml_and_invalid_reference(self):
        raw = 'sender:\n<msg><emoji md5="' + 'A'*32 + '" cdnurl="https://emoji.qpic.cn/x?a=1&amp;b=2"/></msg>'
        value = sticker_reference(dict(message_content=None, compress_content=zstandard.ZstdCompressor().compress(raw.encode()), sender_username="sender"))
        self.assertEqual(value["md5"], "a"*32)
        self.assertEqual(value["cdnurl"], "https://emoji.qpic.cn/x?a=1&b=2")
        for content in ("broken", '<!DOCTYPE a [<!ENTITY e SYSTEM "file:///secret">]><emoji/>'):
            self.assertEqual(sticker_reference(dict(message_content=content)), {})

    def test_reader_accepts_archived_sticker_ref_and_revalidates_identity(self):
        data = picture()
        reader = MessageReader(Mock(wxid="self", cache=None))
        content = zstandard.ZstdCompressor().compress(('other:\n'+row(data)["message_content"]).encode())
        reader.cache = MemoryCache({"message/message_0.db": shard_image("chat", [
            (1, 9, 47, 1, 2, 1780000000, None, content, None)], [(2, "other")])})
        request = dict(source_id="chat", reference=dict(shard="message/message_0.db", local_id=1, server_id="9"))
        with patch("wechat_messages.stickers.StickerDownload") as downloader:
            downloader.return_value.fetch.return_value = data
            self.assertEqual(reader.image(request, time.monotonic()+5)["status"], "available")
            self.assertEqual(reader.session.bind.call_count, 2)
            request["reference"]["server_id"] = "10"
            with self.assertRaisesRegex(ProbeError, "message_not_found"):
                reader.image(request, time.monotonic()+5)

    def test_database_fallback_is_exact_and_account_local(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE kNonStoreEmoticonTable(md5 TEXT, cdn_url TEXT, thumb_url TEXT)")
        data = picture()
        md5 = hashlib.md5(data).hexdigest()
        db.executemany("INSERT INTO kNonStoreEmoticonTable VALUES(?,?,?)", [(md5, "https://emoji.qpic.cn/right", None), ("b"*32, "https://emoji.qpic.cn/wrong", None)])
        @contextmanager
        def database(path):
            self.assertEqual(path, "emoticon/emoticon.db")
            yield db
        reader = Mock()
        reader.cache.database = database
        self.assertEqual(database_reference(reader, md5)["cdnurl"], "https://emoji.qpic.cn/right")
        with patch("wechat_messages.stickers.StickerDownload") as downloader:
            downloader.return_value.fetch.return_value = data
            result = read_sticker(reader, dict(message_content=f'<emoji md5="{md5}"/>'))
            self.assertEqual(result["status"], "available")
            downloader.return_value.fetch.assert_called_once_with("https://emoji.qpic.cn/right")
        db.close()

    def test_thumbnail_fallback_and_failure_reasons(self):
        data = picture()
        value = row(data, thumburl="https://emoji.qpic.cn/thumb")
        with patch("wechat_messages.stickers.StickerDownload") as downloader:
            downloader.return_value.fetch.side_effect = [ProbeError("sticker_download_failed"), picture("PNG")]
            self.assertEqual(read_sticker(Mock(), value)["quality"], "thumbnail")
            downloader.return_value.fetch.side_effect = None
            downloader.return_value.fetch.return_value = picture("PNG")
            self.assertEqual(read_sticker(Mock(), row(data))["reason"], "sticker_digest_mismatch")
            downloader.return_value.fetch.return_value = b"not an image"
            self.assertEqual(read_sticker(Mock(), row(data))["reason"], "image_invalid")
        self.assertEqual(read_sticker(Mock(), dict(message_content='<emoji encrypturl="https://emoji.qpic.cn/encrypted"/>'))["reason"], "sticker_encrypted_unsupported")
        self.assertEqual(read_sticker(Mock(), dict(message_content=""))["reason"], "sticker_reference_unavailable")

    def test_animation_limit_and_truncated_later_frame(self):
        data = picture()
        with self.assertRaisesRegex(ProbeError, "image_invalid"):
            verified_image(data[:-10], all_frames=True)
        output = io.BytesIO()
        frames = [Image.new("RGB", (1, 1), "red" if i % 2 else "blue") for i in range(501)]
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=10, loop=0)
        with self.assertRaisesRegex(ProbeError, "image_animation_limit"):
            verified_image(output.getvalue(), all_frames=True)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.download = StickerDownload(lambda: None)

    def response(self, data=b"content", status=200, **headers):
        response = Mock(status=status)
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        response.read1.side_effect = [data, b""]
        conn = Mock()
        conn.getresponse.return_value = response
        return conn

    def test_disallowed_urls_and_private_dns_never_connect(self):
        self.assertEqual(cdn_url("http://vweixinf.tc.qq.com/110/20401/stodownload?m=x")[1], "vweixinf.tc.qq.com")
        for url in ("file:///x", "http://127.0.0.1/x", "https://qpic.cn.evil.test/x", "https://vweixinf.tc.qq.com.evil.test/x", "https://other.tc.qq.com/x", "https://user@emoji.qpic.cn/x", "https://emoji.qpic.cn:444/x", "https://emoji.qpic.cn/x\r\nHost: x"):
            with self.assertRaisesRegex(ProbeError, "sticker_url_not_allowed"):
                cdn_url(url)
        for ip in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "198.18.0.1", "::1", "::ffff:127.0.0.1"):
            values = [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
            with patch("socket.getaddrinfo", return_value=values), patch("socket.socket") as connect:
                with self.assertRaisesRegex(ProbeError, "sticker_url_not_allowed"):
                    self.download.connection("https", "emoji.qpic.cn", 443)
                connect.assert_not_called()

    def test_pinned_public_socket_preserves_tls_hostname(self):
        values = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
        with patch.object(self.download, "addresses", return_value=values), patch("socket.socket") as make_socket, patch("ssl.create_default_context") as tls:
            connection = self.download.connection("https", "emoji.qpic.cn", 443)
            make_socket.return_value.connect.assert_called_once_with(("1.1.1.1", 443))
            tls.return_value.wrap_socket.assert_called_once_with(make_socket.return_value, server_hostname="emoji.qpic.cn")
            self.assertEqual(connection.host, "emoji.qpic.cn")

    def test_redirects_validate_every_target_and_have_limit(self):
        first = self.response(status=302, Location="https://localhost/x")
        with patch.object(self.download, "connection", return_value=first) as connect:
            with self.assertRaisesRegex(ProbeError, "sticker_url_not_allowed"):
                self.download.fetch("https://emoji.qpic.cn/a")
            self.assertEqual(connect.call_count, 1)
            first.close.assert_called_once()
        redirect = self.response(status=302, Location="/next")
        with patch.object(self.download, "connection", return_value=redirect) as connect:
            with self.assertRaisesRegex(ProbeError, "sticker_redirect_limit"):
                self.download.fetch("https://emoji.qpic.cn/a")
            self.assertEqual(connect.call_count, 3)

    def test_stream_size_content_length_timeout_and_cumulative_budget(self):
        for conn, reason in ((self.response(**{"Content-Length": str(MAX_IMAGE+1)}), "image_size_limit"),
                             (self.response(data=b"x", **{"Content-Length": "2"}), "image_invalid"),
                             (self.response(data=b"x"*(MAX_IMAGE+1)), "image_size_limit"),
                             (self.response(**{"Content-Encoding": "gzip"}), "image_invalid")):
            with patch.object(self.download, "connection", return_value=conn):
                with self.assertRaisesRegex(ProbeError, reason):
                    self.download.fetch("https://emoji.qpic.cn/a")
        with patch.object(self.download, "connection", side_effect=TimeoutError):
            with self.assertRaisesRegex(ProbeError, "sticker_download_timeout"):
                self.download.fetch("https://emoji.qpic.cn/a")
        self.download.deadline = time.monotonic()-1
        with self.assertRaisesRegex(ProbeError, "sticker_download_timeout"):
            self.download.fetch("https://emoji.qpic.cn/a")

    def test_raw_bytes_and_stream_rate(self):
        data = b"a"*CHUNK_SIZE
        conn = self.response(data)
        before = time.monotonic()
        with patch.object(self.download, "connection", return_value=conn):
            self.assertEqual(self.download.fetch("https://emoji.qpic.cn/a"), data)
        self.assertGreaterEqual(time.monotonic()-before, CHUNK_SIZE/1048576-.01)
        self.assertTrue(conn.getresponse.return_value.close.called)


CHUNK_SIZE = 64 * 1024
