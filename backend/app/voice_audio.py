"""Bounded PCM/WAV validation and Tencent SILK encoding; no network or storage."""
import audioop
import hashlib
import io
import re
import struct
import wave

from .conversations import ConversationError
from .delivery_storage import text_units

SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_DURATION_MS = 60000
SILK_HEADER = b'\x02#!SILK_V3'


def invalid(message='语音音频无效，已准备回退为原文。'):
    raise ConversationError('voice_audio_invalid', message, 422)


def make_wav(pcm, sample_rate=SAMPLE_RATE):
    result = io.BytesIO()
    with wave.open(result, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm)
    return result.getvalue()


def normalize_audio(data, mime='audio/wav'):
    if not isinstance(data, bytes) or not data or len(data) > MAX_AUDIO_BYTES:
        invalid()
    if mime.lower().startswith('audio/l16'):
        rate = re.search(r'(?:^|;)\s*rate=(\d+)', mime, re.I)
        channels = re.search(r'(?:^|;)\s*channels=(\d+)', mime, re.I)
        if not rate or (channels and channels[1] != '1'):
            invalid()
        sample_rate, channel_count, width, pcm = int(rate[1]), 1, 2, data
    else:
        if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
            invalid()
        # Streaming WAV writers may leave RIFF/data lengths at UINT32_MAX.
        repaired = bytearray(data)
        if struct.unpack_from('<I', data, 4)[0] == 0xffffffff:
            struct.pack_into('<I', repaired, 4, len(data)-8)
        elif struct.unpack_from('<I', data, 4)[0] != len(data)-8:
            invalid()
        position, seen_data = 12, False
        while position < len(data):
            if position+8 > len(data): invalid()
            name, size = struct.unpack_from('<4sI', data, position)
            if name == b'data':
                if seen_data: invalid()
                seen_data = True
                if size == 0xffffffff:
                    size = len(data)-position-8
                    struct.pack_into('<I', repaired, position+4, size)
            if position+8+size > len(data): invalid()
            position += 8+size+(size % 2)
        try:
            with wave.open(io.BytesIO(repaired), 'rb') as stream:
                channel_count, width, sample_rate = stream.getnchannels(), stream.getsampwidth(), stream.getframerate()
                if stream.getcomptype() != 'NONE': invalid()
                pcm = stream.readframes(stream.getnframes())
                if len(pcm) != stream.getnframes()*channel_count*width: invalid()
        except (wave.Error, EOFError, ValueError):
            invalid()
    if sample_rate not in (8000, 12000, 16000, 24000, 32000, 44100, 48000) or channel_count not in (1, 2) or width != 2:
        invalid()
    if not pcm or len(pcm) % (width*channel_count): invalid()
    duration = len(pcm)*1000 // (width*channel_count*sample_rate)
    if not 0 < duration <= MAX_DURATION_MS:
        invalid('单条语音超过 60 秒或为空，已准备回退为原文。')
    if channel_count == 2: pcm = audioop.tomono(pcm, 2, .5, .5)
    if sample_rate != SAMPLE_RATE: pcm = audioop.ratecv(pcm, 2, 1, sample_rate, SAMPLE_RATE, None)[0]
    frame_bytes = SAMPLE_RATE*2//50
    pcm += b'\0' * (-len(pcm) % frame_bytes)
    return pcm


def silk_duration(data):
    if not data.startswith(SILK_HEADER) or len(data) > MAX_AUDIO_BYTES: invalid()
    position, packets = len(SILK_HEADER), 0
    while position < len(data):
        if position+2 > len(data): invalid()
        size = struct.unpack_from('<H', data, position)[0]
        position += 2
        if not 0 < size <= 32767 or position+size > len(data): invalid()
        position += size
        packets += 1
        if packets > 3000: invalid()
    if not packets: invalid()
    return packets*20


def encode_audio(data, mime='audio/wav'):
    pcm = normalize_audio(data, mime)
    duration = len(pcm)*1000//(SAMPLE_RATE*2)
    if duration > MAX_DURATION_MS: invalid()
    try:
        import pysilk
        output = io.BytesIO()
        pysilk.encode(io.BytesIO(pcm), output, SAMPLE_RATE, 24000, tencent=True)
        silk = output.getvalue()
    except Exception:
        raise ConversationError('voice_encoder_unavailable', '语音编码未完成，已准备回退为原文。', 503) from None
    if silk_duration(silk) != duration: invalid()
    wav = make_wav(pcm)
    return dict(wav=wav, silk=silk, duration_ms=duration, sample_rate=SAMPLE_RATE,
                wav_sha256=hashlib.sha256(wav).hexdigest(), silk_sha256=hashlib.sha256(silk).hexdigest(),
                size_bytes=len(wav)+len(silk))


def split_voice_text(text, limit=160):
    """Preserve every character while preferring complete sentences."""
    parts, current = [], ''
    for sentence in re.split(r'(?<=[。！？!?；;\n])', text):
        for unit in text_units(sentence):
            if len(unit) > limit: invalid('语音包含过长的连续组合字符。')
            if len(current)+len(unit) > limit:
                parts.append(current)
                current = ''
            current += unit
        if current and len(current) >= limit//2:
            parts.append(current)
            current = ''
    if current: parts.append(current)
    return parts
