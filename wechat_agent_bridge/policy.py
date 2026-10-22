"""Deterministic authorization; message content never changes policy."""

from dataclasses import dataclass
from enum import Enum

from .domain import ActionRequest, Target


class Mode(str, Enum):
    READ_ONLY = "read-only"
    DRY_RUN = "dry-run"
    MOCK = "mock"


@dataclass(frozen=True)
class Policy:
    allowed_targets: frozenset[Target] = frozenset()
    mode: Mode = Mode.READ_ONLY
    max_text_length: int = 500

    def __post_init__(self) -> None:
        # Freeze caller-owned containers and reject unknown modes rather than
        # accidentally falling through into execution.
        object.__setattr__(self, "allowed_targets", frozenset(self.allowed_targets))
        object.__setattr__(self, "mode", Mode(self.mode))
        if self.max_text_length < 1:
            raise ValueError("max_text_length must be positive")

    def target_denial(self, target: Target) -> str | None:
        return None if target in self.allowed_targets else "target_not_allowed"

    def action_denial(self, request: ActionRequest) -> str | None:
        if denial := self.target_denial(request.target):
            return denial
        if request.operation != "send_text":
            return "operation_not_allowed"
        if self.mode is Mode.READ_ONLY:
            return "read_only"
        if len(request.text) > self.max_text_length:
            return "text_too_long"
        return None
