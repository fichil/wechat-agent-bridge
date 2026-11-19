"""CosyVoice HTTP synthesis with bounded, credential-free result downloads."""
import json
import re
import struct
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .conversations import ConversationError
from .voice_audio import MAX_AUDIO_BYTES, encode_audio


def instruction_units(value):
    return sum(2 if '\u3400' <= char <= '\u9fff' else 1 for char in value)


def _audio_url(value):
    if not isinstance(value, str):
        raise ValueError()
    url = urlsplit(value)
    # The service can return HTTP OSS URLs. Fetch the same signed object via TLS.
    if (url.scheme not in ('http', 'https') or url.username is not None or url.password is not None or url.fragment
            or url.port not in (None, 80, 443)
            or url.hostname != 'dashscope-result-bj.oss-cn-beijing.aliyuncs.com'):
        raise ValueError()
    return urlunsplit(('https', url.hostname, url.path, url.query, ''))


def normalize_provider_wav(raw):
    # CosyVoice's non-seekable PCM writer leaves these two exact placeholder
    # lengths in otherwise complete 44-byte-header WAV files. Keep all other
    # malformed lengths subject to the shared strict WAV validator.
    if (len(raw) >= 44 and raw[:4] == b'RIFF' and raw[8:20] == b'WAVEfmt \x10\x00\x00\x00'
            and raw[20:36] == struct.pack('<HHIIHH', 1, 1, 24000, 48000, 2, 16)
            and raw[36:40] == b'data'
            and struct.unpack_from('<I',raw,4)[0] == 0x7fffffbf
            and struct.unpack_from('<I',raw,40)[0] == 0x7fffff9b):
        repaired = bytearray(raw)
        struct.pack_into('<I',repaired,4,len(raw)-8)
        struct.pack_into('<I',repaired,40,len(raw)-44)
        return bytes(repaired)
    return raw


def synthesize(profile, key, text, voice, instructions, expires, transport=None):
    if instruction_units(instructions) > 100:
        raise ConversationError('voice_instructions_invalid', 'CosyVoice 说话风格最多 100 个字符，汉字按 2 个计算。', 422)
    base = profile['base_url'].rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'https' or url.username is not None or url.password is not None or url.query or url.fragment
            or url.port not in (None, 443)
            or not re.fullmatch(r'[A-Za-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com', url.hostname or '')
            or url.path != '/api/v1'):
        raise ConversationError('voice_provider_invalid', 'CosyVoice 需要北京工作空间的 HTTPS API 地址。', 422)
    body = dict(model=profile.get('speech_model') or profile['model'],
                input=dict(text=text, voice=voice, format='wav', sample_rate=24000))
    if instructions:
        body['input']['instruction'] = instructions

    def timeout():
        remaining = expires-time.monotonic()
        if remaining <= 0:
            raise ConversationError('voice_timeout', '语音合成超过等待预算。', 503)
        return httpx.Timeout(remaining, connect=min(10, remaining))

    def read(response, limit):
        if response.status_code >= 300:
            raise ConversationError('voice_provider_error', f'语音服务未完成请求（HTTP {response.status_code}）。', 502)
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            timeout()
            size += len(chunk)
            if size > limit:
                raise ValueError()
            chunks.append(chunk)
        return b''.join(chunks)

    try:
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            with client.stream('POST', base+'/services/audio/tts/SpeechSynthesizer',
                               headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'},
                               json=body, timeout=timeout()) as response:
                document = json.loads(read(response, 256*1024))
            if document.get('code') or document['output'].get('finish_reason', 'stop') != 'stop':
                raise ValueError()
            audio_url = _audio_url(document['output']['audio']['url'])
            # Authorization is scoped to the POST. Never forward it to the OSS object.
            with client.stream('GET', audio_url, timeout=timeout()) as response:
                raw = read(response, MAX_AUDIO_BYTES)
        return encode_audio(normalize_provider_wav(raw))
    except ConversationError:
        raise
    except (httpx.HTTPError, OSError):
        raise ConversationError('voice_network_error', '语音服务连接失败或超时。', 503) from None
    except (ValueError, KeyError, TypeError):
        raise ConversationError('voice_audio_invalid', '语音服务没有返回完整可用的音频。', 502) from None
