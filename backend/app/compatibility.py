"""Capabilities and explicit recovery for exact WeChat builds."""
from dataclasses import asdict
import hashlib
import json
import threading

from pydantic import BaseModel, Field
from .models import Instance
from .role_models import JsonRequest
from .conversations import ConversationError
from wechat_compatibility import CAPABILITIES, profile_for_hash, profile_digest

REASONS = {
    'checking': '正在核对本机微信构建。',
    'not_checked': '尚未完成兼容检查。',
    'unidentified_client': '存在未确认的微信实例，自动处理已暂停。',
    'process_changed': '微信进程或构建已变化，正在重新识别。',
    'build_changed': '微信构建或运行实例已变化，请检查后恢复。',
    'unsupported_build': '当前精确构建尚未完成适配。',
    'source_offline': '微信离线，历史归档仍可使用。',
    'native_acceptance_required': '读取可用，请在启动器完成本机发送验收。',
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
    resumable: bool = False
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


class CompatibilityResume(JsonRequest):
    account_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


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
            self.store.compatibility_pause(aid, capability['code'])
            self.store.call(self.store._compatibility_require, aid)

    def query(self, refresh=True, refresh_sender=False):
        if refresh:
            self.check_processes()
        rows = {r['scope']: r for r in self.store.compatibility_rows()}
        global_state = rows.get('*', dict(held=self.enabled, reason='not_checked', snapshot='{}'))
        result = []
        for aid, row in rows.items():
            if aid == '*':
                continue
            snapshot = json.loads(row['snapshot'])
            caps = snapshot.get('capabilities', {name:False for name in CAPABILITIES})
            account = self.bridge.discovery.account(aid)
            send = self.sender.capability(aid, refresh=refresh_sender) if self.sender else dict(ready=False, code='not_checked')
            if self.enabled:
                row = self.store.compatibility_send_state(aid, send['ready'], send.get('code', 'not_checked'))
            reason = row['reason']
            if not send['ready'] and account.source_state == 'online':
                reason = send['code']
            ready = (all(caps.values()) and send['ready'] and len(account.instances) == 1
                     and account.source_state == 'online' and not account.sync_pending and not account.sync_issues
                     and not global_state['held'] and not self.store.migration_hold)
            result.append(dict(account_id=aid, nickname=account.nickname, revision=row['revision'], held=bool(row['held']), reason=reason,
                               message=REASONS.get(reason, '兼容检查未通过。') if reason else '兼容检查通过。',
                               resumable=bool(ready and row['held']), capabilities=caps,
                               instances=snapshot.get('instances',[]), send_ready=send['ready']))
        reason = global_state['reason']
        return dict(enforced=self.enabled, held=bool(global_state['held']), reason=reason,
                    message=REASONS.get(reason, '兼容检查未通过。') if reason else '本机微信已识别。',
                    migration_pending=self.store.migration_hold, accounts=result,
                    unresolved_instances=json.loads(global_state['snapshot']).get('instances', []))

    def resume(self, aid, expected_revision):
        if not self.enabled:
            raise ConversationError('compatibility_not_enabled', '当前测试后端未启用真实微信兼容检查。', 409)
        with self.lock:
            self.check_processes()
            state = next((a for a in self.query(refresh=False, refresh_sender=True)['accounts'] if a['account_id'] == aid), None)
            if not state or state['revision'] != expected_revision:
                raise ConversationError('revision_conflict', '兼容状态已变化，请重新检查。', 409)
            if not state['resumable']:
                raise ConversationError('compatibility_not_ready', '请先完成同步、兼容检查和本机发送验收。', 409)
            for row in self.store.call(lambda: self.store.db.execute('SELECT conversation_id FROM conversation_preferences WHERE account_id=? AND ai_enabled=1',(aid,)).fetchall()):
                if not self.store.preferences(aid,row[0])['effective']['ready']:
                    raise ConversationError('ai_configuration_required', '仍有会话的 API 或角色配置未就绪。', 409)
            self.store.compatibility_resume(aid, expected_revision)
            return self.query(refresh=False)
