"""Speech uses the selected chat profile's origin and protected credential."""
import base64
from datetime import datetime, timezone
import json
import re
import time
from urllib.parse import quote

import httpx

from .conversations import ConversationError
from .voice_audio import encode_audio, MAX_AUDIO_BYTES
from .voice_models import VOICE_INSTRUCTIONS_LIMIT, SPEECH_TEXT_PATTERN

SPEECH_SECONDS = 45
DEFAULT_VOICES = {'openai_speech': 'alloy', 'gemini_generate_content': 'Kore'}
VOICE_EXTENSION = 'wechat_agent_bridge'
SPEECH_CONTENT_INSTRUCTIONS = ('Read only the supplied input text, exactly as written. Do not translate, add or omit words, '
                               'read these style instructions aloud, or add laughter, sound effects or introductions. '
                               'Express the character through delivery only, adapting emotion to the input text.')


def speech_protocol(profile):
    protocol = profile.get('speech_protocol', 'auto')
    return ('gemini_generate_content' if profile.get('protocol') == 'gemini_generate_content' else 'openai_speech') if protocol == 'auto' else protocol


def voice_for_card(card, profile):
    settings = card.get('data', {}).get('extensions', {}).get(VOICE_EXTENSION, {})
    voices = settings.get('voices', {}) if isinstance(settings, dict) else {}
    voice = voices.get(profile.get('profile_id')) if isinstance(voices, dict) else None
    return voice.strip() if voice else DEFAULT_VOICES[speech_protocol(profile)]


def instructions_for_card(card, profile):
    settings = card.get('data', {}).get('extensions', {}).get(VOICE_EXTENSION, {})
    bindings = settings.get('voice_instructions', {}) if isinstance(settings, dict) else {}
    value = bindings.get(profile.get('profile_id'), '') if isinstance(bindings, dict) else ''
    return value.strip() if isinstance(value, str) else ''


def supports_voice_instructions(profile):
    model = profile.get('speech_model') or profile['model']
    return (speech_protocol(profile) == 'openai_speech'
            and bool(re.fullmatch(r'gpt-4o-mini-tts(?:-\d{4}-\d{2}-\d{2})?', model)))


def validate_voice_instructions(value):
    if (not isinstance(value, str) or len(value) > VOICE_INSTRUCTIONS_LIMIT
            or re.fullmatch(SPEECH_TEXT_PATTERN, value) is None):
        raise ConversationError('voice_instructions_invalid', '说话风格须为不超过 2000 字符的文本，不能包含无效控制字符。', 422)


def validate_voice_bindings(card):
    extension = card.get('data', {}).get('extensions', {}).get(VOICE_EXTENSION)
    if extension is None: return
    if not isinstance(extension, dict):
        raise ConversationError('voice_binding_invalid', '角色卡语音设置必须为对象。', 422)
    bindings = extension.get('voices', {})
    if not isinstance(bindings, dict) or len(bindings) > 100:
        raise ConversationError('voice_binding_invalid', '角色卡音色绑定格式无效或超过 100 项。', 422)
    for profile, voice in bindings.items():
        if (not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', profile)
                or not isinstance(voice, str) or not voice.strip() or len(voice)>200
                or any(ord(c)<32 for c in voice)):
            raise ConversationError('voice_binding_invalid', '音色名称必须非空，且不能包含控制字符。', 422)
    styles = extension.get('voice_instructions', {})
    if not isinstance(styles, dict) or len(styles) > 100:
        raise ConversationError('voice_instructions_invalid', '角色说话风格须按 API 配置绑定，且不能超过 100 项。', 422)
    for profile, instructions in styles.items():
        if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', profile):
            raise ConversationError('voice_instructions_invalid', '说话风格的 API 配置标识无效。', 422)
        validate_voice_instructions(instructions)


class SpeechProvider:
    def __init__(self, transport=None):
        self.transport = transport

    def synthesize(self, profile, key, text, voice=None, *, instructions='', deadline=None):
        validate_voice_instructions(instructions)
        instructions = instructions.strip()
        if instructions and not supports_voice_instructions(profile):
            raise ConversationError('voice_style_unsupported', '当前语音模型不支持说话风格；请使用 gpt-4o-mini-tts 系列或清空风格设置。', 422)
        protocol = speech_protocol(profile)
        model = profile.get('speech_model') or profile['model']
        voice = voice or DEFAULT_VOICES[protocol]
        base = profile['base_url'].rstrip('/')
        if protocol == 'openai_speech':
            url = base+'/audio/speech'
            headers = {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}
            body = dict(model=model, input=text, voice=voice, response_format='wav')
            if instructions:
                body['instructions'] = instructions+'\n\n'+SPEECH_CONTENT_INSTRUCTIONS
        else:
            url = base+'/models/'+quote(model.removeprefix('models/'), safe='')+':generateContent'
            headers = {'x-goog-api-key': key, 'Content-Type': 'application/json'}
            # Gemini 3.8 introduced direct voice names; older TTS uses prebuiltVoiceConfig.
            voice_config = {'voice': voice} if model.startswith('gemini-3.8-') else {'prebuiltVoiceConfig': {'voiceName': voice}}
            body = {'contents': [{'role': 'user', 'parts': [{'text': text}]}],
                    'generationConfig': {'responseModalities': ['AUDIO'], 'speechConfig': {'voiceConfig': voice_config}}}
        expires = min(deadline or float('inf'), time.monotonic()+SPEECH_SECONDS)
        remaining = expires-time.monotonic()
        if remaining <= 0:
            raise ConversationError('voice_timeout', '语音合成超过等待预算。', 503)
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(remaining, connect=min(10, remaining)), follow_redirects=False) as client:
                with client.stream('POST', url, headers=headers, json=body) as response:
                    if response.status_code >= 300:
                        unsupported = response.status_code in (400, 404, 405, 415, 422)
                        raise ConversationError('voice_unsupported' if unsupported else 'voice_provider_error',
                                                f'语音服务未完成请求（HTTP {response.status_code}）。', 502)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() > expires:
                            raise ConversationError('voice_timeout', '语音合成超过等待预算。', 503)
                        size += len(chunk)
                        if size > MAX_AUDIO_BYTES*4//3+8192:
                            raise ConversationError('voice_audio_invalid', '语音服务返回内容超过大小限制。', 502)
                        chunks.append(chunk)
                    raw = b''.join(chunks)
            mime = 'audio/wav'
            if protocol == 'gemini_generate_content':
                document = json.loads(raw)
                candidates = document.get('candidates', [])
                if len(candidates) != 1 or candidates[0].get('finishReason', 'STOP') != 'STOP': raise ValueError()
                parts = candidates[0].get('content', {}).get('parts', [])
                audio = [p['inlineData'] for p in parts if 'inlineData' in p]
                if len(audio) != 1: raise ValueError()
                raw = base64.b64decode(audio[0]['data'], validate=True)
                mime = audio[0]['mimeType']
                if not (mime.lower().startswith('audio/l16') or mime.lower().split(';')[0] in ('audio/wav', 'audio/x-wav')): raise ValueError()
            return encode_audio(raw, mime)
        except ConversationError:
            raise
        except (httpx.HTTPError, OSError):
            raise ConversationError('voice_network_error', '语音服务连接失败或超时。', 503) from None
        except (ValueError, KeyError, TypeError):
            raise ConversationError('voice_audio_invalid', '语音服务没有返回完整可用的音频。', 502) from None

    def test(self, profile, key):
        started = time.monotonic()
        value = dict(status='unverified', tested_at=datetime.now(timezone.utc).isoformat(),
                     model=profile.get('speech_model') or profile['model'], protocol=speech_protocol(profile),
                     default_voice=DEFAULT_VOICES[speech_protocol(profile)])
        try:
            audio = self.synthesize(profile, key, '这是一条人工智能合成的语音测试。')
            value.update(status='supported', duration_ms=audio['duration_ms'])
        except ConversationError as exc:
            value.update(status='unsupported' if exc.code=='voice_unsupported' else 'failed', error=exc.code, message=exc.message)
        value['latency_ms'] = round((time.monotonic()-started)*1000)
        return value
