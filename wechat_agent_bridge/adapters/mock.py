"""Synthetic fixtures only: no UI, clipboard, network or model access."""

from enum import Enum
from typing import Iterable

from ..domain import (
    ActionRequest, ActionResult, ActionStatus, MessageEvent, Target, TargetState,
)
from .base import AdapterRejected


class MockOutcome(str, Enum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SUBMIT_TIMEOUT = "submit-timeout"
    VERIFY_TIMEOUT = "verify-timeout"


class MockAdapter:
    simulation_only = True

    def __init__(
        self,
        target: Target,
        events: Iterable[MessageEvent] = (),
        outcome: MockOutcome = MockOutcome.CONFIRMED,
    ) -> None:
        self.state = TargetState(target)
        self.events = tuple(events)
        self.outcome = MockOutcome(outcome)
        self.submitted: list[ActionRequest] = []
        self.submit_calls = 0
        self.verify_calls = 0

    def inspect(self, target: Target) -> TargetState:
        return self.state

    def read(self, target: Target) -> tuple[MessageEvent, ...]:
        self._guard(target, writing=False)
        # The application also checks envelopes, even when an adapter leaks
        # an out-of-scope fixture. Text is always treated as inert data.
        return self.events

    def _guard(self, target: Target, *, writing: bool) -> None:
        if not self.state.connected:
            raise AdapterRejected("disconnected")
        if not self.state.identity_certain or self.state.target is None:
            raise AdapterRejected("identity_uncertain")
        if self.state.target != target:
            raise AdapterRejected("target_mismatch")
        if writing and self.state.has_draft:
            raise AdapterRejected("existing_draft")

    def submit(self, request: ActionRequest) -> ActionResult:
        self.submit_calls += 1
        # Recheck at the write boundary, not only during application preflight.
        self._guard(request.target, writing=True)
        if self.outcome is MockOutcome.FAILED:
            return ActionResult(request.request_id, ActionStatus.FAILED, "mock_rejected")
        self.submitted.append(request)
        if self.outcome is MockOutcome.SUBMIT_TIMEOUT:
            raise TimeoutError("synthetic timeout after the possible write")
        return ActionResult(
            request.request_id, ActionStatus.ATTEMPTED, "mock_attempted", attempted=True,
        )

    def verify(self, request: ActionRequest) -> ActionResult:
        self.verify_calls += 1
        if request not in self.submitted:
            return ActionResult(request.request_id, ActionStatus.FAILED, "not_attempted")
        if self.outcome is MockOutcome.VERIFY_TIMEOUT:
            raise TimeoutError("synthetic verification timeout")
        # A target switch or disconnect after submission cannot prove failure.
        try:
            self._guard(request.target, writing=False)
        except AdapterRejected:
            return ActionResult(
                request.request_id, ActionStatus.UNKNOWN, "verification_unavailable", True,
            )
        status = (
            ActionStatus.UNKNOWN if self.outcome is MockOutcome.UNKNOWN
            else ActionStatus.UI_CONFIRMED
        )
        return ActionResult(request.request_id, status, "mock_" + status.value, True)
