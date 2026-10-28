"""Read-only RawViewWalker hit test inside the selected main window.

The fixed own-header point is a discovery locator, never an identity or an
input coordinate. No Name/Text/Value/image, focus or action API is called.
"""
from wechat_agent_bridge.console_models import ConsoleError
from scripts.own_avatar_metadata import runtime, safe_class, logical_rect, left_header
from scripts.inspect_contacts_navigation import rectangle

POINT = (31, 59)
MAX_NODES = 64
MAX_DEPTH = 8


def contains(rect, point):
    return rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]


def collect(root, raw, uia, fp, geometry):
    def verify_root():
        if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                root.CurrentClassName != 'mmui::MainWindow'):
            raise ConsoleError('own_hit_root_changed')
    verify_root()
    before = geometry(fp['hwnd'])
    # Validate the frame and DPI through the same tested geometry converter.
    logical_rect(before['frame'], before['frame'], before['dpi'])
    point = (before['frame'][0] + POINT[0]*before['dpi']/96,
             before['frame'][1] + POINT[1]*before['dpi']/96)
    if not contains(rectangle(root), point):
        raise ConsoleError('own_hit_root_geometry_unusable')
    walker = raw.RawViewWalker
    queue = [(root, [], 0, runtime(root))]
    seen, leaves = set(), []
    checked = 0
    while queue:
        node, route, depth, expected_rid = queue.pop(0)
        rid = runtime(node)
        if rid != expected_rid:
            raise ConsoleError('own_hit_node_changed')
        if rid in seen or node.CurrentProcessId != fp['pid']:
            raise ConsoleError('own_hit_path_cycle_or_foreign_node')
        seen.add(rid)
        rect = rectangle(node)
        logical = logical_rect(before['frame'], rect, before['dpi'])
        record = dict(runtime_id=list(rid), class_name=safe_class(node.CurrentClassName),
                      control_type=int(node.CurrentControlType), physical_rect=rect,
                      logical_rect=[round(value, 3) for value in logical])
        next_route = route + [record]
        matching = []
        if depth < MAX_DEPTH:
            child = walker.GetFirstChildElement(node)
            sibling_seen = set()
            while child:
                checked += 1
                if checked > MAX_NODES:
                    raise ConsoleError('own_hit_node_limit')
                if child.CurrentProcessId == fp['pid']:
                    child_rid = runtime(child)
                    if child_rid in sibling_seen:
                        raise ConsoleError('own_hit_sibling_cycle')
                    sibling_seen.add(child_rid)
                    # Bounds are read before entering a subtree. A chat pane
                    # outside the own-header point is never descended into.
                    child_rect = rectangle(child)
                    if not child.CurrentIsOffscreen and contains(child_rect, point):
                        if child_rid in seen:
                            raise ConsoleError('own_hit_path_cycle_or_foreign_node')
                        matching.append((child, next_route, depth+1, child_rid))
                        if len(matching) > 8:
                            raise ConsoleError('own_hit_overlapping_branch_limit')
                child = walker.GetNextSiblingElement(child)
        if matching:
            queue.extend(matching)
        else:
            patterns = {}
            for name in ('Invoke', 'LegacyIAccessible'):
                value = node.GetCurrentPropertyValue(getattr(uia, 'UIA_Is'+name+'PatternAvailablePropertyId'))
                patterns[name] = bool(value) if type(value) in (bool, int) and value in (0, 1) else None
            leaves.append(dict(**record, route=next_route, patterns=patterns,
                precise_geometry=left_header(logical) and depth < MAX_DEPTH,
                depth_limit_reached=depth == MAX_DEPTH, identity_verified=False))
        if runtime(node) != rid or node.CurrentProcessId != fp['pid'] or rectangle(node) != rect:
            raise ConsoleError('own_hit_node_changed')
    verify_root()
    if geometry(fp['hwnd']) != before:
        raise ConsoleError('own_hit_window_geometry_changed')
    return dict(locator_point_logical=list(POINT), locator_is_identity=False,
                nodes_checked=checked, max_nodes=MAX_NODES, max_depth=MAX_DEPTH,
                window_geometry=before, leaves=leaves, geometry_inspection_usable=True,
                own_avatar_identity_verified=False, names_or_text_or_values_read=False,
                avatar_images_read=False, action_executed=False)


def worker(pipe, fp, _account):
    import sys
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from comtypes.gen import UIAutomationClient as uia
        from scripts.inspect_contacts_navigation import physical_coordinate_context, window_geometry
        from scripts.development_window_restore import WindowPort
        with physical_coordinate_context():
            client = BackgroundUIAClient()
            root = client.element_from_handle(fp['hwnd'])
            port = WindowPort(fp['hwnd'])
            state = port.state()
            if not state['visible'] or state['iconic']:
                result = dict(geometry_inspection_usable=False, reason='selected_window_minimized_or_hidden',
                    own_avatar_identity_verified=False, names_or_text_or_values_read=False,
                    avatar_images_read=False, action_executed=False)
            else:
                result = collect(root, client._client, uia, fp, window_geometry)
            if port.state() != state:
                raise ConsoleError('own_hit_window_state_changed')
            client.require_background_mode()
        pipe.send(dict(kind='navigation_metadata', metadata=dict(window_state=state, hit_test=result,
            dpi_context_restored=True, own_avatar_identity_verified=False, names_or_text_or_values_read=False,
            avatar_images_read=False, action_executed=False)))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'own_hit_metadata_failed'))
    finally:
        pipe.close()
