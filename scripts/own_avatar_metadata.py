"""Bounded XAvatarImage geometry and ancestry; never reads image or text."""
import math
import re
from wechat_agent_bridge.console_models import ConsoleError

AVATAR = 'mmui::XAvatarImage'
OWN_CLASSES = (AVATAR, 'mmui::MainView')


def safe_class(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}', value) else 'other'


def runtime(node):
    rid = tuple(node.GetRuntimeId())
    if not 1 <= len(rid) <= 16 or any(type(v) is not int for v in rid):
        raise ConsoleError('own_avatar_runtime_invalid')
    return rid


def logical_rect(frame, rect, dpi):
    if type(dpi) is not int or not 96 <= dpi <= 480:
        raise ConsoleError('own_avatar_dpi_invalid')
    for value in (frame, rect):
        if (not isinstance(value, (list, tuple)) or len(value) != 4 or
                any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 100000 for v in value) or
                not value[0] < value[2] or not value[1] < value[3]):
            raise ConsoleError('own_avatar_geometry_invalid')
    scale = 96 / dpi
    return [(rect[0]-frame[0])*scale, (rect[1]-frame[1])*scale,
            (rect[2]-frame[0])*scale, (rect[3]-frame[1])*scale]


def left_header(rect):
    left, top, right, bottom = rect
    return 0 <= left < right <= 80 and 0 <= top < bottom <= 120 and 16 <= right-left <= 80 and 16 <= bottom-top <= 80


def ancestry(node, raw, fp):
    route, seen = [], set()
    for _ in range(6):
        node = raw.ControlViewWalker.GetParentElement(node)
        if node is None:
            break
        if node.CurrentProcessId != fp['pid']:
            break
        rid = runtime(node)
        if rid in seen:
            raise ConsoleError('own_avatar_parent_cycle')
        seen.add(rid)
        cls = safe_class(node.CurrentClassName)
        route.append(dict(class_name=cls, runtime_id=list(rid)))
        if cls == 'mmui::MainWindow' and node.CurrentNativeWindowHandle == fp['hwnd']:
            return dict(bound_main_ancestor=True, route=route)
    return dict(bound_main_ancestor=False, route=route)


def collect(root, raw, uia, fp, geometry):
    from scripts.inspect_contacts_navigation import rectangle
    def verify_root():
        if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                root.CurrentClassName != 'mmui::MainWindow'):
            raise ConsoleError('own_avatar_root_changed')
    verify_root()
    before = geometry(fp['hwnd'])
    conditions = [raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, cls) for cls in OWN_CLASSES]
    conditions += [raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, role)
                   for role in (uia.UIA_ButtonControlTypeId, uia.UIA_ImageControlTypeId)]
    condition = conditions[0]
    for item in conditions[1:]:
        condition = raw.CreateOrCondition(condition, item)
    matches = root.FindAll(uia.TreeScope_Descendants, condition)
    if type(matches.Length) is not int or not 0 <= matches.Length <= 64:
        raise ConsoleError('own_avatar_node_limit')
    candidates, seen = [], set()
    class_counts = {cls: 0 for cls in OWN_CLASSES}
    other_role_nodes = 0
    for index in range(matches.Length):
        node = matches.GetElement(index)
        rid = runtime(node)
        cls = node.CurrentClassName
        role = int(node.CurrentControlType)
        if (node.CurrentProcessId != fp['pid'] or rid in seen or
                cls not in OWN_CLASSES and role not in (uia.UIA_ButtonControlTypeId, uia.UIA_ImageControlTypeId)):
            raise ConsoleError('own_avatar_node_changed')
        if cls in class_counts:
            class_counts[cls] += 1
        else:
            other_role_nodes += 1
        seen.add(rid)
        if bool(node.CurrentIsOffscreen):
            continue
        rect = rectangle(node)
        logical = logical_rect(before['frame'], rect, before['dpi'])
        if left_header(logical):
            owner = ancestry(node, raw, fp)
            value = node.GetCurrentPropertyValue(uia.UIA_IsInvokePatternAvailablePropertyId)
            candidates.append(dict(runtime_id=list(rid), class_name=safe_class(cls),
                control_type=role, physical_rect=rect,
                logical_rect=[round(v, 3) for v in logical],
                invoke_available=bool(value) if type(value) in (bool, int) and value in (0, 1) else None,
                enabled=bool(node.CurrentIsEnabled), **owner))
            if rectangle(node) != rect:
                raise ConsoleError('own_avatar_geometry_changed')
        if runtime(node) != rid or node.CurrentProcessId != fp['pid']:
            raise ConsoleError('own_avatar_node_changed')
    verify_root()
    if geometry(fp['hwnd']) != before:
        raise ConsoleError('own_avatar_window_geometry_changed')
    return dict(avatar_nodes_seen=matches.Length, class_counts=class_counts,
                other_button_or_image_nodes=other_role_nodes,
                max_avatar_nodes=64, max_parent_depth=6,
                window_geometry=before, left_header_candidates=candidates,
                own_avatar_identity_verified=False, names_or_text_or_values_read=False,
                avatar_images_read=False, action_executed=False)


def collect_for_window_state(root, raw, uia, fp, geometry, state):
    if (not isinstance(state, dict) or type(state.get('visible')) is not bool or
            type(state.get('iconic')) is not bool):
        raise ConsoleError('own_avatar_window_state_unknown')
    if not state['visible'] or state['iconic']:
        return dict(geometry_inspection_usable=False, reason='selected_window_minimized_or_hidden',
                    own_avatar_identity_verified=False, names_or_text_or_values_read=False,
                    avatar_images_read=False, action_executed=False)
    return dict(collect(root, raw, uia, fp, geometry), geometry_inspection_usable=True)
