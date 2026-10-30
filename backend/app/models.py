"""Browser-safe contracts, independent of the probe's Python/CLI schema."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Instance(BaseModel):
    pid: int
    created: str = Field(pattern=r"^\d+$", description="Exact Windows FILETIME, decimal string")
    session_id: int
    version: str
    core_sha256: str | None = None
    profile_id: str | None = None


class Avatar(BaseModel):
    mime_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data_base64: str


class Account(BaseModel):
    monitoring_started_at: datetime | None = None
    sync_pending: bool = False
    sync_issues: list[str] = Field(default_factory=list)
    source_state: Literal["online", "offline", "unknown"] = "unknown"
    last_seen_at: datetime | None = None
    last_synced_at: datetime | None = None
    revision: int = 0
    account_id: str | None = None
    wxid: str | None = None
    wechat_id: str | None = None
    nickname: str | None = None
    sources: dict[str, str] = Field(default_factory=dict)
    status: Literal["complete", "partial", "unavailable", "not_logged_in"]
    reason: str = ""
    instances: list[Instance] = Field(default_factory=list)
    avatar: Avatar | None = None
    avatar_status: Literal["available", "missing", "unavailable"]
    avatar_reason: str = ""


class Issue(BaseModel):
    reason: str
    pid: int | None = None


class Summary(BaseModel):
    confirmed_accounts: int
    complete_accounts: int
    avatar_available_accounts: int
    unresolved_instances: int
    coverage_complete: bool


class AccountSnapshot(BaseModel):
    scan_id: str
    scanned_at: datetime
    schema_version: Literal[2] = 2
    scope: Literal["current_windows_user_all_sessions"] = "current_windows_user_all_sessions"
    accounts: list[Account]
    skipped_instances: list[Account] = Field(default_factory=list)
    client_instances: list[Instance] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    summary: Summary
    exit_code: Literal[0, 1, 2]
    error: str | None = None
    duration_seconds: float | None = None
    observation: dict[str, JsonValue] = Field(default_factory=dict)


class ScanTask(BaseModel):
    scan_id: str
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None = None
    summary: Summary | None = None
    exit_code: Literal[0, 1, 2] | None = None
    error: str | None = None


class StartScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanQuery(StartScanRequest):
    scan_id: str = Field(min_length=1, max_length=128)


class AccountQuery(StartScanRequest):
    account_id: str = Field(min_length=1, max_length=128)


class ApiError(BaseModel):
    code: str
    message: str


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["wechat-agent-bridge"] = "wechat-agent-bridge"
    api_version: Literal["v1"] = "v1"
