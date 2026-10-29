"""Read only existing account-local image files. No downloads or UI fallback."""
import base64
import hashlib
import io
import os
import re
import struct
import warnings

from wechat_account_probe.models import ProbeError
from .content import decode_text, protobuf, xml_root

MAX_IMAGE = 8 * 1024 * 1024
V2 = b"\x07\x08\x56\x32\x08\x07"
V1 = b"\x07\x08\x05\x56\x02\x05"


def image_keys(session):
    from wechat_account_probe.memory import pointer, remote_string
    candidates = set()
    for address in getattr(session.memory, "configuration_anchors", ()):
        owner = pointer(session.memory, address + 16 - 0x138)
        cfg = pointer(session.memory, owner + 0x68) if owner else 0
        if cfg and remote_string(session.memory, cfg + 0x48) == session.wxid:
            value = session.memory.read(cfg + 0x40, 4)
            if len(value) == 4:
                number = struct.unpack("<I", value)[0]
                candidates.add((hashlib.md5((str(number)+session.wxid).encode()).hexdigest()[:16].encode(), number & 255))
    if len(candidates) != 1:
        raise ProbeError("image_key_unavailable")
    return candidates.pop()


def decrypt_image(data, keys):
    if data.startswith(V2):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if len(data) < 15:
            raise ProbeError("image_invalid")
        aes_size, xor_size = struct.unpack_from("<II", data, 6)
        block = aes_size + 16 - aes_size % 16
        if 15 + block + xor_size > len(data):
            raise ProbeError("image_invalid")
        key, xor = keys()
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = dec.update(data[15:15+block]) + dec.finalize()
        pad = plain[-1]
        if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad])*pad:
            raise ProbeError("image_decryption_failed")
        tail = len(data)-xor_size
        return plain[:-pad] + data[15+block:tail] + bytes(v ^ xor for v in data[tail:])
    if data.startswith(V1):
        return bytes(v ^ keys()[1] for v in data[22:])
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")):
        return data
    for magic in (b"\xff\xd8\xff", b"\x89PNG", b"GIF8"):
        if data:
            key = data[0] ^ magic[0]
            if bytes(v ^ key for v in data[:len(magic)]) == magic:
                return bytes(v ^ key for v in data)
    raise ProbeError("image_format_unsupported")


def verified_image(data, *, check=lambda: None, all_frames=False):
    from PIL import Image
    if len(data) > MAX_IMAGE:
        raise ProbeError("image_size_limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                mime = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}.get(img.format)
                if not mime:
                    raise ProbeError("image_format_unsupported")
                if img.width * img.height > 40000000:
                    raise ProbeError("image_dimensions_limit")
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                pixels = 0
                for frame in range(501 if all_frames else 1):
                    check()
                    try:
                        img.seek(frame)
                    except EOFError:
                        break
                    pixels += img.width * img.height
                    if frame == 500 or pixels > 100000000:
                        raise ProbeError("image_animation_limit")
                    img.load()
                check()
        return mime
    except ProbeError:
        raise
    except Exception:
        raise ProbeError("image_invalid") from None


def image_names(row):
    # Prefer explicit packed filenames over XML hashes (which can name a different tier).
    names = []
    def visit(data, depth=0):
        if depth > 4:
            return
        try:
            for values in protobuf(data).values():
                for value in values:
                    if isinstance(value, bytes):
                        match = re.fullmatch(rb'"?([0-9a-fA-F]{32})(?:_[ht])?(?:\.dat)?"?', value)
                        if match:
                            names.append(match[1].decode().lower())
                        else:
                            visit(value, depth+1)
        except ValueError:
            pass
    visit(row.get("packed_info_data"))
    try:
        root = xml_root(decode_text(row.get("message_content")))
        if root is not None:
            for node in root.iter("img"):
                value = node.get("md5", "")
                if re.fullmatch(r"[0-9a-fA-F]{32}", value):
                    names.append(value.lower())
    except ValueError:
        pass
    return list(dict.fromkeys(names))


def read_image(reader, source, row):
    from wechat_account_probe.native import read_shared_file
    session, names = reader.session, image_names(row)
    base = session.directory / "msg" / "attach" / hashlib.md5(source.encode()).hexdigest()
    account_root = session.directory.resolve()
    if not base.resolve().is_relative_to(account_root):
        raise ProbeError("message_path_invalid")
    if not names:
        return dict(status="unavailable", reason="image_reference_unavailable")
    wanted = {name+suffix: (index, quality) for index, name in enumerate(names)
              for suffix, quality in (("_h.dat", "original"), (".dat", "local"), ("_t.dat", "thumbnail"))}
    files, visited = [], 0
    for root, dirs, entries in os.walk(base, followlinks=False):
        session.memory.check()
        dirs[:] = [d for d in dirs if (base.__class__(root)/d).resolve().is_relative_to(account_root)
                   and not (base.__class__(root)/d).is_symlink()]
        visited += len(entries)
        if visited > 100000:
            return dict(status="unavailable", reason="image_lookup_limit")
        for filename in entries:
            if filename in wanted:
                path = base.__class__(root) / filename
                if path.resolve().is_relative_to(account_root):
                    files.append((*wanted[filename], path))
    reason = "image_not_downloaded"
    files.sort(key=lambda hit: ({"original": 0, "local": 1, "thumbnail": 2}[hit[1]], hit[0]))
    for _, quality, path in files:
        try:
            before = path.stat()
            if before.st_size > MAX_IMAGE + 1024:
                raise ProbeError("image_size_limit")
            data = read_shared_file(path, MAX_IMAGE+1024, check=session.memory.check)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ProbeError("image_file_changed")
            data = decrypt_image(data, lambda: image_keys(session))
            mime = verified_image(data)
            return dict(status="available", reason=None, mime_type=mime,
                        data_base64=base64.b64encode(data).decode("ascii"), quality=quality)
        except (ProbeError, OSError) as exc:
            reason = str(exc) if isinstance(exc, ProbeError) else "image_read_failed"
    return dict(status="unavailable", reason=reason)
