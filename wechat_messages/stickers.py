"""Message-bound sticker references; original bytes, never UI or re-encoding."""
import base64
import hashlib
import re

from wechat_account_probe.models import ProbeError
from wechat_conversations.reader import IDENTITY_ERRORS
from .content import message_text, xml_root
from .images import verified_image
from .sticker_download import StickerDownload


def sticker_reference(row):
    try:
        root = xml_root(message_text(row, row.get("sender_username")))
        node = root if root is not None and root.tag == "emoji" else root.find(".//emoji") if root is not None else None
        if node is None:
            return {}
        result = {key: node.get(key, "")[:8193] for key in ("md5", "cdnurl", "thumburl", "encrypturl")}
        if not re.fullmatch(r"[0-9a-fA-F]{32}", result["md5"]):
            result["md5"] = ""
        result["md5"] = result["md5"].lower()
        return result
    except (ValueError, TypeError):
        return {}


def database_reference(reader, md5):
    """Only exact MD5 rows in known tables in this already-bound account."""
    result = {}
    try:
        with reader.cache.database("emoticon/emoticon.db") as db:
            for table in ("kNonStoreEmoticonTable", "kStoreEmoticonTable"):
                columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                fields = [c for c in ("cdn_url", "thumb_url", "encrypt_url") if c in columns]
                if "md5" not in columns or not fields:
                    continue
                selected = ",".join(f'CASE WHEN length("{c}")<=8192 THEN "{c}" END AS "{c}"' for c in fields)
                rows = db.execute(f'SELECT {selected} FROM "{table}" WHERE md5=? COLLATE NOCASE LIMIT 2', (md5,)).fetchall()
                if len(rows) != 1:
                    continue
                for column, key in (("cdn_url", "cdnurl"), ("thumb_url", "thumburl"), ("encrypt_url", "encrypturl")):
                    if column in fields and isinstance(rows[0][column], str) and rows[0][column]:
                        result.setdefault(key, rows[0][column])
    except FileNotFoundError:
        pass
    except ProbeError as exc:
        if str(exc) in IDENTITY_ERRORS:
            raise
        reader.session.memory.check()
    return result


def read_sticker(reader, row):
    ref = sticker_reference(row)
    if not ref:
        return dict(status="unavailable", reason="sticker_reference_unavailable")
    if not ref.get("cdnurl") and ref.get("md5"):
        for key, value in database_reference(reader, ref["md5"]).items():
            if not ref.get(key):
                ref[key] = value
    candidates = [(ref.get("cdnurl"), "original"), (ref.get("thumburl"), "thumbnail")]
    reason = "sticker_encrypted_unsupported" if ref.get("encrypturl") else "sticker_reference_unavailable"
    download = StickerDownload(reader.session.memory.check)
    tried = set()
    for url, quality in candidates:
        if not url or url in tried:
            continue
        tried.add(url)
        try:
            data = download.fetch(url)
            mime = verified_image(data, check=download.check, all_frames=True)
            if quality == "original" and ref.get("md5") and hashlib.md5(data).hexdigest() != ref["md5"]:
                raise ProbeError("sticker_digest_mismatch")
            return dict(status="available", reason=None, mime_type=mime,
                        data_base64=base64.b64encode(data).decode("ascii"), quality=quality)
        except ProbeError as exc:
            if str(exc) in IDENTITY_ERRORS:
                raise
            reader.session.memory.check()
            reason = str(exc)
    return dict(status="unavailable", reason=reason)
