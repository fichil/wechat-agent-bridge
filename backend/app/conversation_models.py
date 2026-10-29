from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .models import Avatar


class Exclusions(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=
                             lambda schema: schema.update(required=list(schema["properties"])))
    service_accounts: StrictBool = True
    official_accounts: StrictBool = True
    service_notifications: StrictBool = True
    file_transfer_assistant: StrictBool = True
    folded_chats: StrictBool = True
    wechat_pay: StrictBool = True
    enterprise_contacts: StrictBool = True


class ConversationAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=128)


class RefreshQuery(ConversationAccountRequest):
    refresh_id: str = Field(min_length=1, max_length=128)


class ConversationQuery(ConversationAccountRequest):
    cursor: str | None = Field(default=None, min_length=1, max_length=128)
    page_size: int = Field(default=50, ge=1, le=100, strict=True)


class SettingsUpdate(ConversationAccountRequest):
    exclusions: Exclusions

    @model_validator(mode="after")
    def complete_replacement(self):
        if self.exclusions.model_fields_set != set(Exclusions.model_fields):
            raise ValueError("all_exclusions_required")
        return self


class ConversationSettings(BaseModel):
    account_id: str
    exclusions: Exclusions


class Conversation(BaseModel):
    conversation_id: str
    name: str
    category: Literal["contact", "group", "system", "unknown", "service_accounts",
                      "official_accounts", "service_notifications", "file_transfer_assistant",
                      "folded_chats", "wechat_pay", "enterprise_contacts"]
    is_container: bool
    summary: str | None
    last_message_at: datetime | None
    unread_count: int | None = Field(ge=0)
    avatar: Avatar | None = None


class ConversationRefresh(BaseModel):
    account_id: str
    refresh_id: str
    status: Literal["queued", "running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class ConversationPage(BaseModel):
    last_synced_at: datetime | None = None
    source_state: Literal["online", "offline", "unknown"] = "unknown"
    coverage: Literal["complete", "partial", "not_synced"] = "not_synced"
    needs_sync: bool = False
    revision: int = 0
    account_id: str
    snapshot_id: str
    observed_at: datetime
    status: Literal["complete", "partial"]
    items: list[Conversation]
    total: int
    unfiltered_total: int
    next_cursor: str | None
    applied_exclusions: Exclusions
    issues: list[str] = Field(default_factory=list)
    stale: bool = False
