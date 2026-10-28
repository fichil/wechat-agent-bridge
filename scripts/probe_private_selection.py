"""Explicit SelectionItem.Select calibration on an owned private-desktop ListBox.

The frozen GUI/COM lifecycle is reused unchanged. This checks execution and
recovery only; any local focus change is reported, not a background qualification.
"""
import argparse
import ctypes as c
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_uia_autofocus as base
from scripts.probe_uia_autofocus_tree import replace_attributes
from scripts.development_private_selection import select_transaction,sources


def qualified(report):
    result=report.get('result',{});phases=result.get('phases',[])
    return bool(not report.get('error') and not result.get('error') and result.get('mta_verified') is True and
        len(phases)==1 and phases[0].get('phase')=='selection' and phases[0].get('select_calls')==1 and
        phases[0].get('state')=='selected_for_development' and phases[0].get('selection_readback') is True and
        phases[0].get('requested_auto_focus') is False and phases[0].get('native_selected_index')==1 and
        phases[0].get('identity_stable') is True and
        report.get('source_unchanged') is True and report.get('uia_dll_unchanged') is True and
        report.get('input_checkpoints_unchanged') is True and report.get('checkpoint_count',0)>=3 and
        report.get('cleanup',{}).get('terminated') is False and report.get('cleanup',{}).get('errors')==[] and
        all(report.get('cleanup',{}).get(k) is True for k in (
            'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent')))


def child(pipe,desktop_id):
    original_call=base.call
    owned=dict(listbox=None,native_selected_index=None)
    def wrapped_call(dll,name,restype,argtypes):
        native=original_call(dll,name,restype,argtypes)
        if name=='CreateWindowExW':
            def create(*args):
                if args[1]!='Edit' or owned['listbox'] is not None:return native(*args)
                values=list(args);values[1]='ListBox';values[3]=0x50000001;values[7]=80
                hwnd=native(*values)
                if not hwnd:raise RuntimeError('owned_listbox_create_failed')
                owned['listbox']=hwnd
                send=original_call(dll,'SendMessageW',c.c_ssize_t,[base.PTR,base.U32,base.U64,c.c_ssize_t])
                for label in ('synthetic-initial','synthetic-private-target'):
                    text=c.create_unicode_buffer(label)
                    if send(hwnd,0x180,0,c.cast(text,c.c_void_p).value)<0:raise RuntimeError('owned_listbox_add_failed')
                if send(hwnd,0x186,0,0)!=0:raise RuntimeError('owned_listbox_baseline_failed')
                return hwnd
            return create
        if name=='GetWindowTextW':
            def read(hwnd,*args):
                if hwnd==owned['listbox']:
                    send=original_call(dll,'SendMessageW',c.c_ssize_t,[base.PTR,base.U32,base.U64,c.c_ssize_t])
                    owned['native_selected_index']=int(send(hwnd,0x188,0,0))
                # Do not fabricate a text readback to satisfy the original Edit
                # canary. Its text equality is inapplicable and remains false.
                return native(hwnd,*args)
            return read
        return native
    def phase(create,uia,target,exchange,name,reports):
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client=BackgroundUIAClient(factory=lambda:create(uia.CUIAutomation8,uia.IUIAutomation2))
        ready=exchange('prepare',name)
        root=client.element_from_handle(target['a'])
        items=root.FindAll(uia.TreeScope_Children,client._client.CreatePropertyCondition(
            uia.UIA_ControlTypePropertyId,uia.UIA_ListItemControlTypeId))
        if items.Length!=2:raise RuntimeError('owned_selection_item_count')
        row=items.GetElement(1);runtime=tuple(row.GetRuntimeId());calls=[];claims=[]
        pattern=row.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
        class Port:
            def verify_row(self):
                client.require_background_mode()
                if (root.CurrentProcessId!=target['pid'] or root.CurrentNativeWindowHandle!=target['a'] or
                    row.CurrentProcessId!=target['pid'] or tuple(row.GetRuntimeId())!=runtime or
                    row.CurrentName!='synthetic-private-target'):
                    raise RuntimeError('owned_selection_identity_changed')
            def selected(self):return bool(pattern.CurrentIsSelected)
            def select(self):calls.append(True);pattern.Select()
            def verify_target(self):
                if not self.selected():raise RuntimeError('owned_selection_unconfirmed')
        port=Port()
        outcome=select_transaction(port,lambda:exchange('check',name),lambda:claims.append(True),allowed=True)
        after=exchange('complete',name)
        port.verify_row()
        reports.append(dict(**outcome,phase=name,select_calls=len(calls),claim_count=len(claims),
            requested_auto_focus=False,selection_readback=port.selected(),identity_stable=True,
            native_selected_index=owned['native_selected_index'],focus_before=ready['focus'],focus_after=after['focus'],
            local_focus_changed=ready['focus']!=after['focus'],
            text_readback_applicable=False))
    with replace_attributes(base,call=wrapped_call,phase=phase,PHASES=('selection',),TEXT={'selection':'not-a-text-operation'}):
        base.child(pipe,desktop_id)


def run():
    with replace_attributes(base,child=child,fingerprints=sources,complete_pass=qualified):
        report=base.run()
    phases=report.get('result',{}).get('phases',[])
    operation=phases[0] if len(phases)==1 else {}
    report.update(kind='owned_private_selection_probe',contains_real_client=False,background_qualified=False,
        operation=operation,native=dict(selected_index=operation.get('native_selected_index')),
        mta_verified=report.get('result',{}).get('mta_verified') is True,
        sources=report.get('fingerprints'),sources_unchanged=report.get('source_unchanged') is True,
        working_desktop_unchanged=report.get('input_checkpoints_unchanged') is True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-synthetic',action='store_true')
    args=parser.parse_args(argv)
    if not args.run_synthetic:print(json.dumps(dict(state='disabled')));return 0
    report=run();path=ROOT/'.local/live-acceptance'/('private-selection-synthetic-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),passed=report.get('passed'),error=report.get('error'),
        worker_error=report.get('result',{}).get('error'))))
    return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())
