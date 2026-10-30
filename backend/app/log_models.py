"""Local diagnostic log contracts. List responses never contain message bodies."""
from typing import Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from .role_models import JsonRequest, PreferencesQuery

class LogAccount(JsonRequest):
    account_id: str = Field(min_length=1, max_length=128)

class LogQuery(LogAccount):
    category: Literal["ai", "source"] = "ai"
    scope: Literal["all", "account", "global"] = "all"
    conversation_id: str | None = None
    profile_id: str | None = None
    status: Literal["running", "completed", "failed", "interrupted"] | None = None
    since: datetime | None = None
    until: datetime | None = None
    cursor: str | None = None
    page_size: int = Field(default=30, ge=1, le=100)

    @field_validator("since", "until", mode="before")
    @classmethod
    def timestamp_with_timezone(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
            raise ValueError("timestamp_requires_timezone")
        return value

    @model_validator(mode="after")
    def ordered_range(self):
        if self.since and self.until and self.since > self.until:
            raise ValueError("invalid_time_range")
        return self

class LogGet(LogAccount):
    log_id: str = Field(min_length=1, max_length=128)

class LogClear(LogAccount):
    scope: Literal["account", "global"]

class LogSummary(BaseModel):
    trigger_reason: str | None = None
    round_id: str | None = None
    batch_index: int | None = None
    round_message_count: int | None = None
    round_completed_count: int | None = None
    round_final: bool | None = None
    chose_silence: bool | None = None
    log_id: str
    category: Literal["ai", "source"]
    operation: str
    account_id: str | None = None
    conversation_id: str | None = None
    conversation_name: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    started_at: str
    updated_at: str
    status: str
    application_status: str | None = None
    trigger: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    provider: str | None = None
    model: str | None = None
    protocol: str | None = None
    profile_revision: int | None = None
    output_mode: str | None = None
    http_status: int | None = None
    provider_request_id: str | None = None
    duration_ms: int | None = None
    count: int = 1
    item_count: int | None = None
    source: str | None = None
    error: str | None = None
    message: str | None = None
    legacy: bool = False

class LogDetail(LogSummary):
    details: dict[str, Any]

class LogPage(BaseModel):
    items: list[LogSummary]
    next_cursor: str | None = None
    warning: str | None = None
    retention_days: int = 7
    size_bytes: int
    limit_bytes: int

class LogDeleted(BaseModel):
    deleted_count: int

class SessionSummary(BaseModel):
    account_id: str
    conversation_id: str
    name: str
    ai_enabled: bool
    pending_count: int
    waiting_count: int
    paused: bool
    memory_revision: int

class SessionPage(BaseModel):
    items: list[SessionSummary]

class RunMessagesQuery(PreferencesQuery):
    status: Literal["pending", "waiting_transcript", "waiting_image", "unsupported", "processing", "failed"] | None = None
    cursor: str | None = None
    page_size: int = Field(default=30, ge=1, le=100)

from .message_models import Message

class RunMessagesPage(BaseModel):
    items: list[Message]
    next_cursor: str | None = None
