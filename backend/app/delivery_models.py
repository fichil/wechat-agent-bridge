from typing import Literal
from pydantic import BaseModel, Field
from .role_models import PreferencesQuery

DeliveryStatus = Literal["not_required", "queued", "sending", "sent", "failed", "unknown", "cancelled"]


class DeliveryPart(BaseModel):
    index: int
    status: DeliveryStatus
    attempts: int = 0
    code: str | None = None
    archive_message_id: str | None = None
    confirmation: Literal["native", "user"] | None = None


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
