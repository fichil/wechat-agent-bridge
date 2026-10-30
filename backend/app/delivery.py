"""Independent per-account sender; model work and SQLite never execute native code."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from .conversations import ConversationError

MESSAGES = {
    'ready': '原生发送已通过本机验收。',
    'source_offline': '微信离线，待发送回复将保留。',
    'source_ambiguous': '账号对应多个进程，等待唯一绑定。',
    'native_acceptance_required': '发送通道尚未通过本机无干扰验收。',
    'native_version_unsupported': '当前微信构建尚未适配，自动发送已阻止。',
    'native_build_stale': '发送模块源码已变化，需要重新构建及验收。',
    'native_build_changed': '发送模块校验失败。',
    'native_code_changed': '微信已加载代码与已验证文件不一致，发送通道不可用。',
    'identity_unavailable': '无法确认微信账号对象布局，发送通道不可用。',
    'native_unavailable': '原生发送暂不可用，回复保留在队列中。',
    'source_identity_changed': '微信登录身份已变化，等待重新识别。',
    'native_busy': '微信原生发送仍在处理上一项任务。',
}


class NativeTransport:
    def invoke(self, request):
        if os.name!='nt': return dict(ready=False,status='failed',code='native_unavailable')
        child=subprocess.Popen([sys.executable,'-B','-m','wechat_sender.worker'],
            cwd=Path(__file__).resolve().parents[2],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        # No target thread is terminated on timeout. The worker performs its own bounded waits.
        try:
            output,_=child.communicate(json.dumps(request,ensure_ascii=True).encode()+b'\n',timeout=150)
        except subprocess.TimeoutExpired:
            threading.Thread(target=child.communicate,daemon=True).start()
            return dict(ready=False,status='unknown',code='native_result_uncertain')
        try: return json.loads(output)
        except (ValueError,UnicodeError): return dict(ready=False,status='unknown',code='native_result_uncertain')

    def inspect(self, binding):
        return self.invoke(dict(binding,operation='inspect'))

    def send(self, binding, job):
        return self.invoke(dict(binding,operation='send',target=job['target'],text=job['text']))


class DeliveryExecution:
    def __init__(self,store,bridge,transport=None,automatic=True):
        self.store,self.bridge=store,bridge
        self.transport=transport or NativeTransport()
        self.stop=threading.Event(); self.lock=threading.RLock(); self.pending=set(); self.cache={}
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='wechat-send')
        self.thread=None
        if automatic:
            self.thread=threading.Thread(target=self.loop,name='delivery-scheduler',daemon=True); self.thread.start()

    def binding(self, aid):
        account=self.bridge.discovery.online(aid)
        if len(account.instances)!=1: raise ConversationError('source_ambiguous',MESSAGES['source_ambiguous'],503)
        instance=account.instances[0]
        return dict(account=account.wxid,pid=instance.pid,created=instance.created)

    def capability(self, aid, refresh=False):
        try:
            binding=self.binding(aid)
            key=(aid,binding['pid'],binding['created'])
            with self.lock: cached=self.cache.get(key)
            if cached and not refresh and time.monotonic()-cached[0]<10: result=cached[1]
            else:
                result=self.transport.inspect(binding)
                with self.lock: self.cache[key]=(time.monotonic(),result)
            code='ready' if result.get('ready') else result.get('code','native_acceptance_required')
        except ConversationError as exc: code=exc.code
        except Exception: code='native_unavailable'
        return dict(account_id=aid,ready=code=='ready',code=code,message=MESSAGES.get(code,MESSAGES['native_unavailable']))

    def perform(self, candidate):
        job=None
        try:
            if self.stop.is_set(): return
            aid=candidate['account_id']
            if not self.capability(aid)['ready']: return
            binding=self.binding(aid)
            job=self.store.claim_delivery(candidate['run_id'],candidate['part_index'])
            if job is None: return
            result=self.transport.send(binding,job)
            self.store.finish_delivery(job,result)
            if result.get('status')!='sent':
                with self.lock: self.cache.clear()
        except Exception:
            if job:
                self.store.finish_delivery(job,dict(status='unknown',code='native_result_uncertain'))
        finally:
            with self.lock: self.pending.discard(candidate['account_id'])

    def tick(self):
        for job in self.store.delivery_candidates():
            with self.lock:
                if self.stop.is_set() or len(self.pending)>=2: return
                if job['account_id'] in self.pending: continue
                self.pending.add(job['account_id'])
                self.executor.submit(self.perform,job)

    def loop(self):
        while not self.stop.wait(1):
            try: self.tick()
            except Exception: pass

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=3)
        self.executor.shutdown(wait=True)
