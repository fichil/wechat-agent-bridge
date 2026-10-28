"""Content-free structure of the fixed MainTabBar; no UI actions or labels."""
import re
from wechat_agent_bridge.console_models import ConsoleError

NAV = 'mmui::MainTabBar'
MAIN_VIEW = 'mmui::MainView'


def main_view_children(root, raw, uia, fp):
    """Only direct MainView children; never descend into chat surfaces."""
    if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
            root.CurrentClassName != 'mmui::MainWindow'):
        raise ConsoleError('own_navigation_root_changed')
    matches = root.FindAll(uia.TreeScope_Descendants,
                          raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, MAIN_VIEW))
    if type(matches.Length) is not int or not 0 <= matches.Length <= 1:
        raise ConsoleError('own_navigation_main_view_ambiguous')
    if not matches.Length:
        return dict(main_view_count=0, children=[], avatar_identity_verified=False)
    view = matches.GetElement(0)
    rid = tuple(view.GetRuntimeId())
    if view.CurrentProcessId != fp['pid'] or view.CurrentClassName != MAIN_VIEW or not rid:
        raise ConsoleError('own_navigation_main_view_changed')
    children = view.FindAll(uia.TreeScope_Children, raw.CreateTrueCondition())
    if type(children.Length) is not int or not 0 <= children.Length <= 32:
        raise ConsoleError('own_navigation_child_limit')
    result, seen = [], set()
    for index in range(children.Length):
        node = children.GetElement(index)
        child_rid = tuple(node.GetRuntimeId())
        if node.CurrentProcessId != fp['pid'] or not 1 <= len(child_rid) <= 16 or child_rid in seen:
            raise ConsoleError('own_navigation_node_changed')
        seen.add(child_rid)
        cls = node.CurrentClassName
        if not isinstance(cls, str) or not re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}', cls):
            cls = 'other'
        value = node.GetCurrentPropertyValue(uia.UIA_IsInvokePatternAvailablePropertyId)
        result.append(dict(index=index, class_name=cls, runtime_id=list(child_rid),
                           control_type=int(node.CurrentControlType),
                           invoke_available=bool(value) if type(value) in (bool, int) and value in (0, 1) else None))
        if tuple(node.GetRuntimeId()) != child_rid or node.CurrentProcessId != fp['pid']:
            raise ConsoleError('own_navigation_node_changed')
    if (tuple(view.GetRuntimeId()) != rid or view.CurrentProcessId != fp['pid'] or
            root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd']):
        raise ConsoleError('own_navigation_main_view_changed')
    return dict(main_view_count=1, runtime_id=list(rid), children=result, avatar_identity_verified=False)


def collect(root, raw, uia, fp):
    def verify_root():
        if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                root.CurrentClassName != 'mmui::MainWindow'):
            raise ConsoleError('own_navigation_root_changed')
    verify_root()
    matches = root.FindAll(uia.TreeScope_Descendants, raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, NAV))
    if type(matches.Length) is not int or matches.Length != 1:
        raise ConsoleError('own_navigation_container_ambiguous')
    nav = matches.GetElement(0)
    nav_rid = tuple(nav.GetRuntimeId())
    if nav.CurrentProcessId != fp['pid'] or nav.CurrentClassName != NAV or not nav_rid:
        raise ConsoleError('own_navigation_container_changed')
    true = raw.CreateTrueCondition()
    queue = [(nav, [])]
    records, seen = [], set()
    while queue:
        node, path = queue.pop(0)
        rid = tuple(node.GetRuntimeId())
        if (node.CurrentProcessId != fp['pid'] or not 1 <= len(rid) <= 16 or
                any(type(x) is not int for x in rid) or rid in seen):
            raise ConsoleError('own_navigation_node_changed')
        seen.add(rid)
        if len(seen) > 64:
            raise ConsoleError('own_navigation_node_limit')
        classname = node.CurrentClassName
        if not isinstance(classname, str) or not re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}', classname):
            classname = 'other'
        patterns = {}
        for name in ('Invoke', 'SelectionItem', 'LegacyIAccessible'):
            value = node.GetCurrentPropertyValue(getattr(uia, 'UIA_Is' + name + 'PatternAvailablePropertyId'))
            patterns[name] = bool(value) if type(value) in (bool, int) and value in (0, 1) else None
        records.append(dict(path=path, runtime_id=list(rid), class_name=classname,
                            control_type=int(node.CurrentControlType), patterns=patterns,
                            enabled=bool(node.CurrentIsEnabled), offscreen=bool(node.CurrentIsOffscreen)))
        if len(path) < 2:
            children = node.FindAll(uia.TreeScope_Children, true)
            if type(children.Length) is not int or not 0 <= children.Length <= 32:
                raise ConsoleError('own_navigation_child_limit')
            queue.extend((children.GetElement(i), path + [i]) for i in range(children.Length))
        if tuple(node.GetRuntimeId()) != rid or node.CurrentProcessId != fp['pid']:
            raise ConsoleError('own_navigation_node_changed')
    if tuple(nav.GetRuntimeId()) != nav_rid:
        raise ConsoleError('own_navigation_container_changed')
    verify_root()
    return dict(navigation_runtime_id=list(nav_rid), nodes=records, max_depth=2, max_nodes=64,
                names_or_text_or_values_read=False, avatar_images_read=False, action_executed=False,
                own_avatar_identity_verified=False)


def worker(pipe, fp, _account):
    import sys
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from comtypes.gen import UIAutomationClient as uia
        from scripts.inspect_contacts_navigation import physical_coordinate_context, window_geometry
        from scripts.own_avatar_metadata import collect_for_window_state
        from scripts.development_window_restore import WindowPort
        with physical_coordinate_context():
            client = BackgroundUIAClient()
            root = client.element_from_handle(fp['hwnd'])
            window_port = WindowPort(fp['hwnd'])
            state = window_port.state()
            result = collect(root, client._client, uia, fp)
            result['window_state'] = state
            result['avatar_metadata'] = collect_for_window_state(root, client._client, uia, fp, window_geometry, state)
            if window_port.state() != state:
                raise ConsoleError('own_avatar_window_state_changed')
            client.require_background_mode()
        result.update(dpi_context='per_monitor_aware_v2', dpi_context_restored=True)
        pipe.send(dict(kind='navigation_metadata', metadata=result))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'own_navigation_metadata_failed'))
    finally:
        pipe.close()
