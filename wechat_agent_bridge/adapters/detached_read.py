"""Read-only semantic binding for an explicitly selected detached group window.

The source account/process is verified by WindowsAdapter. An exact root
AutomationId (observed when opening that bound group's window) is mandatory;
neither a nickname nor an arbitrary HWND is sufficient. No text entry here.
"""
import re
from ..console_models import ConsoleError


def require_selection(source_hwnd, hwnd, root_id):
    if (type(hwnd) is not int or not 0 < hwnd < 2**64 or hwnd == source_hwnd or
            not isinstance(root_id, str) or
            re.fullmatch(r'ChatSingleWindow[0-9]{5,24}@chatroom', root_id) is None):
        raise ConsoleError('detached_group_selection_invalid')


def check_metadata(expected, actual):
    for key in ('window', 'pid', 'automation_id', 'root_runtime_id', 'input_runtime_id', 'header_runtime_id'):
        if not expected.get(key) or actual.get(key) != expected[key]:
            raise ConsoleError('detached_group_binding_changed')


def metadata(client, uia, pid, hwnd, root_id, name):
    raw, root = client._client, client.element_from_handle(hwnd)
    # Report only the failed property, never an unrelated title or chat ID.
    for field, expected in (('ProcessId', pid), ('NativeWindowHandle', hwnd), ('AutomationId', root_id)):
        if getattr(root, 'Current'+field) != expected:
            raise ConsoleError('detached_root_'+field.lower()+'_changed')
    def one(condition):
        items = root.FindAll(uia.TreeScope_Descendants, condition)
        if items.Length != 1:
            raise ConsoleError('detached_group_control_ambiguous')
        item = items.GetElement(0)
        if item.CurrentProcessId != pid:
            raise ConsoleError('detached_group_binding_changed')
        return item
    edit = one(raw.CreateAndCondition(
        raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId, 'chat_input_field'),
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_EditControlTypeId)))
    header = one(raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::ChatTitleBarChatRoomView'))
    # Window.Name is not the conversation title on this client. Match the exact
    # title label inside the already-bound chat-id root, never an arbitrary name.
    title = one(raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId,
        'content_view.top_content_view.title_h_view.left_v_view.left_content_v_view.left_ui_.'
        'big_title_line_h_view.current_chat_name_label'))
    if title.CurrentName != name:
        raise ConsoleError('detached_group_title_changed')
    # Draft content is deliberately not requested for a read-only channel.
    client.require_background_mode()
    return dict(window=hwnd, pid=pid, automation_id=root_id, root_runtime_id=list(root.GetRuntimeId()),
                input_runtime_id=list(edit.GetRuntimeId()), header_runtime_id=list(header.GetRuntimeId()))
