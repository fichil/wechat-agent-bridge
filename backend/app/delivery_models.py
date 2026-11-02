from typing import Literal
from pydantic import BaseModel, Field
from .role_models import PreferencesQuery
from .media_models import MediaSummary

DeliveryStatus = Literal["not_required", "queued", "sending", "sent", "failed", "unknown", "cancelled"]


class DeliveryPart(BaseModel):
    index: int
    kind: Literal['text','image','sticker'] = 'text'
    media: MediaSummary | None = None
    status: DeliveryStatus
    attempts: int = 0
    code: str | None = None
    archive_message_id: str | None = None
    confirmation: Literal["native", "native_archive", "user"] | None = None


class Delivery(BaseModel):
    status: DeliveryStatus = "not_required"
    sent_parts: int = 0
    total_parts: int = 0
    parts: list[DeliveryPart] = Field(default_factory=list)


class DeliveryAction(PreferencesQuery):
    run_id: str = Field(min_length=1, max_length=128)


class DeliveryResolve(DeliveryAction):
    resolution: Literal["confirmed_sent", "abandoned"]


class SendCapability(BaseModel):
    account_id: str
    ready: bool
    code: str
    message: str
    profile_id: str | None = None
    core_sha256: str | None = None
    send_text: bool = False
    send_image: bool = False
    send_sticker: bool = False
