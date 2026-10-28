"""Owned Edit/MTA calibration for one exact-search update; no external client."""
import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_uia_autofocus as base
from scripts.probe_uia_autofocus_tree import replace_attributes
from scripts.development_exact_search import exact_search_transaction,sources

TOKEN='wxid_synthetic_exact'
ORIGINAL_CHILD=base.child


def phase(create,uia,target,exchange,name,reports):
    from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
    from scripts.uia_client_bootstrap import read_pattern_context,require_safe_context
    client=BackgroundUIAClient(factory=lambda:create(uia.CUIAutomation8,uia.IUIAutomation2))
    before=exchange('prepare',name)
    element=client.element_from_handle(target['a']);runtime=tuple(element.GetRuntimeId())
    pattern=element.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    calls=[];claims=[];contexts=[]
    def check():
        value=exchange('check',name)
        if value['focus']!=target['b'] or value['focus_events']:raise RuntimeError('owned_exact_search_focus_changed')
    class Port:
        def verify(self):
            client.require_background_mode()
            if (element.CurrentProcessId!=target['pid'] or element.CurrentNativeWindowHandle!=target['a'] or
                element.CurrentControlType!=uia.UIA_EditControlTypeId or tuple(element.GetRuntimeId())!=runtime or
                pattern.CurrentIsReadOnly):raise RuntimeError('owned_exact_search_identity_changed')
        def guard_context(self):
            require_safe_context(read_pattern_context(client._client,element,pattern));contexts.append(True)
        def value(self):return pattern.CurrentValue
        def set_value(self,value):calls.append(True);pattern.SetValue(value)
    port=Port()
    outcome=exact_search_transaction(port,check,lambda:claims.append(True),'',TOKEN,allowed=True)
    after=exchange('complete',name);port.verify()
    reports.append(dict(**outcome,phase=name,set_calls=len(calls),claim_count=len(claims),
        native_full_readback_equal=after['native_full_readback_equal'],uia_full_readback_equal=port.value()==TOKEN,
        context_verified=len(contexts)>=2,focus_unchanged=before['focus']==after['focus']==target['b'] and not after['focus_events']))


def qualified(report):
    result=report.get('result',{});phases=result.get('phases',[])
    return bool(not report.get('error') and not result.get('error') and result.get('mta_verified') is True and
        len(phases)==1 and phases[0].get('state')=='exact_search_updated' and phases[0].get('set_calls')==1 and
        phases[0].get('claim_count')==1 and phases[0].get('setter_returned') is True and
        all(phases[0].get(k) is True for k in ('native_full_readback_equal','uia_full_readback_equal','context_verified','focus_unchanged')) and
        report.get('source_unchanged') is True and report.get('uia_dll_unchanged') is True and
        report.get('input_checkpoints_unchanged') is True and report.get('checkpoint_count',0)>=3 and
        report.get('cleanup',{}).get('terminated') is False and report.get('cleanup',{}).get('errors')==[] and
        all(report.get('cleanup',{}).get(k) is True for k in (
            'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent')))


def child(pipe,desktop_id):
    # Spawn imports a fresh module: install the exact-search phase inside the
    # owned child, not merely in the parent's module dictionary.
    with replace_attributes(base,phase=phase,PHASES=('exact_search',),TEXT={'exact_search':TOKEN}):
        ORIGINAL_CHILD(pipe,desktop_id)


def run():
    with replace_attributes(base,child=child,fingerprints=sources,complete_pass=qualified):report=base.run()
    phases=report.get('result',{}).get('phases',[])
    report.update(kind='owned_exact_search_probe',contains_real_client=False,background_qualified=False,
        operation=phases[0] if len(phases)==1 else {},sources=report.get('fingerprints'),
        sources_unchanged=report.get('source_unchanged') is True,
        mta_verified=report.get('result',{}).get('mta_verified') is True,
        working_desktop_unchanged=report.get('input_checkpoints_unchanged') is True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-synthetic',action='store_true')
    args=parser.parse_args(argv)
    if not args.run_synthetic:print(json.dumps(dict(state='disabled')));return 0
    report=run();path=ROOT/'.local/live-acceptance'/('exact-search-synthetic-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),passed=report.get('passed'),error=report.get('error'),worker_error=report.get('result',{}).get('error'))))
    return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())
