"""M1 value objects. Client identities are never inferred from display names."""

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    wechat_id: str | None = None
    instance_id: str = ""
    version: str = ""
    identity_certain: bool = True
    connected: bool = True
    verified: bool = False
    identity_source: str = ""
    fingerprint: dict = field(default_factory=dict)
    discovery_state: str = ""
    discovery_reason: str = ""
    discovery_sources: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Conversation:
    account_id: str
    id: str
    name: str
    kind: str = "private"
    wechat_id: str | None = None
    unread: int | None = None
    preview: str = ""
    updated_at: str = ""
    identity_certain: bool = True
    identity_source: str = ""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    mime: str
    size: int = 0
    status: str = "available"


@dataclass(frozen=True)
class ChatMessage:
    account_id: str
    conversation_id: str
    id: str
    sender: str
    text: str
    timestamp: str
    own: bool | None = False
    kind: str = "text"
    mentions_self: bool | None = False
    attachments: tuple[Attachment, ...] = ()
    historical: bool = False
    upload_eligible: bool = True
    sender_remark: str | None = None
    sender_wechat_id: str | None = None
    sender_identity_source: str = ""
    content_status: str = "available"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ChatMessage":
        data = dict(value)
        data["attachments"] = tuple(Attachment(**item) for item in data.get("attachments", []))
        return cls(**data)


@dataclass(frozen=True)
class MessagePage:
    messages: tuple[ChatMessage, ...]
    cursor: str | None = None
    complete: bool = True
    limitation: str = ""
    loaded_count: int | None = None
    loaded_start: int | None = None
    loaded_end: int | None = None


@dataclass
class Grant:
    read: bool = False
    upload: bool = False
    reply: bool = False
    destination: str = ""
    excluded: list[str] = field(default_factory=list)
    paused: bool = True


class ConsoleError(RuntimeError):
    """Only fixed, non-sensitive reason codes cross the application boundary."""


class ConsoleAdapter(Protocol):
    simulation_only: bool

    def discover(self) -> list[Account]: ...
    def conversations(self, account_id: str) -> list[Conversation]: ...
    def messages(self, account_id: str, conversation_id: str, cursor: str | None = None) -> MessagePage: ...
    def attachment(self, account_id: str, conversation_id: str, attachment_id: str) -> bytes: ...
    def inspect(self, account_id: str, conversation_id: str) -> dict: ...
    def send(self, account_id: str, conversation_id: str, text: str, request_id: str) -> dict: ...
    def close(self) -> None: ...
