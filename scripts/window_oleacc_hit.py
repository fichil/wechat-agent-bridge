"""Read-only numeric MSAA hit inspection; never names, values or actions."""
from scripts.window_oleacc import WindowOleaccPort
from scripts.own_avatar_hit_test import POINT, contains
from scripts.own_avatar_metadata import logical_rect, left_header
from wechat_agent_bridge.console_models import ConsoleError


class HitPort(WindowOleaccPort):
    def hit(self, obj, point):
        return obj.accHitTest(*point)

    def dispatch(self, obj):
        # Only interface conversion, not a property or default action.
        return obj.QueryInterface(self.interface)

    def numeric(self, obj, child):
        return obj.accRole[child], tuple(obj.accLocation(child))

    def identity(self, obj):
        from comtypes import IUnknown
        return int(self.c.cast(obj.QueryInterface(IUnknown), self.c.c_void_p).value or 0)


def scoped(port, obj):
    code, hwnd = port.owner(obj)
    if code != 0 or not hwnd:
        raise ConsoleError('oleacc_hit_object_owner_missing')
    port.require_window(hwnd)
    return hwnd


def resolve_hit(port, root, value):
    if value is None:
        return None, None, 'empty'
    if type(value) is int:
        if not 0 <= value <= 0x7fffffff:
            raise ConsoleError('oleacc_hit_child_id_invalid')
        return root, value, 'self' if value == 0 else 'child_id'
    # comtypes returns VT_DISPATCH as an IDispatch pointer. Only accept that
    # shape via explicit QueryInterface; bool/str/list cannot be child IDs.
    if isinstance(value, (bool, str, bytes, float, list, tuple, dict)):
        raise ConsoleError('oleacc_hit_variant_invalid')
    return port.dispatch(value), 0, 'object'


def inspect_hit(port, hwnd, geometry, *, max_depth=1):
    if type(max_depth) is not int or not 1 <= max_depth <= 6:
        raise ConsoleError('oleacc_hit_depth_invalid')
    before = geometry(hwnd)
    frame, dpi = before['frame'], before['dpi']
    logical_rect(frame, frame, dpi)
    point = tuple(round(frame[i] + POINT[i]*dpi/96) for i in (0, 1))
    if not contains(frame, point):
        raise ConsoleError('oleacc_hit_point_outside_root')
    port.require_window(hwnd)
    code, root = port.create(hwnd)
    if code != 0 or not root:
        raise ConsoleError('oleacc_hit_root_unavailable')
    root_hwnd = scoped(port, root)
    current = root
    seen = set()
    retained = [root]  # Keep COM objects alive while comparing IUnknown IDs.
    route = []
    result = dict(locator_point_logical=list(POINT),
                  own_avatar_identity_verified=False, names_or_text_or_values_read=False,
                  avatar_images_read=False, action_executed=False, precise_geometry=False)
    for depth in range(max_depth):
        current_id = port.identity(current) if max_depth > 1 else None
        if max_depth > 1:
            if type(current_id) is not int or current_id <= 0 or current_id in seen:
                raise ConsoleError('oleacc_hit_chain_cycle_or_invalid_identity')
            seen.add(current_id)
        scoped(port, current)
        value = port.hit(current, point)
        obj, child, kind = resolve_hit(port, current, value)
        result['hit_kind'] = kind
        if obj is None:
            result['precise_geometry'] = False
            break
        owner = scoped(port, obj)  # Must precede all returned-object properties.
        role, loc = port.numeric(obj, child)
        if type(role) is not int or not 1 <= role <= 0x7fffffff:
            raise ConsoleError('oleacc_hit_role_not_numeric')
        if len(loc) != 4 or any(type(v) is not int for v in loc) or loc[2] <= 0 or loc[3] <= 0:
            raise ConsoleError('oleacc_hit_location_invalid')
        rect = [loc[0], loc[1], loc[0]+loc[2], loc[1]+loc[3]]
        logical = logical_rect(frame, rect, dpi)
        if not contains(rect, point):
            raise ConsoleError('oleacc_hit_location_misses_point')
        if port.numeric(obj, child) != (role, loc) or scoped(port, obj) != owner:
            raise ConsoleError('oleacc_hit_object_changed')
        result.update(role=role, child_id=child, object_window_hwnd=owner,
                      physical_rect=rect, logical_rect=list(logical),
                      precise_geometry=left_header(logical))
        route.append(dict(hit_kind=kind, role=role, child_id=child, object_window_hwnd=owner,
                          logical_rect=list(logical), precise_geometry=left_header(logical)))
        if geometry(hwnd) != before:
            raise ConsoleError('oleacc_hit_root_changed')
        if kind != 'object' or max_depth == 1:
            break
        obj_id = port.identity(obj)
        if type(obj_id) is not int or obj_id <= 0:
            raise ConsoleError('oleacc_hit_chain_cycle_or_invalid_identity')
        if obj_id == current_id:
            result['termination'] = 'same_com_object'
            break
        if obj_id in seen:
            raise ConsoleError('oleacc_hit_chain_cycle_or_invalid_identity')
        if depth + 1 == max_depth:
            result.update(depth_limit_reached=True, precise_geometry=False)
            break
        retained.append(obj)
        current = obj
    result.update(route=route, max_depth=max_depth, hit_calls=depth+1)
    if scoped(port, root) != root_hwnd or geometry(hwnd) != before:
        raise ConsoleError('oleacc_hit_root_changed')
    port.require_window(hwnd)
    return result


def worker(pipe, fp, _account):
    import sys
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from scripts.inspect_contacts_navigation import physical_coordinate_context, window_geometry
        from scripts.development_window_restore import WindowPort
        with physical_coordinate_context():
            client = BackgroundUIAClient()
            root = client.element_from_handle(fp['hwnd'])
            def check():
                if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                        root.CurrentClassName != 'mmui::MainWindow'):
                    raise ConsoleError('oleacc_hit_selected_root_changed')
            check()
            window = WindowPort(fp['hwnd'])
            state = window.state()
            if not state['visible'] or state['iconic']:
                result = dict(geometry_inspection_usable=False, reason='selected_window_minimized_or_hidden')
            else:
                result = inspect_hit(HitPort(fp), fp['hwnd'], window_geometry, max_depth=6)
                result['geometry_inspection_usable'] = True
            check()
            if window.state() != state:
                raise ConsoleError('oleacc_hit_selected_window_state_changed')
            client.require_background_mode()
        pipe.send(dict(kind='navigation_metadata', metadata=dict(window_state=state,
            window_oleacc_hit=result, dpi_context_restored=True, own_avatar_identity_verified=False,
            names_or_text_or_values_read=False, avatar_images_read=False, action_executed=False)))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'window_oleacc_hit_failed'))
    finally:
        pipe.close()
