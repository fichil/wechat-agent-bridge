"""Acquire native MSAA only for freshly hit-tested, fixed coarse containers.

This first slice checks the public GetIAccessible boundary. It deliberately
does not query accName/accValue, hit-test arbitrary windows or invoke actions.
"""
from wechat_agent_bridge.console_models import ConsoleError
from scripts.own_avatar_metadata import runtime

ALLOWED = {'MMUIRenderSubWindowHW': 50033, 'mmui::MainTabBar': 50021}


def probe(node, record, fp, uia):
    cls = record.get('class_name')
    if cls not in ALLOWED or record.get('control_type') != ALLOWED[cls]:
        return dict(status='outside_fixed_container_scope', interface_acquired=False, action_executed=False)
    expected = record.get('runtime_id')
    def check():
        if (node.CurrentProcessId != fp['pid'] or node.CurrentClassName != cls or
                int(node.CurrentControlType) != ALLOWED[cls] or list(runtime(node)) != expected):
            raise ConsoleError('own_msaa_container_changed')
    check()
    if record.get('patterns', {}).get('LegacyIAccessible') is not True:
        return dict(status='legacy_pattern_not_declared', interface_acquired=False, action_executed=False)
    stage = 'legacy_pattern'
    try:
        pattern = node.GetCurrentPattern(uia.UIA_LegacyIAccessiblePatternId).QueryInterface(uia.IUIAutomationLegacyIAccessiblePattern)
        check()
        stage = 'get_iaccessible'
        accessible = pattern.GetIAccessible()
        available = bool(accessible)
        result = dict(status='native_interface_available' if available else 'native_interface_null',
                      interface_acquired=available, action_executed=False)
    except ConsoleError:
        raise
    except Exception as exc:
        result = dict(status='interface_query_failed', stage=stage, interface_acquired=False,
                      action_executed=False)
        code = getattr(exc, 'hresult', None)
        if type(code) is int:
            result['hresult'] = code
    check()
    return result


def collect_fixed_containers(root, raw, uia, fp):
    """Interface presence does not require a visible pixel or a popup card."""
    def check_root():
        if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                root.CurrentClassName != 'mmui::MainWindow'):
            raise ConsoleError('own_msaa_root_changed')
    check_root()
    records = []
    for cls, role in ALLOWED.items():
        matches = root.FindAll(uia.TreeScope_Descendants,
                              raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, cls))
        if type(matches.Length) is not int or not 0 <= matches.Length <= 4:
            raise ConsoleError('own_msaa_container_limit')
        record = dict(class_name=cls, container_count=matches.Length, identity_verified=False)
        if matches.Length != 1:
            record['native_msaa'] = dict(status='container_not_unique_or_unavailable', interface_acquired=False)
        else:
            node = matches.GetElement(0)
            if node.CurrentProcessId != fp['pid'] or node.CurrentClassName != cls or int(node.CurrentControlType) != role:
                raise ConsoleError('own_msaa_container_changed')
            record.update(runtime_id=list(runtime(node)), control_type=role)
            value = node.GetCurrentPropertyValue(uia.UIA_IsLegacyIAccessiblePatternAvailablePropertyId)
            record['patterns'] = dict(LegacyIAccessible=bool(value) if type(value) in (bool, int) and value in (0, 1) else None)
            record['native_msaa'] = probe(node, record, fp, uia)
        records.append(record)
        check_root()
    return dict(scope='fixed_container_interface_presence_only', containers=records, max_containers=2,
                normal_geometry_required=False, own_avatar_identity_verified=False,
                names_or_text_or_values_read=False, avatar_images_read=False, action_executed=False)


def worker(pipe, fp, _account):
    import sys
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from comtypes.gen import UIAutomationClient as uia
        from scripts.inspect_contacts_navigation import physical_coordinate_context
        from scripts.development_window_restore import WindowPort
        with physical_coordinate_context():
            client = BackgroundUIAClient()
            root = client.element_from_handle(fp['hwnd'])
            port = WindowPort(fp['hwnd'])
            state = port.state()
            result = collect_fixed_containers(root, client._client, uia, fp)
            if port.state() != state:
                raise ConsoleError('own_msaa_window_state_changed')
            client.require_background_mode()
        pipe.send(dict(kind='navigation_metadata', metadata=dict(window_state=state, native_msaa_probe=result,
            dpi_context_restored=True, own_avatar_identity_verified=False,
            names_or_text_or_values_read=False, avatar_images_read=False, action_executed=False)))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'own_msaa_probe_failed'))
    finally:
        pipe.close()
