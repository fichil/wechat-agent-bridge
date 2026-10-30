"""Exercise an extracted package on a clean Windows host, with synthetic data.

Run only against a new disposable package directory. Never imports personal data
or sends WeChat messages. HTTPS origin must belong to the operator.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--origin',default='http://127.0.0.1:18765')
    parser.add_argument('--private',action='store_true')
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
    def rpc(**value):
        return subprocess.run([sys.executable,'-B','-X','utf8','-m','bridge_portable.rpc'],cwd=root,
                              input=json.dumps(value),capture_output=True,encoding='utf-8',timeout=90,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    initialized=rpc(action='setup',origin=args.origin,username='acceptance',password=password,confirmation=password,private=args.private)
    if initialized.returncode: raise RuntimeError(initialized.stdout)
    results=dict(runtime=sys.version.split()[0],initialized=True,origin=args.origin,mode='private' if args.private else 'production')
    try:
        with socket.socket() as occupied:
            occupied.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            occupied.bind(('127.0.0.1',8765)); occupied.listen()
            refused=rpc(action='start')
            results['port_conflict']=refused.returncode!=0 and '8765' in refused.stdout and control.owner() is None
        started=rpc(action='start')
        if started.returncode: raise RuntimeError(started.stdout)
        results['start']=started.stdout.strip()
        # The management process has exited; its services must remain alive.
        time.sleep(2)
        results['detached_backend']=bool(control.status().get('backend'))
        results['second_start']=control.start()
        with LocalApi(args.origin,'acceptance',password) as api:
            assert api.post('accounts/query')['accounts']==[]
            results['authenticated_local_api']=True
        with httpx.Client(timeout=3,trust_env=False) as client:
            for _ in range(40):
                try:
                    response=client.get(args.origin+'/api/v1/health')
                    if response.status_code==200 and response.json().get('service')=='wechat-agent-bridge':
                        results['private_access' if args.private else 'trusted_https']=True; break
                except (httpx.HTTPError,ValueError):
                    pass
                time.sleep(1)
            if results.get('trusted_https') or results.get('private_access'):
                login=client.post(args.origin+'/api/v1/auth/login',headers={'Origin':args.origin},json=dict(username='acceptance',password=password))
                assert login.status_code==200
                cookie=login.headers['set-cookie']
                assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
                assert ('Secure' in cookie) is not args.private
                results['cookie_policy']=True
                from websockets.sync.client import connect
                origin=args.origin.replace('https://','wss://').replace('http://','ws://')
                with connect(origin+'/api/v1/events',origin=args.origin,additional_headers={'Cookie':'; '.join(f'{k}={v}' for k,v in client.cookies.items())},proxy=None,open_timeout=5) as ws:
                    ws.send(json.dumps(dict(type='subscribe',account_ids=[])))
                    assert json.loads(ws.recv(timeout=5))['type']=='ready'
                    results['websocket']=True
                response=client.get(args.origin+'/accounts')
                assert response.status_code==200 and '<div id="app">' in response.text
                results['spa_deep_link']=True
                new_password=secrets.token_urlsafe(32)
                reset=rpc(action='reset',password=new_password,confirmation=new_password)
                assert reset.returncode==0
                assert client.post(args.origin+'/api/v1/accounts/query',headers={'Origin':args.origin,'X-CSRF-Token':login.json()['csrf_token']},json={}).status_code==401
                with LocalApi(args.origin,'acceptance',new_password) as api:
                    api.post('accounts/query')
                    results['password_reset']=True
        owner=control.owner()[0]
        processes=[owner,*owner.children(recursive=True)]
        results['process_rss_mib']=round(sum(p.memory_info().rss for p in processes)/1024**2,1)
        results['process_count']=len(processes)
        if args.private:
            pids={p.pid for p in processes}
            import psutil
            listeners=[c.laddr for c in psutil.net_connections(kind='tcp') if c.pid in pids and c.status=='LISTEN']
            results['private_loopback_bind']=bool(listeners) and all(a.ip=='127.0.0.1' for a in listeners) and {a.port for a in listeners}=={8765,18765}
    finally:
        results['stop']=control.stop()
        results['stopped']=control.owner() is None
        required=['initialized','port_conflict','detached_backend','authenticated_local_api','cookie_policy','websocket','password_reset','spa_deep_link','stopped']
        required+=['private_access','private_loopback_bind'] if args.private else ['trusted_https']
        results['passed']=all(results.get(key) is True for key in required)
        (root/'data/portable-acceptance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=False),flush=True)
    if not results['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
