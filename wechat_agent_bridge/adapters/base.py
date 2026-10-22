from typing import Protocol

from ..domain import ActionRequest, ActionResult, MessageEvent, Target, TargetState


class AdapterRejected(RuntimeError):
    """An adapter can prove that no write was attempted."""


class Adapter(Protocol):
    simulation_only: bool

    def inspect(self, target: Target) -> TargetState: ...

    def read(self, target: Target) -> tuple[MessageEvent, ...]: ...

    def submit(self, request: ActionRequest) -> ActionResult: ...

    def verify(self, request: ActionRequest) -> ActionResult: ...
