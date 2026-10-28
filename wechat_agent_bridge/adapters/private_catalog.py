"""Bounded session metadata discovery; no message bodies or UI actions."""
from ..console_models import ConsoleError


def collect_candidates(client, uia, pid, hwnd):
    root = client.element_from_handle(hwnd)
    if root.CurrentProcessId != pid or root.CurrentNativeWindowHandle != hwnd:
        raise ConsoleError('private_catalog_root_mismatch')
    raw = client._client
    listing = root.FindAll(uia.TreeScope_Descendants,
        raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId, 'session_list'))
    if listing.Length != 1:
        raise ConsoleError('private_catalog_list_ambiguous')
    items = listing.GetElement(0).FindAll(uia.TreeScope_Children,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListItemControlTypeId))
    if not 0 <= items.Length <= 512:
        raise ConsoleError('private_catalog_row_limit')
    rows, seen = [], set()
    for i in range(items.Length):
        row = items.GetElement(i)
        if row.CurrentProcessId != pid:
            raise ConsoleError('private_catalog_process_mismatch')
        runtime = tuple(row.GetRuntimeId())
        if not runtime or runtime in seen:
            raise ConsoleError('private_catalog_row_identity_changed')
        seen.add(runtime)
        aid = row.CurrentAutomationId
        if not isinstance(aid, str) or not aid.startswith('session_item_'):
            continue
        # Name contains a message preview; never read it for catalog discovery.
        rows.append(dict(runtime_id=list(runtime), display_name=aid[len('session_item_'):][:200],
                         kind='unknown', wechat_id=None))
    client.require_background_mode()
    return dict(rows=rows, complete=False, limitation='loaded_session_rows_only',
                reason='private_identity_direction_navigation_unverified')
