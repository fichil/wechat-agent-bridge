"""Synchronous M0 routing. Reading never automatically schedules a reply."""

from .adapters.base import Adapter, AdapterRejected
from .domain import ActionRequest, ActionResult, ActionStatus, MessageEvent, Target, TargetState
from .policy import Mode, Policy
from .state import StateStore


class OperationBlocked(RuntimeError):
    """A read was stopped; the exception contains only a fixed reason code."""


class Bridge:
    def __init__(self, adapter: Adapter, policy: Policy, state: StateStore) -> None:
        self.adapter = adapter
        self.policy = policy
        self.state = state
        self.paused = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def _target_problem(self, target: Target, *, writing: bool) -> str | None:
        if self.paused:
            return "paused"
        if denial := self.policy.target_denial(target):
            return denial
        try:
            current = self.adapter.inspect(target)
        except TimeoutError:
            return "inspection_timeout"
        except Exception:
            return "inspection_failed"
        if not isinstance(current, TargetState):
            return "invalid_target_state"
        if not current.connected:
            return "disconnected"
        if not current.identity_certain or current.target is None:
            return "identity_uncertain"
        if current.target != target:
            return "target_mismatch"
        if writing and current.has_draft:
            return "existing_draft"
        return None

    def poll(self, target: Target) -> tuple[MessageEvent, ...]:
        if problem := self._target_problem(target, writing=False):
            raise OperationBlocked(problem)
        try:
            events = self.adapter.read(target)
        except TimeoutError:
            raise OperationBlocked("read_timeout") from None
        except Exception:
            raise OperationBlocked("read_failed") from None
        accepted = []
        for event in events:
            if event.target != target or event.is_own:
                continue
            if self.state.remember_event(event):
                accepted.append(event)
        return tuple(accepted)

    def execute(self, request: ActionRequest) -> ActionResult:
        def result(status: ActionStatus, reason: str, attempted: bool = False) -> ActionResult:
            return ActionResult(request.request_id, status, reason, attempted)

        if self.paused:
            return result(ActionStatus.DENIED, "paused")
        if denial := self.policy.action_denial(request):
            return result(ActionStatus.DENIED, denial)
        if self.policy.mode is Mode.MOCK and self.adapter.simulation_only is not True:
            return result(ActionStatus.DENIED, "live_execution_disabled")
        if problem := self._target_problem(request.target, writing=True):
            return result(ActionStatus.DENIED, problem)
        if self.policy.mode is Mode.DRY_RUN:
            return result(ActionStatus.DRY_RUN, "validated_without_submission")
        previous = self.state.claim(request)
        if previous is not None:
            return previous

        # The durable claim precedes every possible write. Recheck the current
        # target, draft and pause immediately before passing to the adapter.
        if problem := self._target_problem(request.target, writing=True):
            final = result(ActionStatus.FAILED, problem)
        else:
            try:
                submitted = self.adapter.submit(request)
            except AdapterRejected:
                final = result(ActionStatus.FAILED, "adapter_rejected")
            except TimeoutError:
                final = result(ActionStatus.UNKNOWN, "submission_timeout", True)
            except Exception:
                # Exception messages may contain private UI or message text.
                final = result(ActionStatus.UNKNOWN, "submission_error", True)
            else:
                if not isinstance(submitted, ActionResult) or submitted.request_id != request.request_id:
                    final = result(ActionStatus.UNKNOWN, "invalid_submission_result", True)
                elif submitted.status is ActionStatus.ATTEMPTED:
                    final = self._verify(request)
                elif submitted.status is ActionStatus.FAILED:
                    final = result(ActionStatus.FAILED, "submission_failed", submitted.attempted)
                else:
                    final = result(ActionStatus.UNKNOWN, "submission_uncertain", True)
        self.state.finish(final)
        return final

    def _verify(self, request: ActionRequest) -> ActionResult:
        status, reason = ActionStatus.UNKNOWN, "verification_uncertain"
        try:
            verified = self.adapter.verify(request)
        except TimeoutError:
            reason = "verification_timeout"
        except Exception:
            reason = "verification_error"
        else:
            if isinstance(verified, ActionResult) and verified.request_id == request.request_id:
                if verified.status is ActionStatus.UI_CONFIRMED:
                    status, reason = ActionStatus.UI_CONFIRMED, "mock_ui_confirmed"
                elif verified.status is ActionStatus.FAILED:
                    status, reason = ActionStatus.FAILED, "verification_failed"
        return ActionResult(request.request_id, status, reason, attempted=True)
