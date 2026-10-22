"""Value objects shared by adapters, policy and application code."""

from dataclasses import dataclass
from enum import Enum


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifiers must be non-empty strings")


@dataclass(frozen=True)
class Target:
    account_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        _identifier(self.account_id)
        _identifier(self.conversation_id)


@dataclass(frozen=True)
class MessageEvent:
    target: Target
    message_id: str
    sender_id: str
    text: str
    is_own: bool = False

    def __post_init__(self) -> None:
        _identifier(self.message_id)
        _identifier(self.sender_id)
        if not isinstance(self.text, str) or type(self.is_own) is not bool:
            raise ValueError("invalid message payload")


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    target: Target
    text: str
    operation: str = "send_text"

    def __post_init__(self) -> None:
        _identifier(self.request_id)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")


class ActionStatus(str, Enum):
    DENIED = "denied"
    DRY_RUN = "dry_run"
    ATTEMPTED = "attempted"
    UI_CONFIRMED = "ui_confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionResult:
    request_id: str
    status: ActionStatus
    reason: str
    attempted: bool = False
    replayed: bool = False


@dataclass(frozen=True)
class TargetState:
    target: Target | None
    connected: bool = True
    has_draft: bool = False
    identity_certain: bool = True
