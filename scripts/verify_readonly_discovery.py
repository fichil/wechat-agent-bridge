"""Explicit local read-only acceptance. Default disabled; never controls production."""
import argparse
from contextlib import closing
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OBSERVATION = {}


class LocalConsole:
    def __init__(self, port, log):
        self.origin=f'http://127.0.0.1:{port}'
        self.opener=build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf=''
        lines=log.read_text(encoding='utf-8').splitlines()
        launch=next(line for line in reversed(lines) if line.startswith(self.origin+'/#'))
        parts=urlsplit(launch)
        if parts.scheme!='http' or parts.netloc!=f'127.0.0.1:{port}' or not parts.fragment:
            raise RuntimeError('invalid_local_console_url')
        self.csrf=self.request('/api/session',dict(token=parts.fragment))['csrf']

    def request(self, path, body=None, expected=200):
        if not path.startswith('/api/'):
            raise ValueError('api_path_required')
        headers={'Origin':self.origin,'X-CSRF-Token':self.csrf}
        data=None
        if body is not None:
            data=json.dumps(body).encode()
            headers['Content-Type']='application/json'
        request=Request(self.origin+path,data=data,headers=headers)
        try:
            response=self.opener.open(request,timeout=20)
        except HTTPError as exc:
            response=exc
        with response:
            if response.status!=expected:
                raise RuntimeError('unexpected_local_http_status')
            return json.loads(response.read())


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def check_observer(observer):
    OBSERVATION.update(foreground_focus_events=len(observer.evidence.events),
                       target_events=sum(e['target_process'] for e in observer.evidence.events),
                       observation_failed=observer.evidence.failed)
    observer.check()


def wait_for_read_progress(production, before, check=lambda:None, *, sleep=time.sleep, clock=time.monotonic):
    deadline=clock()+12
    while clock()<deadline:
        status=production.request('/api/status')
        require(status['paused'] is False and any(c['send_ready'] for c in status['execution']['channels']),
                'production_readiness_changed')
        require(status['background']['fingerprint']==before['background']['fingerprint'],
                'production_binding_changed')
        check()
        if status['execution']['lanes']['read']['last_success']>before['execution']['lanes']['read']['last_success']:
            return status
        sleep(.5)
    raise RuntimeError('production_read_did_not_advance')


def fields_summary(accounts):
    return [dict(nickname_present=bool(a.get('discovery_sources',{}).get('name')),
                 wechat_id_present=bool(a.get('wechat_id')),state=a.get('discovery_state'),
                 sources=a.get('discovery_sources',{}),reason=a.get('discovery_reason')) for a in accounts]


def current_record_digest(instance):
    from wechat_agent_bridge.acceptance import acceptance_directory
    from wechat_agent_bridge.console_store import digest
    with closing(sqlite3.connect((acceptance_directory()/'gate.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT payload FROM records WHERE bucket=? AND key=?',
                       ('console_identity',digest(instance))).fetchone()
    return hashlib.sha256(row[0]).hexdigest() if row else None


def run(phase, previous=None):
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.adapters.own_account_windows import native_stamp
    from wechat_agent_bridge.adapters.readonly_account import OwnIdentityRecordReader,verified_record_id
    production=LocalConsole(8766,ROOT/'.local/startup/stable-live-20261028.out.log')
    testing=LocalConsole(8767,ROOT/'.local/startup/dev-readonly-8767.out.log')
    before=production.request('/api/status')
    fingerprint=before['background']['fingerprint']
    require(before['paused'] is False and any(c['send_ready'] for c in before['execution']['channels']),
            'production_not_ready_before_test')
    test_status=testing.request('/api/status')
    require(test_status.get('account_discovery')==dict(mode='read-only',metadata_only=True), 'wrong_test_mode')
    require(test_status['simulation_only'] is False and test_status['provider']=='mock' and
            test_status['paused'] is True and test_status['acceptance'] is None,'test_not_isolated')
    first=None
    samples=[]
    desktop_before=snapshot(fingerprint['pid'])
    with DesktopEventObserver(fingerprint['pid']) as observer:
        observer.set_phase('readonly_discovery')
        for index in range(3):
            accounts=testing.request('/api/discover',{})
            require(bool(accounts),'no_accounts_discovered')
            for account in accounts:
                require(not any(account['grant'][key] for key in ('read','upload','reply')),'test_chat_grant_present')
            if first is None:
                first=accounts
            else:
                require(accounts==first,'repeat_discovery_changed')
            status=production.request('/api/status')
            require(status['paused'] is False and any(c['send_ready'] for c in status['execution']['channels']),
                    'production_readiness_changed')
            require(status['background']['fingerprint']==fingerprint,'production_binding_changed')
            samples.append(status)
            check_observer(observer)
            time.sleep(1)
        observer.set_phase('readonly_endpoint_guards')
        error=testing.request('/api/resume',{},expected=409)
        require(error.get('error')=='account_discovery_read_only','resume_not_blocked')
        error=testing.request('/api/grant',dict(account=first[0]['id'],read=True),expected=409)
        require(error.get('error')=='account_discovery_read_only','grant_not_blocked')
        check_observer(observer)
        after=wait_for_read_progress(production,before,lambda:check_observer(observer))
        event_count=len(observer.evidence.events)
    desktop_after=snapshot(fingerprint['pid'])
    require(desktop_after==desktop_before,'desktop_snapshot_changed')
    known=[a for a in first if a.get('wechat_id')]
    require(bool(known),'verified_wechat_id_unavailable')
    for account in known:
        require(account['name']!=account['wechat_id'],'id_used_as_nickname')
        pid,hwnd=(int(part) for part in account['instance_id'].split(':'))
        record=OwnIdentityRecordReader().read(account['instance_id'])
        require(verified_record_id(record,native_stamp(hwnd))==account['wechat_id'],'current_record_does_not_match')
    record_digest=current_record_digest(known[0]['instance_id'])
    require(len(after['errors'])==len(before['errors']),'production_error_count_changed')
    final_test=testing.request('/api/status')
    require(final_test['usage']['hour']==0 and final_test['usage']['day']==0 and
            final_test['pending']==0 and not final_test['execution']['channels'],'test_processed_chat_or_model')
    with closing(sqlite3.connect((ROOT/'.local/dev-account-discovery-windows/windows-state.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        grants=db.execute("SELECT count(*) FROM records WHERE bucket='grant'").fetchone()[0]
    require(grants==0,'test_grants_persisted')
    both=all(a['discovery_state']=='identified' for a in known)
    report=dict(kind='readonly_account_acceptance',phase=phase,state='identified' if both else 'partial_verified',
                dual_fields_verified=both,client_versions=sorted({a['version'] for a in first}),
                os='Windows '+sys.getwindowsversion().__str__(),adapter_revision='windows-own-metadata-readonly-1',
                fields=fields_summary(first),repeat_count=3,repeat_consistent=True,
                production_paused=after['paused'],production_send_ready=all(c['send_ready'] for c in after['execution']['channels']),
                production_binding_unchanged=after['background']['fingerprint']==fingerprint,
                production_read_advanced=True,production_new_errors=0,
                desktop_unchanged=True,foreground_focus_events=event_count,
                ui_actions_executed=False,model_calls=0,test_chat_grants=0,
                identity_record_ciphertext_sha256=record_digest)
    if previous:
        old=json.loads(previous.read_text(encoding='utf-8-sig'))
        require(old['identity_record_ciphertext_sha256']==record_digest,'identity_record_changed_across_restart')
        require(old['fields']==report['fields'],'field_states_changed_across_restart')
        report['restart_consistent']=True
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--phase',choices=('initial','restarted'),default='initial')
    parser.add_argument('--previous-report',type=Path)
    args=parser.parse_args(argv)
    if not args.run:
        print(json.dumps(dict(state='disabled',production_modified=False)))
        return 0
    try:
        report=run(args.phase,args.previous_report)
    except Exception as exc:
        # Do not print exception messages, response bodies, IDs or credentials.
        known={'desktop_event_change_observed','desktop_event_observation_failed',
               'account_identity_record_stale','account_identity_record_unverified',
               'account_identity_record_unreadable','background_focus_unavailable',
               'desktop_locked_or_unavailable','repeat_discovery_changed','verified_wechat_id_unavailable',
               'production_not_ready_before_test','production_readiness_changed','production_binding_changed',
               'desktop_snapshot_changed','production_error_count_changed','production_read_did_not_advance',
               'test_not_isolated','wrong_test_mode','no_accounts_discovered','test_chat_grant_present',
               'resume_not_blocked','grant_not_blocked','test_processed_chat_or_model','test_grants_persisted',
               'current_record_does_not_match','id_used_as_nickname','unexpected_local_http_status',
               'identity_record_changed_across_restart','field_states_changed_across_restart'}
        reason=str(exc) if str(exc) in known else 'readonly_acceptance_failed'
        print(json.dumps(dict(state='failed',error_type=type(exc).__name__,reason=reason,
                              observation=OBSERVATION,production_modified=False)))
        return 1
    directory=ROOT/'.local/dev-account-discovery-windows'
    path=directory/('readonly-acceptance-'+args.phase+'-'+uuid.uuid4().hex+'.json')
    path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(state=report['state'],path=str(path),dual_fields_verified=report['dual_fields_verified'],
                          desktop_unchanged=report['desktop_unchanged'],production_read_advanced=True,
                          foreground_focus_events=report['foreground_focus_events'],
                          restart_consistent=report.get('restart_consistent'))))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
