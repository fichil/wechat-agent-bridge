"""Explicit filehelper-only voice acceptance; never retry a partially run report."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--conversation-id', required=True)
    parser.add_argument('--profile-id', required=True)
    parser.add_argument('--speech-model', default='')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    os.environ['BRIDGE_DATA_DIR'] = str(args.archive.resolve().parent)
    from backend.app.delivery import NativeTransport
    from backend.app.secret_protection import reveal
    from backend.app.voice_provider import SpeechProvider
    from wechat_account_probe import discover_accounts
    from wechat_account_probe.observation import Observation
    from wechat_sender.media_files import stage, release
    from wechat_sender.worker import modules
    from scripts.verify_media_send_live import DesktopObservation

    def query(sql, values=()):
        with closing(sqlite3.connect(args.archive.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, values)]

    accounts = query('SELECT wxid FROM accounts WHERE account_id=?', (args.account_id,))
    targets = query('SELECT source_id FROM conversations WHERE account_id=? AND conversation_id=?', (args.account_id, args.conversation_id))
    if len(accounts)!=1 or len(targets)!=1 or targets[0]['source_id']!='filehelper':
        raise RuntimeError('Only an exact filehelper conversation is allowed')
    if query("SELECT 1 FROM deliveries WHERE account_id=? AND status IN ('queued','sending') LIMIT 1", (args.account_id,)):
        raise RuntimeError('Account outbox is busy; no test was sent')
    if query("SELECT 1 FROM ai_runs WHERE account_id=? AND status IN ('queued','running') LIMIT 1", (args.account_id,)):
        raise RuntimeError('Account AI work is busy; no test was sent')
    profiles = query('SELECT * FROM api_profiles WHERE profile_id=?', (args.profile_id,))
    if len(profiles)!=1:
        raise RuntimeError('Exact API profile required')
    profile = {k:v for k,v in profiles[0].items() if k!='secret'}
    settings = query('SELECT protocol,model FROM speech_profiles WHERE profile_id=?', (args.profile_id,))
    profile.update(speech_protocol=settings[0]['protocol'] if settings else 'auto',
                   speech_model=args.speech_model or (settings[0]['model'] if settings else ''))
    wxid = accounts[0]['wxid']
    found = [a for a in discover_accounts()['accounts'] if a.get('wxid')==wxid and len(a.get('instances', []))==1]
    if len(found)!=1:
        raise RuntimeError('Account is not uniquely online')
    instance = found[0]['instances'][0]
    kernel = c.WinDLL('kernel32'); ours = w.DWORD(); theirs = w.DWORD()
    kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), c.byref(ours))
    kernel.ProcessIdToSessionId(instance['pid'], c.byref(theirs))
    if ours.value!=theirs.value:
        raise RuntimeError('Observer must share the WeChat desktop session')
    binding = dict(account=wxid, pid=instance['pid'], created=instance['created'])
    transport = NativeTransport()
    identity = transport.inspect(binding)
    if identity.get('core_sha256')!='a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4' or not identity.get('ready') or not identity.get('send_voice'):
        raise RuntimeError('Exact native voice capability check failed')
    report = dict(account_id=args.account_id, conversation_id=args.conversation_id, target='filehelper',
                  marker='BRIDGE_VOICE_TEST_'+uuid4().hex[:12], profile_id=args.profile_id,
                  speech_model=profile['speech_model'] or profile['model'], identity=identity,
                  parts=[], passed=False, playback='pending_human_confirmation')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(report, stream)

    def save():
        temporary = args.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(args.report)

    speech_text = '你好，这是一条人工智能合成的语音。微信原生语音回复功能正在进行验收。'
    audio = SpeechProvider().synthesize(profile, reveal(bytes(profiles[0]['secret'])), speech_text)
    wav_path = args.report.with_suffix('.wav')
    with wav_path.open('xb') as stream:
        stream.write(audio['wav'])
    report['audio'] = {k:audio[k] for k in ('duration_ms','sample_rate','wav_sha256','silk_sha256','size_bytes')}
    media = dict(data=audio['silk'], mime_type='audio/x-silk', sha256=audio['silk_sha256'], duration_ms=audio['duration_ms'])
    probe_stage = stage(uuid4().hex, media)
    probe = transport.invoke(dict(binding, operation='send', target='filehelper', text='', kind='voice', voice_probe=True, **probe_stage))
    probe['module_unloaded'] = 'bridge_text.dll' not in modules(instance['pid'])
    report['constructor_probe'] = probe
    save()
    if probe.get('resources_released'):
        release(probe_stage)
    if not probe.get('voice_probe_valid') or not probe.get('resources_released') or not probe.get('module_unloaded'):
        raise RuntimeError('Voice constructor probe failed; no message was sent')
    observer = DesktopObservation(instance['pid'])
    report['drafts_before'] = observer.drafts()
    report['database_drafts_before'] = observer.database_drafts(instance, wxid)
    save()
    parts = [('text', report['marker']+' 开始文字、语音、文字顺序验收'), ('voice', speech_text), ('text', report['marker']+' 顺序验收结束')]
    for index, (kind, text) in enumerate(parts):
        item = dict(index=index, kind=kind, status='sending', started_at=datetime.now(timezone.utc).isoformat())
        report['parts'].append(item)
        save()  # A crash from this point is uncertain. This report cannot be reused.
        job = dict(target='filehelper', text=text if kind=='text' else '', kind=kind)
        if kind=='voice':
            job['staged_media'] = stage(uuid4().hex, media)
        before = observer.snapshot()
        with Observation([instance['pid']]) as events:
            result = transport.send(binding, job)
        after = observer.snapshot()
        evidence = result.get('evidence', {})
        matched = None
        deadline = time.monotonic()+60
        while time.monotonic()<deadline and result.get('status') in ('sent','accepted'):
            rows = query("SELECT message_id,payload,sort_key,server_id,source_ref FROM messages WHERE account_id=? AND conversation_id=? AND json_extract(payload,'$.is_self')=1 AND json_extract(payload,'$.type')=? AND unixepoch(json_extract(payload,'$.sent_at'))>=unixepoch(?)",
                         (args.account_id,args.conversation_id,kind,item['started_at']))
            if kind=='text':
                matches = [r for r in rows if json.loads(r['payload']).get('content',{}).get('text')==text]
            else:
                matches = [r for r in rows if json.loads(r['source_ref']).get('local_id')==evidence.get('local_id')
                           and int(datetime.fromisoformat(json.loads(r['payload'])['sent_at']).timestamp())==evidence.get('create_time')
                           and r['server_id'].isdigit() and int(r['server_id'])>0
                           and evidence.get('target')=='filehelper' and evidence.get('message_type')==34]
            if len(matches)==1:
                matched = matches[0]
                break
            time.sleep(.3)
        item.update(status='sent' if matched else 'failed' if result.get('status')=='failed' else 'unknown', result=result,
                    archive={k:matched[k] for k in ('message_id','server_id','sort_key','source_ref')} if matched else None,
                    stable=before==after and bool(before['windows']), before=before, after=after,
                    observation=events.result, module_unloaded='bridge_text.dll' not in modules(instance['pid']))
        if kind=='voice' and result.get('resources_released') and item['status'] in ('sent','failed'):
            release(job['staged_media'])
        save()
        print(json.dumps(dict(index=index,kind=kind,status=item['status'],native_status=result.get('status'),archive_match=bool(matched))), flush=True)
        if item['status']!='sent':
            break
    report['drafts_after'] = observer.drafts()
    report['database_drafts_after'] = observer.database_drafts(instance, wxid)
    report['drafts_unchanged'] = report['database_drafts_before']==report['database_drafts_after'] and report['drafts_before']==report['drafts_after']
    success = len(report['parts'])==3 and all(p['status']=='sent' for p in report['parts'])
    report['ordered'] = success and [p['archive']['sort_key'] for p in report['parts']]==sorted(p['archive']['sort_key'] for p in report['parts'])
    report['passed'] = bool(success and report['ordered'] and report['drafts_unchanged'] and all(
        p['stable'] and p['module_unloaded'] and p['observation']['available']
        and not p['observation']['wechat_foreground_events'] and not p['observation']['wechat_focus_events']
        for p in report['parts']))
    save()
    print(json.dumps(dict(passed=report['passed'],playback=report['playback'])), flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
