from typing import Literal
from pydantic import BaseModel, Field
from .role_models import JsonRequest
from .media_models import AudioSummary


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


class VoicePreviewResult(BaseModel):
    mime_type: Literal['audio/wav'] = 'audio/wav'
    data_base64: str
    duration_ms: int
