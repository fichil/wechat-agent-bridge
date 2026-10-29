from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from .conversation_models import ConversationAccountRequest


class MessageRequest(ConversationAccountRequest):
    refresh: bool = Field(default=False, strict=True)
    conversation_id: str = Field(min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=128)
    page_size: int = Field(default=50, ge=1, le=100, strict=True)


class ImageRequest(ConversationAccountRequest):
    """Read image or sticker bytes by an archived message identity; no caller URLs."""
    conversation_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)


class MessageTaskQuery(ConversationAccountRequest):
    task_id: str = Field(min_length=1, max_length=128)


class Sender(BaseModel):
    nickname: str | None
    wechat_id: str | None
    wxid: str | None
    remark: str | None
    status: Literal["complete", "partial", "unknown"]


class MessageContent(BaseModel):
    text: str | None = None
    filename: str | None = None
    format: str | None = None
    description: str | None = None
    transcript: str | None = None
    transcript_source: Literal["wechat_xml", "wechat_packed_info"] | None = None
    transcript_status: Literal["available", "not_available"] | None = None
    duration_ms: int | None = None
    image_status: Literal["not_loaded"] | None = None


class Message(BaseModel):
    message_id: str
    sort_key: str = Field(default="", description="Opaque stable lexical ordering key; do not parse or submit as a cursor.")
    sent_at: datetime | None
    type: Literal["text", "image", "voice", "video", "sticker", "attachment", "system", "unsupported"]
    content: MessageContent
    sender: Sender
    is_self: bool | None
    issues: list[str] = Field(default_factory=list)


class MessagePage(BaseModel):
    last_synced_at: datetime | None = None
    source_state: Literal["online", "offline", "unknown"] = "unknown"
    coverage: Literal["complete", "partial", "not_synced"] = "not_synced"
    needs_sync: bool = False
    revision: int = 0
    account_id: str
    conversation_id: str
    snapshot_id: str
    observed_at: datetime
    status: Literal["complete", "partial"]
    items: list[Message]
    older_cursor: str | None
    newer_cursor: str
    has_more: bool
    issues: list[str] = Field(default_factory=list)


class MessageImage(BaseModel):
    """Original image/sticker bytes as standard base64 JSON; GIF animation is preserved."""
    needs_sync: bool = False
    message_id: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    mime_type: str | None = None
    data_base64: str | None = None
    quality: Literal["original", "local", "thumbnail"] | None = None


class MessageReadTask(BaseModel):
    account_id: str
    conversation_id: str
    task_id: str
    kind: Literal["history", "image"]
    status: Literal["queued", "running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    page: MessagePage | None = None
    image: MessageImage | None = None
