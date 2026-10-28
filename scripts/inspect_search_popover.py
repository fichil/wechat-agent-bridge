"""Read-only structural metadata inside the exact-recipient search popover.

Never requests Name, Text, HelpText, AutomationId or values from result items.
No screenshot, contact selection, profile action, input or provider call.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import inspect_bound_search as readonly
from wechat_agent_bridge.console_models import ConsoleError

POPOVER='mmui::SearchContentPopover'
CLASSES=('mmui::SearchContactView','mmui::SearchContactCellView','mmui::SearchContactInfoIconView',
    'mmui::SearchRecentCellView','mmui::SearchRecentHeaderCellView','mmui::SearchResultItemView')


def shallow_structure(pop,raw,uia,pid):
    queue=[(pop,0)];records=[];seen=set()
    while queue and len(records)<48:
        node,depth=queue.pop(0);rid=tuple(node.GetRuntimeId())
        if not rid or rid in seen or node.CurrentProcessId!=pid:raise ConsoleError('search_structure_node_changed')
        seen.add(rid);cls=node.CurrentClassName;kind=int(node.CurrentControlType)
        if not isinstance(cls,str) or not re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}',cls):cls='other'
        records.append(dict(depth=depth,class_name=cls,control_type=kind))
        # Never walk into a result item, even for metadata. No result contents
        # or string properties are needed to understand the container layout.
        if depth<3 and kind!=50007 and not cls.startswith(('mmui::SearchMessage','mmui::SearchMsg')):
            children=node.FindAll(uia.TreeScope_Children,raw.CreateTrueCondition())
            if type(children.Length) is not int or not 0<=children.Length<=32:raise ConsoleError('search_structure_child_limit')
            queue.extend((children.GetElement(i),depth+1) for i in range(children.Length))
        if tuple(node.GetRuntimeId())!=rid:raise ConsoleError('search_structure_node_changed')
    return dict(records=records,truncated=bool(queue))


def collect(root,raw,uia,fp,label,recipient):
    search=readonly.inspect_control(root,raw,uia,fp,label,recipient)
    if search['query_kind']!='recipient_id':raise ConsoleError('search_popover_exact_query_required')
    matches=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId,POPOVER))
    count=matches.Length
    if type(count) is not int or not 0<=count<=1:raise ConsoleError('search_popover_ambiguous')
    result=dict(query=search,popover_count=count,popover_exposed=False,records=[],identity_verified=False,
        message_text_read=False,contact_text_read=False,action_executed=False)
    if count:
        pop=matches.GetElement(0);runtime=tuple(pop.GetRuntimeId())
        def verify():
            if (not runtime or len(runtime)>16 or pop.CurrentProcessId!=fp['pid'] or
                pop.CurrentClassName!=POPOVER or tuple(pop.GetRuntimeId())!=runtime):
                raise ConsoleError('search_popover_identity_changed')
        verify();result.update(popover_exposed=True,popover_runtime_id=list(runtime))
        result['container_structure']=shallow_structure(pop,raw,uia,fp['pid'])
        for cls in CLASSES:
            items=pop.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId,cls))
            if type(items.Length) is not int or not 0<=items.Length<=128:raise ConsoleError('search_popover_node_limit')
            onscreen=enabled=0;types=set()
            for i in range(items.Length):
                item=items.GetElement(i);rid=tuple(item.GetRuntimeId())
                if item.CurrentProcessId!=fp['pid'] or item.CurrentClassName!=cls or not rid:
                    raise ConsoleError('search_popover_child_changed')
                if item.CurrentIsOffscreen in (False,0):onscreen+=1
                if item.CurrentIsEnabled in (True,1):enabled+=1
                types.add(int(item.CurrentControlType))
                if tuple(item.GetRuntimeId())!=rid:raise ConsoleError('search_popover_child_changed')
            result['records'].append(dict(class_name=cls,count=items.Length,onscreen_count=onscreen,
                enabled_count=enabled,control_types=sorted(types)))
            verify()
    after=readonly.inspect_control(root,raw,uia,fp,label,recipient)
    if after!=search:raise ConsoleError('search_popover_query_changed')
    return result


def sources():
    return dict(readonly.sources(),**{'scripts/inspect_search_popover.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def worker(pipe,fp,label,recipient):
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
        from comtypes.gen import UIAutomationClient as uia
        result=collect(root,client._client,uia,fp,label,recipient)
        client.require_background_mode();pipe.send(dict(ok=True,result=result))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'search_popover_metadata_failed'))
    finally:pipe.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-popover-metadata',action='store_true');parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','context-evidence'):parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not args.inspect_popover_metadata:print(json.dumps(dict(state='disabled',action_executed=False)));return 0
    try:report=readonly.run(args,probe_worker=worker,source_provider=sources,
        report_kind='bound_search_popover_metadata_readonly',success_state='search_popover_metadata_inspected')
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'search_popover_preparation_failed')
    path=original_directory()/('search-popover-metadata-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='search_popover_metadata_inspected' else 1


def original_directory():return readonly.original.DIRECTORY


if __name__=='__main__':raise SystemExit(main())
