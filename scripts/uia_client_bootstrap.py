"""Client-first bootstrap and fail-closed pattern context guard for research.

No real client is selected here. Callers supply an already authorized window
and expected input RuntimeId, and own account/target/draft/quota checks.
"""
import ctypes as c
import sys

from scripts.uia_context_evidence import inspect_context
from scripts.uia_context_native import SelfMemory
from wechat_agent_bridge.console_models import ConsoleError


def prepare_client_elements(fp, expected):
    """Create/configure the requested client before any optional legacy reader.

    Discovery reads only process/control/runtime identity. No draft value or
    context field is read until the controller verifies the selected test group.
    """
    from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
    from comtypes.gen import UIAutomationClient as uia
    legacy_loaded = 'pywinauto' in sys.modules
    client = BackgroundUIAClient()
    raw, root = client._client, client.element_from_handle(fp['hwnd'])
    if root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd']:
        raise ConsoleError('inspection_root_mismatch')
    condition = raw.CreateAndCondition(
        raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId, 'chat_input_field'),
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_EditControlTypeId))
    matches = root.FindAll(uia.TreeScope_Descendants, condition)
    if type(matches.Length) is not int or matches.Length != 1:
        raise ConsoleError('inspection_input_ambiguous')
    element = matches.GetElement(0)
    if (element.CurrentProcessId != fp['pid'] or element.CurrentAutomationId != 'chat_input_field' or
            element.CurrentControlType != uia.UIA_EditControlTypeId or
            tuple(element.GetRuntimeId()) != tuple(expected['input_runtime_id'])):
        raise ConsoleError('inspection_input_identity_mismatch')
    pattern = element.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    return client, root, element, pattern, legacy_loaded, uia


def read_pattern_context(raw, element, pattern):
    memory = SelfMemory()
    try:
        value = inspect_context(memory, memory.image,
            c.cast(raw, c.c_void_p).value, c.cast(element, c.c_void_p).value,
            c.cast(pattern, c.c_void_p).value, False)
        memory.validate_source()
        return value
    finally:
        memory.close()


def require_safe_context(value):
    if (not isinstance(value, dict) or value.get('stable') is not True or
            value.get('association') != 'present' or
            value.get('requested_auto_focus') is not False or
            value.get('associated_auto_focus') is not False or
            value.get('matches_requested_client') is not True or
            value.get('matches_requested_context_body') is not True):
        raise ConsoleError('candidate_associated_auto_focus_unsafe')
    return True
