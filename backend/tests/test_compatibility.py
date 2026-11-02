from migration_helpers import remove_v10
from contextlib import closing
from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

from backend.app.storage import Store
from backend.app.conversations import ConversationError
from backend.app.compatibility_storage import COMPATIBILITY_SCHEMA
from wechat_account_probe.models import Instance, ProbeError
from wechat_compatibility import require_profile, registry, profile_digest, validate_registry, bind_memory
from wechat_sender import compatibility
from test_roles import RoleTestCase
from backend.app.services import browser_values
from test_conversations import accounts, rows
import test_ai_execution as execution
import test_delivery as delivery
import test_group_scheduling as groups


class CompatibilityTests(RoleTestCase):
    configured = execution.ExecutionTests.configured
    ingest = execution.ExecutionTests.ingest
    start = execution.ExecutionTests.start
    wait_run = execution.ExecutionTests.wait_run
    delta = delivery.DeliveryTests.delta
    finish = delivery.DeliveryTests.finish
    settle = groups.GroupSchedulingTests.settle

    def test_background_reconcile_recovers_without_browser_or_manual_endpoint(self):
        manager = self.app.state.bridge.compatibility
        manager.enabled = True
        manager.store.compatibility_start()
        result = browser_values(accounts())
        result['client_instances'] = [deepcopy(i) for a in result['accounts'] for i in a['instances']]
        profile = registry()['profiles'][0]
        # All metadata originates from one exact synthetic inventory.
        for item in result['client_instances']:
            item.update(core_sha256=profile['builds'][0]['sha256'], profile_id=profile['id'])
        for account in result['accounts']:
            for item in account['instances']:
                item.update(core_sha256=profile['builds'][0]['sha256'], profile_id=profile['id'])
        bridge = self.app.state.bridge
        bridge.discovery.probe = lambda: result
        manager.sender = Mock()
        manager.sender.capability.return_value = dict(ready=True, code='ready')
        with patch.object(manager, 'check_processes', return_value=True):
            bridge.discovery.scan()
            pending=deepcopy(bridge.discovery.account(self.ids[0]))
            pending.sync_pending=True
            with patch.object(bridge.discovery,'account',return_value=pending):
                manager.reconcile()
                self.assertTrue(self.store.compatibility_blocked(self.ids[0]))
            for aid in self.ids:
                self.store.save_conversations(aid, dict(items=rows(1), status='complete', issues=[]))
                self.store.save_delta(aid, dict(groups=[dict(shard='@monitor', table_name='', generation='',
                                                           last_id=0, signature=[], items=[])], issues=[]))
            manager.reconcile()
            self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
            with patch.object(bridge.discovery,'account',return_value=pending):
                manager.reconcile()
                self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
                pending.sync_issues=['synthetic_sync_error']
                manager.reconcile()
                self.assertTrue(self.store.compatibility_blocked(self.ids[0]))
                pending.sync_issues=[]
                manager.reconcile()
                self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
            current = next(a for a in manager.query()['accounts'] if a['account_id'] == self.ids[0])
            self.assertEqual(current['auto_state'], 'ready')
            self.assertNotIn('resumable', current)
            self.assertEqual(self.post('compatibility/resume', account_id=self.ids[0], expected_revision=0).status_code, 404)
            manager.sender.capability.return_value = dict(ready=False, code='native_code_changed')
            manager.reconcile()
            self.assertTrue(self.store.compatibility_blocked(self.ids[0]))
            manager.sender.capability.return_value = dict(ready=True, code='ready')
            manager.reconcile()
            self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
            self.assertTrue(any(c.kwargs.get('refresh') for c in manager.sender.capability.call_args_list))

    def test_restart_with_same_identity_preserves_queued_reply_and_epoch(self):
        self.configured(); self.enable(); self.delta()
        run = self.wait_run(self.start())
        before = self.store.call(lambda: dict(self.store._ai_session(self.ids[0], self.cids[0])))
        self.store.compatibility_start()
        self.store.compatibility_observe(self.states, [])
        self.restore()
        after = self.store.call(lambda: dict(self.store._ai_session(self.ids[0], self.cids[0])))
        self.assertEqual(before, after)
        self.assertEqual([x['run_id'] for x in self.store.delivery_candidates()], [run['run_id']])

    def test_migration_accounts_recover_independently_and_persist_token(self):
        self.enable()
        self.store.migration_token = 'import-1'
        self.assertTrue(all(self.store.compatibility_blocked(aid) for aid in self.ids))
        self.restore(self.ids[0])
        self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
        self.assertTrue(self.store.compatibility_blocked(self.ids[1]))
        self.store.compatibility_start()
        self.store.compatibility_observe(self.states, [])
        self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
        self.store.migration_token = 'import-2'
        self.assertTrue(self.store.compatibility_blocked(self.ids[0]))

    def enable(self):
        self.states = {aid: dict(fingerprint='build-'+aid, readable=True, instances=[], capabilities={}) for aid in self.ids}
        self.store.compatibility_start()
        self.store.compatibility_observe(self.states, [])
        for aid in self.ids:
            self.restore(aid)

    def revision(self, aid=None):
        return next(r['revision'] for r in self.store.compatibility_rows() if r['scope'] == (aid or self.ids[0]))

    def restore(self, aid=None):
        aid = aid or self.ids[0]
        self.store.compatibility_resume(aid, self.revision(aid))

    def test_hold_is_persistent_and_manual_start_and_setting_changes_cannot_clear_it(self):
        self.configured(); self.enable()
        self.store.compatibility_pause(self.ids[0], 'build_changed')
        self.assertTrue(self.update(revision=self.preferences()['revision'],ai_enabled=True).json()['ai_enabled'])
        response = self.post('ai-runs/start', account_id=self.ids[0], conversation_id=self.cids[0], request_id='blocked')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'compatibility_paused')
        copy = self.path.with_name('restart.sqlite3')
        def backup():
            with closing(sqlite3.connect(copy)) as dest: self.store.db.backup(dest)
        self.store.call(backup)
        with closing(Store(copy)) as reopened:
            reopened.compatibility_start()
            reopened.compatibility_observe(self.states, [])
            self.assertTrue(reopened.compatibility_blocked(self.ids[0]))
        self.assertTrue(self.preferences()['ai_enabled'])

    def test_transient_readiness_preserves_group_mentions_without_sending_while_held(self):
        groups.GroupSchedulingTests.group(self,20);self.enable()
        aid,cid=self.ids[0],self.cids[0]
        wxid=self.store.account(aid)['wxid']
        for index,reason in enumerate(('native_busy','sync_pending','native_unavailable'),1):
            with self.subTest(reason=reason):
                self.store.compatibility_send_state(aid,False,reason)
                self.delta(index,mention_status='known',mentioned_wxids=[wxid],mentions_all=False)
                self.assertEqual(self.store.ai_candidates(),[])
                self.assertEqual(self.store.delivery_candidates(),[])
                self.store.compatibility_send_state(aid,True,'ready');self.restore()
                self.assertEqual(self.store.call(lambda:self.store._group_reason(aid,cid)),'mention')
                groups.GroupSchedulingTests.settle(self)
                self.assertEqual(len(self.store.ai_candidates()),1)
                run=self.wait_run(self.app.state.ai.start(aid,cid,'resumed-'+reason,'auto'))
                self.assertEqual(run['trigger_reason'],'mention')
                self.assertEqual(run['delivery']['status'],'queued')
                self.finish(run)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE conversation_id=?',(cid,)).fetchone()[0]),3)

    def test_transient_capture_does_not_bypass_identity_rebaseline(self):
        self.configured();self.enable()
        aid,cid=self.ids[0],self.cids[0]
        self.store.compatibility_send_state(aid,False,'native_busy')
        self.delta(1)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers').fetchone()[0]),1)
        # A subsequent identity change must discard the old epoch even if readiness
        # temporarily overwrites its visible reason before the baseline is rebuilt.
        self.states[aid]['fingerprint']='replacement-build'
        self.store.compatibility_observe(self.states,[])
        self.store.compatibility_send_state(aid,False,'native_busy')
        self.delta(2)
        self.store.compatibility_send_state(aid,True,'ready');self.restore()
        epoch=self.store.call(lambda:self.store._ai_session(aid,cid)['epoch'])
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE epoch=?',(epoch,)).fetchone()[0]),0)
        self.delta(3)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE epoch=?',(epoch,)).fetchone()[0]),1)

    def test_guard_readiness_race_preserves_epoch_and_pending_triggers(self):
        self.configured();self.enable();self.delta(1)
        aid,cid=self.ids[0],self.cids[0]
        epoch=self.store.call(lambda:self.store._ai_session(aid,cid)['epoch'])
        manager=self.app.state.bridge.compatibility
        manager.enabled=True;manager.sender=Mock()
        manager.sender.capability.return_value=dict(ready=False,code='native_busy')
        with patch.object(manager,'check_processes',return_value=True):
            with self.assertRaises(ConversationError): manager.guard(aid)
        self.delta(2)
        self.assertEqual(self.store.call(lambda:self.store._ai_session(aid,cid)['epoch']),epoch)
        self.assertEqual(self.store.ai_candidates(),[])
        self.store.compatibility_send_state(aid,True,'ready');self.restore()
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE epoch=?',(epoch,)).fetchone()[0]),2)

    def test_inflight_ai_result_cannot_change_memory_or_queue_send_after_hold(self):
        self.configured(); self.enable(); self.delta()
        previous = self.preferences()['memory']
        generate = self.provider.generate
        def finish_after_change(*args):
            self.store.compatibility_pause(self.ids[0], 'build_changed')
            return generate(*args)
        with patch.object(self.provider, 'generate', side_effect=finish_after_change):
            run = self.wait_run(self.start())
        self.assertEqual(run['status'], 'cancelled')
        self.assertIn('代次已变化',run['message'])
        self.assertEqual(self.preferences()['memory'], previous)
        self.assertEqual(self.store.delivery_candidates(), [])
        self.assertTrue(self.store.compatibility_blocked(self.ids[0]))

    def test_model_result_during_transient_hold_commits_memory_and_ordered_queue(self):
        from test_reply_media import encoded
        self.configured();self.enable()
        aid,cid=self.ids[0],self.cids[0]
        reply='第一条\n[[SPLIT]]\n[[IMAGE:data:image/png;base64,'+encoded()+']]\n[[SPLIT]]\n[[STICKER:data:image/png;base64,'+encoded()+']]'
        for index,reason in enumerate(('native_busy','sync_pending','sync_error','native_unavailable'),1):
            with self.subTest(reason=reason):
                self.delta(index*100)
                before=self.preferences()['memory_revision']
                def generate(*args):
                    self.store.compatibility_send_state(aid,False,reason)
                    return dict(memory='暂停期间完成'+reason,reply=reply)
                with patch.object(self.provider,'generate',side_effect=generate) as provider:
                    run=self.wait_run(self.start())
                self.assertEqual(run['status'],'completed',run)
                self.assertEqual(self.preferences()['memory_revision'],before+1)
                self.assertEqual([p['kind'] for p in run['reply_parts']],['text','image','sticker'])
                self.assertEqual(run['delivery']['status'],'queued')
                self.assertEqual(self.store.delivery_candidates(),[])
                self.assertIsNone(self.store.claim_delivery(run['run_id'],0))
                self.store.compatibility_send_state(aid,True,'ready');self.restore()
                for part in range(3):
                    self.assertEqual(self.store.delivery_candidates()[0]['part_index'],part)
                    job=self.store.claim_delivery(run['run_id'],part)
                    self.assertIsNone(self.store.claim_delivery(run['run_id'],part))
                    evidence={}
                    if part:
                        item=self.delta(index*100+part,type=job['kind'],is_self=True,content={})
                        created=int(datetime.fromisoformat(item['sent_at']).timestamp())
                        self.store.call(lambda:self.store.db.execute('UPDATE messages SET server_id=? WHERE local_id=? AND account_id=?',(str(index*100+part),item['_ref']['local_id'],aid)))
                        evidence=dict(token='f'*32,local_id=item['_ref']['local_id'],create_time=created,timestamp_ms=created*1000,
                                      source_uuid='12345678-1234-1234-1234-123456789abc',message_type=3 if part==1 else 47,target=job['target'])
                    self.store.finish_delivery(job,dict(status='sent',evidence=evidence))
                self.assertEqual(self.store.delivery_candidates(),[])
                self.assertEqual(provider.call_count,1)

    def test_compatibility_diagnostics_record_only_changes_and_preserve_business_on_failure(self):
        self.enable();aid=self.ids[0]
        def count():
            return self.store.call(lambda:self.store.db.execute("SELECT count(*) FROM diagnostic_logs WHERE json_extract(payload,'$.operation')='compatibility'").fetchone()[0])
        before=count()
        self.store.compatibility_send_state(aid,False,'native_busy')
        self.assertGreater(count(),before)
        before=count()
        self.store.compatibility_send_state(aid,False,'native_busy')
        self.assertEqual(count(),before)
        with patch.object(self.store,'_log_put',side_effect=sqlite3.OperationalError('synthetic')):
            self.store.compatibility_send_state(aid,False,'sync_error')
        self.assertTrue(self.store.compatibility_blocked(aid))
        self.assertIn('诊断未能保存',self.store.log_warning)

    def test_group_threshold_model_finishes_during_sync_hold(self):
        groups.GroupSchedulingTests.group(self,2);self.enable()
        self.delta(1);self.delta(2)
        def generate(*args):
            self.store.compatibility_send_state(self.ids[0],False,'sync_pending')
            return dict(memory='群聊新记忆',reply='收到大家的消息')
        with patch.object(self.provider,'generate',side_effect=generate):
            run=groups.GroupSchedulingTests.start(self)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['trigger_reason'],'group_threshold')
        self.assertEqual(run['delivery']['status'],'queued')
        self.assertEqual(self.store.delivery_candidates(),[])
        self.store.compatibility_send_state(self.ids[0],True,'ready');self.restore()
        self.finish(run)
        self.assertEqual(self.store.delivery_candidates(),[])

    def test_paused_result_still_rejects_real_disable_or_configuration_change(self):
        self.configured();self.enable();self.delta()
        for disabled in (False,True):
            with self.subTest(disabled=disabled):
                before=self.preferences()['memory_revision']
                def generate(*args):
                    self.store.compatibility_send_state(self.ids[0],False,'native_busy')
                    if disabled: self.update(self.preferences()['revision'],ai_enabled=False)
                    else:
                        def change():
                            with self.store.db: self.store.db.execute('UPDATE ai_settings SET revision=revision+1')
                        self.store.call(change)
                    return dict(memory='不可应用的记忆',reply='不可发送')
                with patch.object(self.provider,'generate',side_effect=generate):
                    run=self.wait_run(self.start())
                self.assertEqual(run['status'],'cancelled' if disabled else 'conflicted',run)
                self.assertEqual(self.preferences()['memory_revision'],before)
                self.assertEqual(run['delivery']['total_parts'],0)
                self.store.compatibility_send_state(self.ids[0],True,'ready');self.restore()

    def test_held_completed_reply_survives_restart_without_reinvoking_model(self):
        self.configured();self.enable();self.delta()
        def generate(*args):
            self.store.compatibility_send_state(self.ids[0],False,'native_busy')
            return dict(memory='已经保存',reply='待发送的回复')
        with patch.object(self.provider,'generate',side_effect=generate):run=self.wait_run(self.start())
        copy=self.path.with_name('held-restart.sqlite3')
        def backup():
            with closing(sqlite3.connect(copy)) as dest:self.store.db.backup(dest)
        self.store.call(backup)
        with closing(Store(copy)) as reopened:
            reopened.compatibility_start();reopened.compatibility_observe(self.states,[])
            self.assertEqual(reopened.delivery_candidates(),[])
            state=reopened.compatibility_send_state(self.ids[0],True,'ready')
            reopened.compatibility_resume(self.ids[0],state['revision'])
            self.assertEqual(reopened.delivery_candidates()[0]['run_id'],run['run_id'])
            job=reopened.claim_delivery(run['run_id'],0)
            reopened.finish_delivery(job,dict(status='sent'))
            self.assertEqual(reopened.delivery_candidates(),[])
            self.assertEqual(reopened.ai_candidates(),[])

    def test_unknown_send_survives_recovery_and_old_queue_and_triggers_do_not_replay(self):
        self.configured(); self.enable(); self.delta()
        run = self.wait_run(self.start()); self.finish(run, 'unknown')
        def extra():
            with self.store.db:
                self.store.db.execute("INSERT INTO deliveries(run_id,part_index,account_id,conversation_id,epoch,target,text,status,attempts,code,confirmation,archive_message_id,created_at) SELECT run_id,1,account_id,conversation_id,epoch,target,'queued part','queued',0,NULL,NULL,NULL,created_at FROM deliveries WHERE run_id=? AND part_index=0", (run['run_id'],))
        self.store.call(extra)
        self.store.compatibility_pause(self.ids[0], 'build_changed')
        self.delta(2)
        revision = self.revision()
        with self.assertRaises(ConversationError):
            self.store.compatibility_resume(self.ids[0], revision-1)
        self.restore()
        states = self.store.call(lambda: [r[0] for r in self.store.db.execute('SELECT status FROM deliveries ORDER BY part_index')])
        self.assertEqual(states, ['unknown','cancelled'])
        self.assertEqual(self.store.delivery_candidates(), [])
        epoch = self.store.call(lambda: self.store._ai_session(self.ids[0],self.cids[0])['epoch'])
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE epoch=?',(epoch,)).fetchone()[0]),0)
        self.delta(3)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_triggers WHERE epoch=?',(epoch,)).fetchone()[0]),1)

    def test_unknown_instance_holds_all_then_accounts_resume_independently(self):
        self.enable()
        self.store.compatibility_observe(self.states, [dict(pid=999, created='1', version='unknown')])
        self.assertTrue(all(self.store.compatibility_blocked(aid) for aid in self.ids))
        self.store.compatibility_observe(self.states, [])
        self.restore(self.ids[0])
        self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
        self.assertTrue(self.store.compatibility_blocked(self.ids[1]))

    def test_v9_upgrade_failure_preserves_v8_and_retry_preserves_accounts(self):
        path = self.path.with_name('v8.sqlite3')
        def backup():
            with closing(sqlite3.connect(path)) as db: self.store.db.backup(db)
        self.store.call(backup)
        with closing(sqlite3.connect(path)) as db, db:
            remove_v10(db)
            db.execute('DROP TABLE compatibility_state')
            db.execute('DELETE FROM schema_migrations WHERE version=9')
            db.execute('PRAGMA user_version=8')
        with patch('backend.app.compatibility_storage.COMPATIBILITY_SCHEMA', (*COMPATIBILITY_SCHEMA, 'INVALID SQL')):
            with self.assertRaises(ConversationError): Store(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='compatibility_state'").fetchone())
        with closing(Store(path)) as store:
            self.assertEqual(len(store.accounts()),2)
            self.assertEqual(store.call(lambda:store.db.execute('PRAGMA user_version').fetchone()[0]),11)


class ProfileTests(unittest.TestCase):
    def test_incomplete_profiles_and_abi_fail_closed(self):
        for change in ('empty_builds', 'missing_function', 'wrong_abi', 'no_readback', 'out_of_image'):
            value = deepcopy(registry())
            profile = value['profiles'][1]
            if change == 'empty_builds': profile['builds'] = []
            elif change == 'missing_function': del profile['native']['rvas']['schedule']
            elif change == 'wrong_abi': profile['native']['abi'] = 'unknown'
            elif change == 'no_readback': profile['capabilities']['messages'] = False
            else: profile['native']['rvas']['schedule'] = profile['builds'][0]['image_size']
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_registry(value)

    def test_changed_file_or_mapped_header_cannot_bind_account_layout(self):
        profile = registry()['profiles'][1]
        build = profile['builds'][0]
        memory = Mock()
        memory.instance = Instance(1, 2, 3, profile['version'], build['sha256'])
        identity = dict(core_sha256='changed', timestamp=build['timestamp'], image_size=build['image_size'])
        with patch('wechat_compatibility.file_identity', return_value=identity):
            with self.assertRaises(ProbeError): bind_memory(memory, {'Weixin.dll':0x1000})
            memory.read.assert_not_called()
            identity['core_sha256'] = build['sha256']
            memory.read.return_value = bytes(4096)
            with self.assertRaises(ProbeError): bind_memory(memory, {'Weixin.dll':0x1000})

    def test_same_version_with_unknown_hash_is_rejected(self):
        with self.assertRaises(ProbeError): require_profile(Instance(1,2,3,'4.1.15.56','0'*64),'account')
        profile = registry()['profiles'][0]
        self.assertEqual(require_profile(Instance(1,2,3,profile['version'],profile['builds'][0]['sha256']),'account')['id'],profile['id'])

    def test_native_header_is_generated_from_registry(self):
        from scripts.generate_compatibility import render, ROOT
        self.assertEqual((ROOT/'native_send/core_version.h').read_text(encoding='utf-8'),render())

    def test_mixed_build_records_bind_each_account_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'acceptance.json'
            first=registry()['profiles'][0]
            second=deepcopy(registry()['profiles'][1]); second['capabilities']['send_text']=True
            records=[]
            for account,profile in [('one',first),('two',second)]:
                records.append(dict(account=account,core_sha256=profile['builds'][0]['sha256'],profile_digest=profile_digest(profile),
                                    dll_sha256='dll',binding='machine-user-release',passed=True,checks={n:'passed' for n in compatibility.REQUIRED_CHECKS}))
            path.write_text(json.dumps(dict(version=2,records=records)))
            by_hash={p['builds'][0]['sha256']:p for p in (first,second)}
            with patch.object(compatibility,'ACCEPTANCE',path), patch.object(compatibility,'machine_binding',return_value='machine-user-release'), \
                    patch.object(compatibility,'profile_for_hash',side_effect=by_hash.get):
                for r in records: self.assertTrue(compatibility.accepted(r['account'],r['core_sha256'],'dll'))
                self.assertFalse(compatibility.accepted('one',records[1]['core_sha256'],'dll'))
                second['revision']+=1
                self.assertFalse(compatibility.accepted('two',records[1]['core_sha256'],'dll'))
                with patch.object(compatibility,'machine_binding',return_value='other-user'):
                    self.assertFalse(compatibility.accepted('one',records[0]['core_sha256'],'dll'))
