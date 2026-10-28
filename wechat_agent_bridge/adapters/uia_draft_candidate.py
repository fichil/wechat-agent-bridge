"""Opt-in, single-draft research candidate using a fresh no-focus UIA client.

This module is not registered as a production adapter. The caller owns process
fingerprints, account/conversation authorization, desktop event observation,
worker timeouts and persistent quota/deduplication. Checkpoints must revalidate
    those conditions and return exactly True. No send, retry or draft cleanup exists.
"""
from dataclasses import dataclass

from .uia_client import BackgroundUIAClient


FIXED_DRAFT = "微信 Agent Bridge M1 验证"
INPUT_AUTOMATION_ID = "chat_input_field"


@dataclass(frozen=True)
class DraftTarget:
    hwnd: int
    pid: int
    root_runtime_id: tuple[int, ...]
    input_runtime_id: tuple[int, ...]


class _Rejected(Exception):
    pass


def _bindings():
    from comtypes.gen import UIAutomationClient
    return UIAutomationClient


def _runtime_id(value):
    try:
        result = tuple(value)
    except (TypeError, ValueError):
        raise _Rejected("candidate_runtime_id_invalid") from None
    if not 1 <= len(result) <= 16 or any(type(item) is not int or not -(2**31) <= item < 2**31
                                       for item in result):
        raise _Rejected("candidate_runtime_id_invalid")
    return result


def _validate_target(target):
    if (not isinstance(target, DraftTarget) or type(target.hwnd) is not int or
            not 0 < target.hwnd < 2**64 or type(target.pid) is not int or not 0 < target.pid < 2**32):
        raise _Rejected("candidate_target_invalid")
    if (type(target.root_runtime_id) is not tuple or type(target.input_runtime_id) is not tuple or
            _runtime_id(target.root_runtime_id) == _runtime_id(target.input_runtime_id)):
        raise _Rejected("candidate_target_invalid")


def _identity(root, element, target, bindings):
    # Do not read a value or obtain a pattern from a mismatched process/element.
    if root.CurrentProcessId != target.pid or root.CurrentNativeWindowHandle != target.hwnd:
        raise _Rejected("candidate_root_mismatch")
    if _runtime_id(root.GetRuntimeId()) != target.root_runtime_id:
        raise _Rejected("candidate_root_runtime_changed")
    if element is None:
        return
    if (element.CurrentProcessId != target.pid or element.CurrentAutomationId != INPUT_AUTOMATION_ID or
            element.CurrentControlType != bindings.UIA_EditControlTypeId):
        raise _Rejected("candidate_input_mismatch")
    if _runtime_id(element.GetRuntimeId()) != target.input_runtime_id:
        raise _Rejected("candidate_input_runtime_changed")


def _checkpoint(callback, phase):
    try:
        allowed = callback(phase)
    except Exception:
        raise _Rejected("candidate_checkpoint_failed") from None
    if allowed is not True:
        raise _Rejected("candidate_checkpoint_rejected")


def _empty(pattern):
    readonly = pattern.CurrentIsReadOnly
    if type(readonly) not in (bool, int) or readonly != 0:
        raise _Rejected("candidate_input_readonly_or_unknown")
    if pattern.CurrentValue != "":
        raise _Rejected("candidate_existing_draft_or_unknown")


def _pattern_guard(callback, raw, element, pattern):
    if callback is None:
        return
    try:
        accepted = callback(raw, element, pattern)
    except Exception:
        raise _Rejected('candidate_pattern_context_rejected') from None
    if accepted is not True:
        raise _Rejected('candidate_pattern_context_rejected')


def write_fixed_draft(target, *, enabled=False, checkpoint=None, claim=None,
                      client_factory=None, bindings=None, pattern_guard=None):
    """Set one fixed draft, leaving it untouched even after an uncertain result.

    ``client_factory`` constructs the raw CUIAutomation8/IUIAutomation2 object;
    it exists for synthetic dependency injection. Elements and patterns are
    obtained exclusively from its fresh BackgroundUIAClient wrapper. ``claim``
    must durably consume the one-shot diagnostic allowance and return True; it
    receives no message text. A raised/uncertain claim is never retried here.

    ``readback_confirmed`` means exact draft readback with successful surrounding
    checkpoints, not verified background compatibility or recipient delivery.
    The caller must preserve the durable claim if its worker is killed/hangs.
    ``commit_write`` must be a lightweight check of current authorization and
    latched desktop/takeover events, with no UIA discovery or other UI actions.
    An optional trusted ``pattern_guard`` validates the exact held raw client,
    element and pattern before claiming and before the final commit checkpoint.
    It confers no account, target, write or quota authority.
    """
    result = {"status": "disabled", "reason": "candidate_disabled", "attempted": False,
              "claim_attempted": False, "claim_confirmed": False, "readback_equal": None,
              "live_compatible": False}
    if enabled is not True:
        return result
    result.update(status="failed", reason="candidate_operation_failed")
    try:
        _validate_target(target)
        if not callable(checkpoint) or not callable(claim):
            raise _Rejected("candidate_callbacks_required")
        _checkpoint(checkpoint, "before_discovery")
        client = BackgroundUIAClient(client_factory)
        constants = bindings if bindings is not None else _bindings()
        root = client.element_from_handle(target.hwnd)
        _identity(root, None, target, constants)
        # Conditions are created by this same newly configured COM client.
        raw = client._client
        condition = raw.CreateAndCondition(
            raw.CreatePropertyCondition(constants.UIA_AutomationIdPropertyId, INPUT_AUTOMATION_ID),
            raw.CreatePropertyCondition(constants.UIA_ControlTypePropertyId, constants.UIA_EditControlTypeId))
        matches = root.FindAll(constants.TreeScope_Descendants, condition)
        if type(matches.Length) is not int or matches.Length != 1:
            raise _Rejected("candidate_input_ambiguous_or_missing")
        element = matches.GetElement(0)
        _identity(root, element, target, constants)
        _checkpoint(checkpoint, "after_discovery")
        _identity(root, element, target, constants)
        pattern = element.GetCurrentPattern(constants.UIA_ValuePatternId).QueryInterface(
            constants.IUIAutomationValuePattern)
        _empty(pattern)
        _checkpoint(checkpoint, "after_read")
        _identity(root, element, target, constants)
        client.require_background_mode()
        _pattern_guard(pattern_guard, raw, element, pattern)
        _checkpoint(checkpoint, "before_claim")
        result["claim_attempted"] = True
        try:
            claimed = claim()
        except Exception:
            raise _Rejected("candidate_claim_failed") from None
        if claimed is not True:
            raise _Rejected("candidate_claim_not_confirmed")
        result["claim_confirmed"] = True
        _checkpoint(checkpoint, "before_write")
        _identity(root, element, target, constants)
        _empty(pattern)
        client.require_background_mode()
        _pattern_guard(pattern_guard, raw, element, pattern)
        _checkpoint(checkpoint, "commit_write")
        result["attempted"] = True  # Set before COM: an exception may still have written.
        result["status"] = "attempted"
        pattern.SetValue(FIXED_DRAFT)
        _checkpoint(checkpoint, "after_write")
        _identity(root, element, target, constants)
        client.require_background_mode()
        result["readback_equal"] = pattern.CurrentValue == FIXED_DRAFT
        _checkpoint(checkpoint, "after_readback")
        _identity(root, element, target, constants)
        client.require_background_mode()
        if result["readback_equal"] is not True:
            raise _Rejected("candidate_readback_mismatch")
        result.update(status="readback_confirmed", reason="candidate_fixed_draft_read_back")
    except Exception as exc:
        result["status"] = "unknown" if result["attempted"] else "failed"
        result["reason"] = str(exc) if isinstance(exc, _Rejected) else "candidate_operation_failed"
    return result
