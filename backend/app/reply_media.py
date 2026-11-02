"""Reply grammar and bounded image validation, independent of storage and transport."""
import base64
import hashlib
import io
import re
import warnings
from PIL import Image, UnidentifiedImageError
from .conversations import ConversationError

MAX_IMAGE = 8 * 1024 * 1024
MAX_RESPONSE = 24 * 1024 * 1024
MAX_REPLY_MEDIA = 16 * 1024 * 1024
MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}
MARKER = re.compile(r'\[\[(IMAGE|STICKER):([^\r\n]+)\]\]')
SPLIT = re.compile(r'(?m)^[ \t]*\[\[SPLIT\]\][ \t]*\r?$')
RESERVED = re.compile(r'\[\[')
DATA_URI = re.compile(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)')


def invalid(message):
    raise ConversationError('reply_invalid', message, 422)


def decode_base64(value, mime=None):
    if not isinstance(value, str) or len(value) > (MAX_IMAGE + 2) // 3 * 4 + 64:
        invalid('图片 Base64 超过 8 MiB 解码限制。')
    if value.startswith('data:'):
        match = DATA_URI.fullmatch(value)
        if not match: invalid('图片 data URI 格式无效。')
        mime, value = match.groups()
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError):
        invalid('图片 Base64 编码无效。')
    if base64.b64encode(data).decode('ascii') != value: invalid('图片 Base64 编码不规范。')
    if not data or len(data) > MAX_IMAGE: invalid('图片必须非空且不超过 8 MiB。')
    return data, mime


def validate_media(data, kind='image', mime=None):
    if not data or len(data) > MAX_IMAGE: invalid('图片必须非空且不超过 8 MiB。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as check: check.verify()
            with Image.open(io.BytesIO(data)) as picture:
                detected = MIMES.get(picture.format)
                if not detected or (mime and mime != detected): invalid('图片格式与声明不符，或不是 PNG/JPEG/GIF/WebP。')
                width, height = picture.size
                frames = getattr(picture, 'n_frames', 1)
                if width * height > 40_000_000 or frames > 500 or width * height * frames > 100_000_000:
                    invalid('图片尺寸、帧数或累计解码像素超过限制。')
                for frame in range(frames): picture.seek(frame); picture.load()
                picture.seek(0)
                if kind == 'sticker':
                    if width > 1024 or height > 1024: invalid('原生表情宽高均不能超过 1024。')
                    if detected != 'image/gif':
                        if frames > 1: invalid('动画表情请上传 GIF，其他动画格式不自动抽帧。')
                        out = io.BytesIO()
                        picture.convert('RGBA').save(out, format='GIF', disposal=2)
                        data, detected = out.getvalue(), 'image/gif'
                    if len(data) > 1024 * 1024: invalid('原生表情的发送 GIF 不能超过 1 MiB。')
                elif kind == 'image' and frames > 1:
                    invalid('动画请使用原生表情类型发送。')
                elif kind == 'image' and detected not in ('image/png', 'image/jpeg'):
                    out = io.BytesIO(); picture.convert('RGBA').save(out, format='PNG')
                    data, detected = out.getvalue(), 'image/png'
                    if len(data) > MAX_IMAGE: invalid('图片转换后超过 8 MiB。')
                return dict(data=data, mime_type=detected, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                            width=width, height=height, frame_count=frames)
    except (UnidentifiedImageError, OSError, ValueError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        invalid('图片损坏或无法安全解码。')


def parse_reply(reply):
    """Return logical parts; only complete stand-alone markers have control meaning."""
    if not isinstance(reply, str) or '\0' in reply: invalid('回复包含无法发送的内容。')
    parts = []
    for raw in SPLIT.split(reply):
        segment = raw.strip()
        if not segment: continue
        match = MARKER.fullmatch(segment)
        if match:
            tag, reference = match.groups()
            kind = 'image' if tag == 'IMAGE' else 'sticker'
            if reference.startswith('data:'):
                data, mime = decode_base64(reference)
                parts.append(dict(kind=kind, media=validate_media(data, kind, mime)))
            elif re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', reference):
                parts.append(dict(kind=kind, reference=reference))
            else: invalid('媒体引用格式无效。')
        else:
            if RESERVED.search(segment): invalid('媒体标记和分隔符必须完整且独占消息段。')
            from .diagnostics import BARE_IMAGE_BASE64
            if re.search(r'data:image/[^\s,]*;base64',segment,re.I) or BARE_IMAGE_BASE64.search(segment):
                invalid('图片 Base64 必须放在独占消息段的 IMAGE 或 STICKER 标记中。')
            parts.append(dict(kind='text', text=segment))
    if sum(len(p.get('text','').encode()) for p in parts) > 64 * 1024:
        invalid('回复文字超过 64 KiB。')
    if sum(p['kind'] != 'text' for p in parts) > 16: invalid('一次回复最多包含 16 项媒体。')
    return parts


def summarize_memory(memory):
    from .diagnostics import sanitize
    value=sanitize(memory)
    value=re.sub(r'\[\[(IMAGE|STICKER):[^\r\n]*?\]\]',lambda m:'[图片]' if m[1]=='IMAGE' else '[表情]',value)
    return value.replace('[[SPLIT]]','\n')


REPLY_INSTRUCTIONS = '''Reply protocol v2: reply is still a string. Separate independent WeChat messages ONLY with [[SPLIT]] on its own line. Ordinary newlines stay within one message.
A media segment must contain ONLY [[STICKER:asset_id]] from reply_media.stickers or [[IMAGE:message_id]] from reply_media.images; never invent IDs. Text before/after media is a separate segment.
Choose an occasional sticker when suitable for the character and context; an image-only reply is valid. No image generation service exists. Prefer references, never synthesize base64. If actual image data is supplied by your service, [[IMAGE:data:image/png;base64,...]] or [[STICKER:data:image/gif;base64,...]] is supported.
Media availability is explicitly listed in reply_media. If a media capability is false, do not request it. Include human-readable descriptions in memory, never base64 or control markers. Generated replies are not proof of delivery.'''
