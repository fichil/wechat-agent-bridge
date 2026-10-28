"""Exact profile-class topology only; no names, values, text or actions."""
from scripts.own_avatar_metadata import runtime
from wechat_agent_bridge.console_models import ConsoleError

PROFILE = 'mmui::ProfileUniquePop'


def collect(client, root, fp, handles, class_property):
    def record(node, expected_hwnd=None):
        if node.CurrentProcessId != fp['pid']:
            raise ConsoleError('own_profile_structure_foreign_node')
        hwnd = int(node.CurrentNativeWindowHandle)
        if expected_hwnd is not None and hwnd != expected_hwnd:
            raise ConsoleError('own_profile_structure_window_changed')
        rid = runtime(node)
        cls = node.CurrentClassName
        if not isinstance(cls,str) or len(cls)>160:
            raise ConsoleError('own_profile_structure_invalid_class')
        if node.CurrentProcessId != fp['pid'] or runtime(node)!=rid:
            raise ConsoleError('own_profile_structure_node_changed')
        return dict(runtime_id=list(rid),native_hwnd=hwnd,class_name=cls,
                    offscreen=bool(node.CurrentIsOffscreen))
    before = tuple(handles())
    if fp['hwnd'] not in before or len(before)>64 or len(set(before))!=len(before):
        raise ConsoleError('own_profile_structure_window_set_invalid')
    root_before=record(root,fp['hwnd'])
    if root_before['class_name']!='mmui::MainWindow':
        raise ConsoleError('own_profile_structure_main_changed')
    # Filter in UIA, not after obtaining an unrestricted text/element tree.
    condition=client._client.CreatePropertyCondition(class_property,PROFILE)
    matches=root.FindAll(4,condition)
    if not 0 <= matches.Length <= 4:
        raise ConsoleError('own_profile_structure_profile_limit')
    profiles=[]
    for index in range(matches.Length):
        item=record(matches.GetElement(index))
        if item['class_name']!=PROFILE:
            raise ConsoleError('own_profile_structure_filter_mismatch')
        profiles.append(item)
    top=[record(root if h==fp['hwnd'] else client.element_from_handle(h),h) for h in before]
    if tuple(handles())!=before or record(root,fp['hwnd'])!=root_before:
        raise ConsoleError('own_profile_structure_topology_changed')
    return dict(profile_class=PROFILE,profiles_under_main=profiles,top_level_classes=top,
                identity_verified=False,own_avatar_identity_verified=False,
                names_or_text_or_values_read=False,avatar_images_read=False,action_executed=False)


def worker(pipe,fp,_account):
    import sys
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from comtypes.gen import UIAutomationClient as uia
        client=BackgroundUIAClient()
        root=client.element_from_handle(fp['hwnd'])
        result=collect(client,root,fp,lambda:WindowsUIA._visible_process_window_handles(fp['pid']),
                       uia.UIA_ClassNamePropertyId)
        client.require_background_mode()
        pipe.send(dict(kind='navigation_metadata',metadata=result))
    except Exception as exc:
        pipe.send(dict(kind='error',reason=str(exc) if isinstance(exc,ConsoleError) else 'own_profile_structure_failed'))
    finally:pipe.close()
