"""Read only the fixed Contacts entry inside MainTabBar; no chat or action."""
import argparse
import ctypes as c
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import inspect_bound_search as readonly
from wechat_agent_bridge.console_models import ConsoleError

NAV_CLASS='mmui::MainTabBar'
PAGE_CLASSES=('mmui::ContactsTableBaseView','mmui::ContactsCellClassifyView','mmui::ContactsCellNewFriendView')


@contextmanager
def physical_coordinate_context(user=None):
    # Only this diagnostic worker thread changes; Windows display settings and
    # the target process are untouched. Never correct provider data by guessing
    # a scale factor from a nearly matching rectangle.
    if user is None:
        user=c.WinDLL('user32',use_last_error=True)
        user.SetThreadDpiAwarenessContext.argtypes=[c.c_void_p]
        user.SetThreadDpiAwarenessContext.restype=c.c_void_p
        user.GetThreadDpiAwarenessContext.restype=c.c_void_p
        user.AreDpiAwarenessContextsEqual.argtypes=[c.c_void_p,c.c_void_p]
        user.AreDpiAwarenessContextsEqual.restype=c.c_int
    old=user.SetThreadDpiAwarenessContext(-4)
    if not old:raise ConsoleError('contacts_dpi_context_unavailable')
    try:
        if not user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(),-4):
            raise ConsoleError('contacts_dpi_context_unconfirmed')
        yield
    finally:
        if not user.SetThreadDpiAwarenessContext(old) or not user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(),old):
            raise ConsoleError('contacts_dpi_context_restore_failed')


def safe_shortcut(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:(?:Ctrl|Control|Alt|Shift)\+){1,3}[A-Z0-9]',value):return None
    return value


def logical_navigation_point(frame,item,nav,dpi):
    if type(dpi) is not int or not 96<=dpi<=480:raise ConsoleError('contacts_geometry_dpi_invalid')
    for rect in (frame,item,nav):
        if (not isinstance(rect,(list,tuple)) or len(rect)!=4 or any(type(v) not in (int,float) for v in rect) or
            not -100000<=rect[0]<rect[2]<=100000 or not -100000<=rect[1]<rect[3]<=100000):
            raise ConsoleError('contacts_geometry_invalid')
    if not (frame[0]<=nav[0]<=item[0]<item[2]<=nav[2]<=frame[2] and
        frame[1]<=nav[1]<=item[1]<item[3]<=nav[3]<=frame[3]):raise ConsoleError('contacts_geometry_outside_navigation')
    scale=96/dpi;x=((item[0]+item[2])/2-frame[0])*scale;y=((item[1]+item[3])/2-frame[1])*scale
    if not 0<x<80 or not 0<(nav[2]-nav[0])*scale<=100:raise ConsoleError('contacts_geometry_not_left_navigation')
    return dict(x=round(x,3),y=round(y,3),coordinate_space='window_relative_logical',dpi=dpi)


def window_geometry(hwnd):
    from ctypes import wintypes as w
    user=c.WinDLL('user32',use_last_error=True);dwm=c.WinDLL('dwmapi',use_last_error=True)
    user.GetDpiForWindow.argtypes=[w.HWND];user.GetDpiForWindow.restype=w.UINT
    dwm.DwmGetWindowAttribute.argtypes=[w.HWND,w.DWORD,c.c_void_p,w.DWORD];dwm.DwmGetWindowAttribute.restype=c.c_int32
    rect=w.RECT()
    if dwm.DwmGetWindowAttribute(hwnd,9,c.byref(rect),c.sizeof(rect))!=0:
        raise ConsoleError('contacts_window_geometry_unavailable')
    return dict(frame=[rect.left,rect.top,rect.right,rect.bottom],dpi=int(user.GetDpiForWindow(hwnd)))


def rectangle(node):
    value=node.CurrentBoundingRectangle
    return [int(value.left),int(value.top),int(value.right),int(value.bottom)]


def page_structure(root,raw,uia,pid):
    result={}
    for cls in PAGE_CLASSES:
        items=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId,cls))
        if type(items.Length) is not int or not 0<=items.Length<=256:raise ConsoleError('contacts_navigation_structure_limit')
        visible=0
        for index in range(items.Length):
            node=items.GetElement(index)
            if node.CurrentProcessId!=pid or node.CurrentClassName!=cls:raise ConsoleError('contacts_navigation_structure_changed')
            visible+=int(not node.CurrentIsOffscreen)
        result[cls]=dict(count=items.Length,onscreen_count=visible)
    return result


def collect(root,raw,uia,fp,label,recipient,geometry=None):
    def verify_root():
        if (root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd'] or
            root.CurrentClassName!='mmui::MainWindow'):raise ConsoleError('contacts_navigation_root_changed')
    verify_root()
    items=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId,NAV_CLASS))
    if type(items.Length) is not int or items.Length!=1:raise ConsoleError('contacts_navigation_container_ambiguous')
    nav=items.GetElement(0);nav_runtime=tuple(nav.GetRuntimeId())
    if nav.CurrentProcessId!=fp['pid'] or nav.CurrentClassName!=NAV_CLASS or not nav_runtime:
        raise ConsoleError('contacts_navigation_container_changed')
    # Server-side exact app-label filtering inside the fixed navigation bar;
    # no labels or values from the current chat/contacts list are requested.
    matches=nav.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(uia.UIA_NamePropertyId,'通讯录'))
    if type(matches.Length) is not int or not 0<=matches.Length<=4:raise ConsoleError('contacts_navigation_item_limit')
    records=[]
    for index in range(matches.Length):
        node=matches.GetElement(index);runtime=tuple(node.GetRuntimeId())
        if node.CurrentProcessId!=fp['pid'] or not runtime:raise ConsoleError('contacts_navigation_item_changed')
        cls=node.CurrentClassName
        if not isinstance(cls,str) or not re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}',cls):cls='other'
        availability={}
        for name in ('Invoke','SelectionItem','Toggle','LegacyIAccessible'):
            try:
                value=node.GetCurrentPropertyValue(getattr(uia,'UIA_Is'+name+'PatternAvailablePropertyId'))
                availability[name]=bool(value) if type(value) in (bool,int) and value in (0,1) else None
            except Exception:availability[name]=None
        record=dict(class_name=cls,runtime_id=list(runtime),control_type=int(node.CurrentControlType),
            enabled=bool(node.CurrentIsEnabled),offscreen=bool(node.CurrentIsOffscreen),patterns=availability,
            accelerator=safe_shortcut(node.CurrentAcceleratorKey),access_key=safe_shortcut(node.CurrentAccessKey))
        try:
            state=node.GetCurrentPropertyValue(uia.UIA_LegacyIAccessibleStatePropertyId)
            record['legacy_state']=state if type(state) is int and 0<=state<2**32 else None
        except Exception:record['legacy_state']=None
        if geometry is not None and record['enabled'] and not record['offscreen']:
            bounds=geometry(fp['hwnd']);item_rect=rectangle(node);nav_rect=rectangle(nav)
            record.update(physical_rect=item_rect,navigation_physical_rect=nav_rect,window_physical_frame=bounds['frame'],
                dpi=bounds['dpi'],geometry_valid=False)
            try:
                record['logical_point']=logical_navigation_point(bounds['frame'],item_rect,nav_rect,bounds['dpi'])
                record['geometry_valid']=True
            except ConsoleError as exc:
                # Diagnostics stay numeric; an invalid frame never yields an
                # input point and must not be silently clipped or guessed.
                record['geometry_reason']=str(exc)
        records.append(record)
        if tuple(node.GetRuntimeId())!=runtime:raise ConsoleError('contacts_navigation_item_changed')
    if tuple(nav.GetRuntimeId())!=nav_runtime:
        raise ConsoleError('contacts_navigation_context_changed')
    verify_root()
    return dict(navigation_runtime_id=list(nav_runtime),contacts_items=records,observed_at_unix=time.time(),
        contact_text_read=False,message_text_read=False,action_executed=False,navigation_verified=False)


def sources():
    return dict(readonly.sources(),**{'scripts/inspect_contacts_navigation.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def worker(pipe,fp,label,recipient):
    sys.coinit_flags=0
    try:
        with physical_coordinate_context():
            from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
            client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
            from comtypes.gen import UIAutomationClient as uia
            result=collect(root,client._client,uia,fp,label,recipient,geometry=window_geometry)
            result['page_structure']=page_structure(root,client._client,uia,fp['pid'])
            client.require_background_mode()
        result.update(dpi_context='per_monitor_aware_v2',dpi_context_restored=True)
        pipe.send(dict(ok=True,result=result))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_metadata_failed'))
    finally:pipe.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-navigation-only',action='store_true');parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','context-evidence'):parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not args.inspect_navigation_only:print(json.dumps(dict(state='disabled',action_executed=False)));return 0
    try:report=readonly.run(args,probe_worker=worker,source_provider=sources,
        report_kind='bound_contacts_navigation_metadata_readonly',success_state='contacts_navigation_inspected')
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_preparation_failed')
    path=readonly.original.DIRECTORY/('contacts-navigation-metadata-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='contacts_navigation_inspected' else 1


if __name__=='__main__':raise SystemExit(main())
