from typing import Literal
from pydantic import BaseModel, Field
from .role_models import JsonRequest
from .media_models import AudioSummary

VOICE_INSTRUCTIONS_LIMIT = 2000
PREVIEW_TEXT_LIMIT = 500
SPEECH_TEXT_PATTERN = r'^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$'


class SpeechVerification(BaseModel):
    status: Literal['unverified','testing','supported','unsupported','failed'] = 'unverified'
    tested_at: str | None = None
    model: str | None = None
    protocol: str | None = None
    default_voice: str | None = None
    duration_ms: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    message: str | None = None


class AudioData(AudioSummary):
    data_base64: str


class VoicePreview(JsonRequest):
    profile_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    voice: str = Field(default='', max_length=200, pattern=r'^[^\x00-\x1f]*$')
    instructions: str = Field(default='', max_length=VOICE_INSTRUCTIONS_LIMIT, pattern=SPEECH_TEXT_PATTERN)
    text: str = Field(default='', max_length=PREVIEW_TEXT_LIMIT, pattern=SPEECH_TEXT_PATTERN)


class VoicePreviewResult(BaseModel):
    mime_type: Literal['audio/wav'] = 'audio/wav'
    data_base64: str
    duration_ms: int
