from contextlib import closing
from copy import deepcopy
import json
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


class CompatibilityTests(RoleTestCase):
    configured = execution.ExecutionTests.configured
    ingest = execution.ExecutionTests.ingest
    start = execution.ExecutionTests.start
    wait_run = execution.ExecutionTests.wait_run
    delta = delivery.DeliveryTests.delta
    finish = delivery.DeliveryTests.finish

    def test_api_resume_requires_current_revision_sync_and_fresh_acceptance(self):
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
            for aid in self.ids:
                self.store.save_conversations(aid, dict(items=rows(1), status='complete', issues=[]))
                self.store.save_delta(aid, dict(groups=[dict(shard='@monitor', table_name='', generation='',
                                                           last_id=0, signature=[], items=[])], issues=[]))
            response = self.post('compatibility/query')
            self.assertEqual(response.status_code, 200, response.text)
            current = next(a for a in response.json()['accounts'] if a['account_id'] == self.ids[0])
            self.assertTrue(current['resumable'], current)
            stale = self.post('compatibility/resume', account_id=self.ids[0], expected_revision=current['revision']-1)
            self.assertEqual(stale.json()['code'], 'revision_conflict')
            manager.sender.capability.return_value = dict(ready=False, code='native_acceptance_required')
            rejected = self.post('compatibility/resume', account_id=self.ids[0], expected_revision=current['revision'])
            self.assertEqual(rejected.json()['code'], 'revision_conflict')
            unavailable = next(a for a in self.post('compatibility/query').json()['accounts'] if a['account_id'] == self.ids[0])
            rejected = self.post('compatibility/resume', account_id=self.ids[0], expected_revision=unavailable['revision'])
            self.assertEqual(rejected.json()['code'], 'compatibility_not_ready')
            self.assertTrue(any(c.kwargs.get('refresh') is True for c in manager.sender.capability.call_args_list))
            manager.sender.capability.return_value = dict(ready=True, code='ready')
            current = next(a for a in self.post('compatibility/query').json()['accounts'] if a['account_id'] == self.ids[0])
            restored = self.post('compatibility/resume', account_id=self.ids[0], expected_revision=current['revision'])
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertFalse(self.store.compatibility_blocked(self.ids[0]))
            self.assertTrue(self.store.compatibility_blocked(self.ids[1]))

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
        self.assertEqual(self.preferences()['memory'], previous)
        self.assertEqual(self.store.delivery_candidates(), [])
        self.assertTrue(self.store.compatibility_blocked(self.ids[0]))

    def test_unknown_send_survives_recovery_and_old_queue_and_triggers_do_not_replay(self):
        self.configured(); self.enable(); self.delta()
        run = self.wait_run(self.start()); self.finish(run, 'unknown')
        def extra():
            with self.store.db:
                self.store.db.execute("INSERT INTO deliveries SELECT run_id,1,account_id,conversation_id,epoch,target,'queued part','queued',0,NULL,NULL,NULL,created_at FROM deliveries WHERE run_id=? AND part_index=0", (run['run_id'],))
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
            self.assertEqual(store.call(lambda:store.db.execute('PRAGMA user_version').fetchone()[0]),9)


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
