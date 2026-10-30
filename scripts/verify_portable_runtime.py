"""Exercise an extracted package on a clean Windows host, with synthetic data.

Run only against a new disposable package directory. Never imports personal data
or sends WeChat messages. HTTPS origin must belong to the operator.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--origin',required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    if (root/'data/auth.sqlite3').exists() or (root/'data/bridge.sqlite3').exists():
        raise RuntimeError('Acceptance requires a new package data directory')
    os.environ['BRIDGE_DATA_DIR']=str(root/'data')
    os.environ['BRIDGE_DISABLE_MONITOR']='1'
    os.environ['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
    sys.path.insert(0,str(root)); sys.path.insert(0,str(root/'scripts'))
    from bridge_portable import control
    from bridge_portable.local_api import LocalApi
    import httpx
    password=secrets.token_urlsafe(32)
    control.setup(args.origin,'acceptance',password,password)
    results=dict(runtime=sys.version.split()[0],initialized=True,origin=args.origin)
    try:
        results['start']=control.start()
        results['second_start']=control.start()
        with LocalApi(args.origin,'acceptance',password) as api:
            assert api.post('accounts/query')['accounts']==[]
            results['authenticated_local_api']=True
        with httpx.Client(timeout=3,trust_env=False) as client:
            for _ in range(40):
                try:
                    response=client.get(args.origin+'/api/v1/health')
                    if response.status_code==200 and response.json().get('service')=='wechat-agent-bridge':
                        results['trusted_https']=True; break
                except (httpx.HTTPError,ValueError):
                    pass
                time.sleep(1)
            if results.get('trusted_https'):
                login=client.post(args.origin+'/api/v1/auth/login',headers={'Origin':args.origin},json=dict(username='acceptance',password=password))
                assert login.status_code==200
                assert 'Secure' in login.headers['set-cookie'] and 'HttpOnly' in login.headers['set-cookie']
                results['secure_cookie']=True
                response=client.get(args.origin+'/accounts')
                assert response.status_code==200 and '<div id="app">' in response.text
                results['spa_deep_link']=True
        owner=control.owner()[0]
        processes=[owner,*owner.children(recursive=True)]
        results['process_rss_mib']=round(sum(p.memory_info().rss for p in processes)/1024**2,1)
        results['process_count']=len(processes)
    finally:
        results['stop']=control.stop()
        results['stopped']=control.owner() is None
        (root/'data/portable-acceptance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
