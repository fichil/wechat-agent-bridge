"""Capabilities and explicit recovery for exact WeChat builds."""
from dataclasses import asdict
import hashlib
import json
import threading

from pydantic import BaseModel, Field
from typing import Literal
from .models import Instance
from .role_models import JsonRequest
from .conversations import ConversationError
from .compatibility_storage import TRANSIENT_SEND_HOLDS
from wechat_compatibility import CAPABILITIES, profile_for_hash, profile_digest

REASONS = {
    'checking': '正在核对本机微信构建。',
    'not_checked': '尚未完成兼容检查。',
    'unidentified_client': '存在未确认的微信实例，自动处理已暂停。',
    'process_changed': '微信进程或构建已变化，正在重新识别。',
    'build_changed': '微信构建或运行实例已变化，正在自动检查。',
    'unsupported_build': '当前精确构建尚未完成适配。',
    'source_offline': '微信离线，历史归档仍可使用。',
    'sync_pending': '等待同步完成后自动继续。',
    'source_ambiguous': '等待账号唯一绑定。',
    'configuration_pending': '部分会话配置未就绪；其他就绪会话继续运行。',
    'native_unavailable': '发送检查暂未通过，将自动重试。',
    'native_build_stale': '发送组件与兼容档案不一致，请更新完整程序包。',
    'native_build_changed': '发送组件校验失败。',
    'native_code_changed': '微信已加载代码发生变化。',
    'probe_failed': '无法确认微信身份，请重新识别。',
}


class CompatibilityAccount(BaseModel):
    account_id: str
    nickname: str | None = None
    revision: int
    held: bool
    reason: str
    message: str
    auto_state: Literal["checking", "ready", "blocked"] = "checking"
    capabilities: dict[str, bool]
    instances: list[Instance] = Field(default_factory=list)
    send_ready: bool = False


class CompatibilitySnapshot(BaseModel):
    enforced: bool
    held: bool
    reason: str
    message: str
    migration_pending: bool
    accounts: list[CompatibilityAccount]
    unresolved_instances: list[Instance]


def signature(instances):
    return tuple(sorted((i['pid'], int(i['created']), i.get('core_sha256',''), i.get('version','')) for i in instances))


class Compatibility:
    def __init__(self, bridge, enabled=False):
        self.bridge, self.store, self.enabled = bridge, bridge.store, enabled
        self.lock = threading.RLock()
        self.seen = None
        self.sender = None
        if enabled:
            self.store.compatibility_start()

    def observe(self, result):
        if not self.enabled:
            return
        with self.lock:
            instances = result.get('client_instances', [])
            states, bound = {}, set()
            for account in result.get('accounts', []):
                aid = account.get('account_id')
                if not aid:
                    continue
                current = account.get('instances', [])
                caps = {name: bool(current) for name in CAPABILITIES}
                revisions = []
                for item in current:
                    profile = profile_for_hash(item.get('core_sha256',''))
                    valid = profile and profile['version'] == item['version']
                    for name in caps:
                        caps[name] = caps[name] and bool(valid and profile['capabilities'][name])
                    if valid:
                        revisions.append(profile_digest(profile))
                    bound.add((item['pid'], int(item['created'])))
                fingerprint = hashlib.sha256(json.dumps([signature(current), sorted(revisions)]).encode()).hexdigest()
                states[aid] = dict(fingerprint=fingerprint, readable=caps['account'] and caps['messages'],
                                   capabilities=caps, instances=current)
            # An explicitly observed logged-out supported instance cannot own archived accounts.
            skipped = {(i['pid'], int(i['created'])) for a in result.get('skipped_instances', [])
                       if a.get('status') == 'not_logged_in' for i in a.get('instances', [])}
            unresolved = [i for i in instances if (i['pid'], int(i['created'])) not in bound | skipped]
            self.store.compatibility_observe(states, unresolved)
            if result.get('issues') or result.get('error'):
                self.store.compatibility_hold('probe_failed', dict(instances=unresolved))
            self.seen = signature(instances)

    def check_processes(self):
        if not self.enabled:
            return True
        from wechat_account_probe.native import enumerate_instances
        try:
            instances, issues = enumerate_instances()
            values = [dict(asdict(i), created=str(i.created)) for i in instances]
            with self.lock:
                if issues or signature(values) != self.seen:
                    if issues or self.seen is not None:
                        self.store.compatibility_hold('process_changed', dict(instances=values))
                    self.bridge.discovery.start()
                    return False
            return True
        except Exception:
            self.store.compatibility_hold('probe_failed')
            return False

    def guard(self, aid):
        if not self.enabled:
            return
        self.check_processes()
        self.store.call(self.store._compatibility_require, aid)
        capability = self.sender.capability(aid, refresh=True) if self.sender else dict(ready=False, code='not_checked')
        if not capability['ready']:
            if capability['code'] in TRANSIENT_SEND_HOLDS:
                self.store.compatibility_send_state(aid, False, capability['code'])
            else:
                self.store.compatibility_pause(aid, capability['code'])
            self.store.call(self.store._compatibility_require, aid)

    def reconcile(self):
        """Run by the background readiness owner, never dependent on a browser query."""
        if not self.enabled or self.sender is None or self.store.deployment_hold:
            return
        with self.lock:
            if not self.check_processes():
                return
            rows = self.store.compatibility_rows()
            for row in rows:
                aid = row['scope']
                if aid == '*':
                    continue
                try:
                    snapshot = json.loads(row['snapshot'])
                    account = self.bridge.discovery.account(aid)
                    caps = snapshot.get('capabilities', {})
                    reason = ('source_offline' if account.source_state != 'online' else
                              'source_ambiguous' if len(account.instances) != 1 else
                              'unsupported_build' if not all(caps.get(k) for k in ('account', 'messages', 'send_text')) else
                              'sync_pending' if account.sync_pending or account.sync_issues else '')
                    send = self.sender.capability(aid, refresh=True) if not reason else dict(ready=False, code=reason)
                    state = self.store.compatibility_send_state(aid, send['ready'], send.get('code', 'native_unavailable'))
                    if send['ready']:
                        self.store.compatibility_resume(aid, state['revision'])
                except ConversationError:
                    continue  # Identity/revision may change during inspection; retry next pass.

    def query(self, refresh=True, refresh_sender=False):
        rows = {r['scope']: r for r in self.store.compatibility_rows()}
        global_state = rows.get('*', dict(held=self.enabled, reason='not_checked', snapshot='{}'))
        result = []
        pending = False
        for aid, row in rows.items():
            if aid == '*':
                continue
            snapshot = json.loads(row['snapshot'])
            caps = snapshot.get('capabilities', {name:False for name in CAPABILITIES})
            account = self.bridge.discovery.account(aid)
            send = snapshot.get('send', dict(ready=False, code='not_checked'))
            migration = self.store.call(self.store.migration_pending_for, aid)
            pending = pending or migration
            held = bool(row['held'] or global_state['held'] or migration or self.store.deployment_hold)
            reason = (global_state['reason'] if global_state['held'] else row['reason']) or ('checking' if held else '')
            missing = self.store.call(lambda: any(not self.store.configuration_ready(aid, cid) for cid, in self.store.db.execute(
                'SELECT conversation_id FROM conversation_preferences WHERE account_id=? AND ai_enabled=1', (aid,))))
            display_reason = reason or ('configuration_pending' if missing else '')
            result.append(dict(account_id=aid, nickname=account.nickname, revision=row['revision'], held=held,
                               reason=display_reason, message=REASONS.get(display_reason, '自动检查未通过，将自动重试。') if display_reason else '自动检查通过，已就绪。',
                               auto_state='checking' if reason in ('checking','not_checked','sync_pending','process_changed') else 'blocked' if held or missing else 'ready',
                               capabilities=caps, instances=snapshot.get('instances', []), send_ready=send['ready']))
        reason = global_state['reason']
        return dict(enforced=self.enabled, held=bool(global_state['held']), reason=reason,
                    message=REASONS.get(reason, '自动检查尚未通过。') if reason else '本机微信已识别。',
                    migration_pending=pending, accounts=result,
                    unresolved_instances=json.loads(global_state['snapshot']).get('instances', []))
