"""Explicit local acceptance: two API scans, then a new hidden CLI process.

Only sanitized fingerprints and avatar metadata are saved. Avatar payloads,
database images and key material never enter the report or progress output.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fingerprint(result):
    fields = sorted((a['wxid'], a['wechat_id'], a['nickname'])
                    for a in result['accounts'] if a['wxid'])
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def avatar_metadata(account):
    from wechat_account_probe.avatar import MAX_AVATAR_BYTES, AVATAR_SOURCE, detect_mime
    output = dict(account_fingerprint=hashlib.sha256((account['wxid'] or '').encode()).hexdigest(),
                  status=account['avatar_status'], reason=account['avatar_reason'])
    avatar = account['avatar']
    if avatar is None:
        output['valid'] = account['avatar_status'] != 'available'
        return output
    try:
        raw = base64.b64decode(avatar['data_base64'], validate=True)
        mime = detect_mime(raw)
        valid = (0 < len(raw) <= MAX_AVATAR_BYTES and mime == avatar['mime_type'] and
                 account['avatar_status'] == 'available' and
                 account['sources'].get('avatar') == AVATAR_SOURCE)
        output.update(valid=valid, mime_type=mime, byte_length=len(raw),
                      sha256=hashlib.sha256(raw).hexdigest())
    except Exception:
        output.update(valid=False, validation_error='invalid_avatar_payload')
    return output


def source_hashes():
    paths = list((ROOT / 'wechat_account_probe').glob('*.py')) + [Path(__file__).resolve()]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--expected-accounts', type=int)
    parser.add_argument('--require-avatars', action='store_true')
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    if args.expected_accounts is not None and args.expected_accounts < 0:
        parser.error('expected_accounts_must_be_nonnegative')
    from wechat_account_probe import discover_accounts
    before = source_hashes()
    results, summaries = [], []
    for number in range(1, 4):
        if number < 3:
            result = discover_accounts()
        else:
            restarted = subprocess.run(
                [sys.executable, '-B', '-X', 'utf8', '-m', 'wechat_account_probe', '--run'],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=190,
                creationflags=0x08000000)
            result = json.loads(restarted.stdout)
            if restarted.returncode != result['exit_code']:
                raise RuntimeError('cli_exit_mismatch')
        results.append(result)
        summaries.append(dict(summary=result.get('summary'),
                              duration_seconds=result.get('duration_seconds'),
                              instances=result.get('client_instances'),
                              observation=result.get('observation'),
                              reasons=[a['reason'] for a in result['accounts']],
                              null_wechat_id_count=sum(a['wechat_id'] is None for a in result['accounts']),
                              avatars=sorted((avatar_metadata(a) for a in result['accounts']),
                                             key=lambda a: a['account_fingerprint']),
                              issues=result.get('issues')))
        print(json.dumps(dict(run=number, **summaries[-1]), ensure_ascii=False), flush=True)
    complete = all(r['schema_version'] == 2 and r['exit_code'] == 0 for r in results)
    expected = (args.expected_accounts is None or
                all(r['summary']['confirmed_accounts'] == args.expected_accounts and
                    len(r['accounts']) == args.expected_accounts for r in results))
    consistent = len({fingerprint(r) for r in results}) == 1
    avatars_valid = all(a['valid'] for run in summaries for a in run['avatars'])
    avatars_available = all(a['status'] == 'available' for run in summaries for a in run['avatars'])
    avatar_signatures = [json.dumps(run['avatars'], sort_keys=True) for run in summaries]
    avatars_consistent = len(set(avatar_signatures)) == 1
    observed = all(r.get('observation', {}).get('available') for r in results)
    zero_events = observed and all(not r['observation']['wechat_foreground_events'] and
                                  not r['observation']['wechat_focus_events'] for r in results)
    unchanged = before == source_hashes()
    report = dict(schema_version=2, timestamp_utc=datetime.now(timezone.utc).isoformat(),
                  complete_accounts=complete, expected_accounts=args.expected_accounts,
                  expected_account_count_matches=expected,
                  consistent_after_restart=consistent, avatars_valid=avatars_valid,
                  avatars_available=avatars_available, avatars_consistent_after_restart=avatars_consistent,
                  source_unchanged=unchanged,
                  noninterference_observation='zero_wechat_focus_events' if zero_events else 'inconclusive',
                  note='User-generated events are not attributed to this module. Only the current desktop is observed.',
                  account_field_fingerprints=[fingerprint(r) for r in results], runs=summaries,
                  source_sha256=before,
                  live_multiple_accounts_tested=all(r['summary']['confirmed_accounts'] > 1 for r in results),
                  live_multiple_sessions_tested=all(len({p['session_id'] for p in r['client_instances']}) > 1
                                                   for r in results))
    directory = ROOT / '.local' / 'account-probe'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('avatar-acceptance-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write('\n')
    print(json.dumps(dict(report=str(path), complete_accounts=complete, expected_account_count_matches=expected,
                          avatars_valid=avatars_valid, avatars_available=avatars_available,
                          consistent_after_restart=consistent and avatars_consistent,
                          noninterference=report['noninterference_observation']), ensure_ascii=False), flush=True)
    passed = (complete and expected and consistent and avatars_valid and avatars_consistent and unchanged and
              zero_events and (avatars_available or not args.require_avatars))
    return 0 if passed else 2


if __name__ == '__main__':
    sys.exit(main())
