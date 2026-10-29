"""Bounded WCDB/text/protobuf decoders. Never evaluate message markup."""
import io
import re
import xml.etree.ElementTree as ET

MAX_CONTENT = 1024 * 1024


def decode_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_CONTENT:
            raise ValueError("message_content_limit")
        return value
    if not isinstance(value, bytes) or len(value) > MAX_CONTENT:
        raise ValueError("message_content_limit")
    if value.startswith(b"\x28\xb5\x2f\xfd"):
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(value)) as stream:
            value = stream.read(MAX_CONTENT + 1)
        if len(value) > MAX_CONTENT:
            raise ValueError("message_content_limit")
    return value.decode("utf-8", errors="strict").rstrip("\0")


def protobuf(data):
    """Read just protobuf wire fields, with strict bounds and no schema guessing."""
    if not isinstance(data, bytes) or len(data) > MAX_CONTENT:
        return {}
    pos, fields = 0, {}

    def integer():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError("packed_info_invalid")
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("packed_info_invalid")

    while pos < len(data):
        tag = integer()
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError("packed_info_invalid")
        if wire == 0:
            value = integer()
        elif wire in (1, 2, 5):
            size = integer() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(data) - pos:
                raise ValueError("packed_info_invalid")
            value = data[pos:pos+size]
            pos += size
        else:
            raise ValueError("packed_info_invalid")
        fields.setdefault(field, []).append(value)
    return fields


def xml_root(text):
    # The group sender prefix is removed only by the identity-aware reader.
    if not text.lstrip().startswith("<") or re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def packed_transcript(data):
    try:
        parent = protobuf(data).get(5, [])
        if len(parent) != 1:
            return None
        values = protobuf(parent[0]).get(2, [])
        if len(values) == 1 and isinstance(values[0], bytes):
            return decode_text(values[0]) or None
    except (ValueError, TypeError):
        pass
    return None


def parse_content(row, sender=None):
    issues = []
    try:
        raw = row.get("message_content")
        text = decode_text(raw if raw not in (None, b"", "") else row.get("compress_content"))
    except Exception:
        text = ""
        issues.append("message_content_unavailable")
    if sender and text.startswith(sender + ":\n"):
        text = text[len(sender)+2:]
    code = row["local_type"] & 0xffffffff
    kind = {1: "text", 3: "image", 34: "voice", 43: "video", 47: "sticker",
            49: "attachment", 10000: "system", 10002: "system", 11000: "sticker"}.get(code, "unsupported")
    result = dict(text=None, filename=None, format=None, description=None,
                  transcript=None, transcript_source=None, transcript_status=None,
                  duration_ms=None, image_status=None)
    root = xml_root(text)
    if kind == "text":
        result["text"] = text
    elif kind == "system":
        result["text"] = "".join(root.itertext()).strip() if root is not None else text
    elif kind == "image":
        result.update(image_status="not_loaded", description="图片")
    elif kind == "voice":
        transcript, source = None, None
        if root is not None:
            node = root.find(".//voicetrans")
            transcript = node.get("transtext") if node is not None else None
            if transcript:
                source = "wechat_xml"
            voice = root.find(".//voicemsg")
            try:
                duration = int(voice.get("voicelength", "0")) if voice is not None else 0
                result["duration_ms"] = duration if 0 < duration <= 3600000 else None
            except ValueError:
                pass
        if not transcript:
            transcript = packed_transcript(row.get("packed_info_data"))
            if transcript:
                source = "wechat_packed_info"
        result.update(transcript=transcript, transcript_source=source,
                      transcript_status="available" if transcript else "not_available",
                      description="语音")
    else:
        descriptions = {"video": "视频", "sticker": "表情", "attachment": "文件或卡片",
                        "unsupported": "暂不支持的消息类型"}
        result["description"] = descriptions[kind]
        if root is not None:
            title = root.findtext(".//appmsg/title")
            extension = root.findtext(".//appattach/fileext")
            subtype = root.findtext(".//appmsg/type")
            if subtype == "6":
                result.update(filename=(title or "")[:512] or None,
                              format=(extension or "")[:32] or None, description="文件")
            elif title:
                result.update(text=title[:4096], description="链接或卡片")
    return kind, result, issues
