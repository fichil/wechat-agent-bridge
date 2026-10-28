"""Read only the accessible object of this campaign's numbered message echo.

No message model, unbounded QObject children, text buffer, sender ID, client
method or new helper is accessed. The successful send is a positive sample,
not a general own-message classifier.
"""
import argparse
from functools import partial
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_background_draft as original
from scripts.verify_bound_signal_send import TEST_TEXT
from wechat_agent_bridge.console_models import ConsoleError


def require_sent_sample(report,fp):
    w=report.get('worker',{}); n=w.get('native_evidence',{})
    if (report.get('state')!='message_echo_observed' or report.get('patch',{}).get('fingerprint')!=fp or
        report.get('patch_restored') is not True or report.get('desktop_unchanged') is not True or
        report.get('events_observed')!=0 or report.get('cleanup_errors')!=[] or
        w.get('new_exact_echo_count')!=1 or w.get('draft_empty_after') is not True or
        n.get('qt_method_called') is not True or n.get('method_returned') is not True or n.get('exception_code')!=0):
        raise ConsoleError('sent_sample_verified_request_required')
    runtime=w.get('echo_runtime_id')
    if (not isinstance(runtime,list) or len(runtime)!=4 or any(type(x) is not int for x in runtime) or
        runtime[:3]!=[42,fp.get('hwnd'),4] or not -0x80000000<=runtime[-1]<-1):
        raise ConsoleError('sent_sample_runtime_invalid')
    return runtime


def inspect_row(memory,fp,profile,runtime):
    from wechat_agent_bridge.adapters.runtime_accessibility import interface_for_id,object_metadata
    profile.validate_loaded(memory,fp)
    iface=interface_for_id(memory,fp.module_base,runtime); table=memory.number(iface)
    if not fp.module_base<=table<=fp.module_base+fp.module_size-160:
        raise ConsoleError('message_accessible_table_invalid')
    slots=[]
    for i in range(20):
        rva=profile.pe.unpack('<Q',profile.pe.offset(table-fp.module_base+i*8,8))[0]-profile.base
        if not 0<=rva<fp.module_size or memory.number(table+i*8)!=fp.module_base+rva:
            raise ConsoleError('message_accessible_table_changed')
        slots.append(rva)
    if slots[2]!=0x1B8FF00: raise ConsoleError('message_object_getter_unverified')
    weak=memory.number(iface+8); control=memory.number(weak)
    if not control or memory.number(control+4,'<i')==0: raise ConsoleError('message_widget_destroyed')
    widget=memory.number(weak+8); metadata=object_metadata(memory,fp.module_base,fp.module_size,profile,widget)
    if metadata.get('class')!='mmui::ChatTextItemView': raise ConsoleError('message_sample_class_changed')
    if interface_for_id(memory,fp.module_base,runtime)!=iface: raise ConsoleError('message_sample_cache_changed')
    memory.recheck()
    return dict(accessible_vtable_rva=table-fp.module_base,slot_rvas=slots,
        widget_vtable_rva=metadata['vtable_rva'],class_name=metadata['class'],
        hierarchy=[dict(class_name=x['class'],metadata_rva=x['rva'],metacall_rva=x['metacall']) for x in metadata['hierarchy']],
        methods_called=False,sender_identity_verified=False,model_fields_read=False)


def worker(pipe,fp_dict,names,expected,write,*,sent_evidence):
    sys.coinit_flags=0; native=reader=None
    try:
        if write: raise ConsoleError('sent_sample_write_forbidden')
        from scripts.uia_client_bootstrap import prepare_client_elements,read_pattern_context,require_safe_context
        from scripts.probe_bound_semantics import unique_by_runtime
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,Fingerprint
        from wechat_agent_bridge.adapters.runtime_accessibility import ExactProfile,ReadOnlyProcess,BoundedMemory
        path=Path(sent_evidence).resolve()
        if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>200000:
            raise ConsoleError('sent_sample_local_evidence_required')
        runtime=require_sent_sample(json.loads(path.read_text(encoding='utf-8-sig')),fp_dict)
        client,root,element,pattern,legacy,uia=prepare_client_elements(fp_dict,expected)
        backend=WindowsUIA(); target=backend.target_control_probe(fp_dict['pid'],fp_dict['hwnd'],names)
        original._exchange(pipe,'verified',dict(target=target,root_runtime_id=list(root.GetRuntimeId())))
        original.check_draft_target(expected,target)
        row=unique_by_runtime(root,client._client,uia,uia.UIA_ListItemControlTypeId,runtime)
        if row.CurrentName!=TEST_TEXT: raise ConsoleError('sent_sample_exact_text_changed')
        original._exchange(pipe,'checkpoint','sent_sample_metadata')
        require_safe_context(read_pattern_context(client._client,element,pattern))
        fp=Fingerprint(**fp_dict); native=NativeGate(fp.hwnd)
        if native.fingerprint()!=fp: raise ConsoleError('sent_sample_process_changed')
        profile=ExactProfile(native.path.read_bytes()); reader=ReadOnlyProcess(fp,native.fingerprint)
        memory=BoundedMemory(reader.read,reader.check,max_bytes=16384)
        sys.path.insert(0,str(ROOT/'.local/static-tools'))
        result=inspect_row(memory,fp,profile,runtime)
        if tuple(row.GetRuntimeId())!=tuple(runtime) or row.CurrentName!=TEST_TEXT:
            raise ConsoleError('sent_sample_changed')
        original.check_draft_target(expected,backend.target_control_probe(fp.pid,fp.hwnd,names))
        original._exchange(pipe,'checkpoint','sent_sample_complete')
        result.update(state='preflight_verified',attempted=False,known_numbered_echo_verified=True,
                      memory_bytes_read=16384-memory.remaining)
        pipe.send(dict(kind='result',value=result))
    except Exception as exc:
        pipe.send(dict(kind='error',value=str(exc) if isinstance(exc,ConsoleError) else 'sent_sample_inspection_failed'))
    finally:
        if reader: reader.close()
        if native: native.close()
        pipe.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-bound',action='store_true'); parser.add_argument('--allow-accessibility-patch',action='store_true')
    for flag in ('account','group','private','target-evidence','synthetic-evidence','virtual-desktop','sent-evidence'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not args.inspect_bound:
        print(json.dumps(dict(state='disabled',send_enabled=False))); return 0
    args.run,args.preflight,args.operator_idle=False,True,False
    try:
        from scripts.probe_bound_uia_context import require_context_canary
        if not args.sent_evidence or not args.virtual_desktop: raise ConsoleError('sent_sample_evidence_required')
        require_context_canary(args.synthetic_evidence)
        report=original.run(args,taskview_desktop_id=args.virtual_desktop,
            inspection_worker=partial(worker,sent_evidence=args.sent_evidence))
    except Exception as exc:
        report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'sent_sample_preparation_failed')
    report.update(kind='known_sent_echo_object_metadata',send_invoked=False,api_called=False,
        inspector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    path=original.DIRECTORY/('sent-message-metadata-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'),patch_restored=report.get('patch_restored'))))
    return 0 if report['state']=='preflight_verified' else 1


if __name__=='__main__': raise SystemExit(main())
