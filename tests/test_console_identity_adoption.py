import contextlib
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from scripts import adopt_console_identity as adopt
from scripts.recover_exited_navigation import content_hash
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_store import ConsoleStore,digest
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint,VERSION,DLL_SHA256,FLAG_RVA


def fixture():
    fp=Fingerprint(7,101,300,VERSION,DLL_SHA256,4096,FLAG_RVA+4096,FLAG_RVA)
    account=dict(id='synthetic-id',name='synthetic',wechat_id='synthetic_sender',instance_id='7:101',
                 identity_source='uia_own_profile_exact_id_user_selected_window',identity_certain=True,
                 connected=True,fingerprint=asdict(fp))
    receipt=dict(kind='console_identity_integration',state='verified',cleanup_errors=[],sources={'synthetic':'hash'},
        account_sha256=hashlib.sha256(b'synthetic_sender').hexdigest(),fingerprint=asdict(fp),restored_byte=0,
        worker_exit_code=-15,console_paused=True,fingerprint_unchanged=True,worker_started=True,worker_exited=True,
        original_ciphertexts_unchanged=True,quota_file_unchanged=True,patch_pending_clear=True,sources_unchanged=True,
        history_read=False,draft_read=False,send_invoked=False,api_called=False,acceptance_binding_modified=False,
        background_qualified=False,chat_scope_bound=False,
        identity_probe=dict(state='verified',identity_saved=True,restored=True,reason=None,instance_id='7:101'),
        usage_before=dict(api_used=10,api_limit=12,send_used=3,send_limit=5),
        usage_after=dict(api_used=10,api_limit=12,send_used=3,send_limit=5))
    return fp,account,receipt


class ConsoleAdoptionValidationTests(unittest.TestCase):
    def setUp(self):self.fp,self.account,self.receipt=fixture()
    def validate(self):return adopt.validate(self.receipt,self.account,'synthetic_sender',{'synthetic':'hash'})
    def test_restored_actual_identity_receipt_validates_without_background_claim(self):
        self.assertEqual(self.validate(),self.fp)
    def test_missing_cleanup_exit_restore_or_scope_proof_is_rejected(self):
        for key,value in (('worker_exited',False),('restored_byte',True),('worker_exit_code',None),
                          ('console_paused',False),('history_read',True),('background_qualified',True),
                          ('cleanup_errors',['unconfirmed']),('chat_scope_bound',True)):
            with self.subTest(key=key):
                self.fp,self.account,self.receipt=fixture();self.receipt[key]=value
                with self.assertRaises(ConsoleError):self.validate()
    def test_source_account_or_quota_changes_do_not_authorize_adoption(self):
        for mutate in (lambda r:r['sources'].update(synthetic='changed'),
                       lambda r:r.update(account_sha256='other'),
                       lambda r:r['usage_after'].update(api_used=11),
                       lambda r:r['identity_probe'].update(restored=False)):
            self.fp,self.account,self.receipt=fixture();mutate(self.receipt)
            with self.assertRaises(ConsoleError):self.validate()
    def test_stored_identity_must_match_source_exact_account_and_current_process(self):
        for key,value in (('wechat_id','other_sender'),('identity_source','visual-only'),
                          ('instance_id','8:101'),('identity_certain',False),('connected',False),('fingerprint',{})):
            self.fp,self.account,self.receipt=fixture();self.account[key]=value
            with self.subTest(key=key),self.assertRaises(ConsoleError):self.validate()
    def test_default_cli_never_opens_a_gate_or_writes_a_binding(self):
        with patch.object(adopt,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(adopt.main([]),0)
            run.assert_not_called()


class ConsoleAdoptionTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ConsoleStore(Path(self.temp.name)/'gate.sqlite3',SyntheticProtector());self.addCleanup(self.store.close)
        self.fp,self.account,self.receipt=fixture()
        self.old=dict(account={**self.account,'instance_id':'6:99'},fingerprint=asdict(replace(self.fp,pid=6,hwnd=99,created=100)))
        self.pending=dict(request_id='synthetic-adoption',lock_token='synthetic-token')
        self.store.put('binding','account',self.old)
        self.store.put('console_identity','7:101',self.account)
        self.store.put('account_rebind','pending',self.pending)
        self.store.put('private_baselines','old',dict(upload_eligible=False,opaque='synthetic-history'))
        self.store.put('development_navigation','unknown',dict(state='unknown'))
        self.protected={bucket:bytes(self.store.db.execute('SELECT payload FROM records WHERE bucket=?',(bucket,)).fetchone()[0])
                        for bucket in ('private_baselines','development_navigation')}
    def commit(self,check=lambda:None):
        return adopt.commit(self.store,self.old,self.account,self.fp,'synthetic-sha',self.pending,check)
    def test_archive_and_new_epoch_commit_together_without_private_data_or_unknown_changes(self):
        result=self.commit()
        self.assertEqual(result['state'],'sender_rebound')
        self.assertFalse(result['background_qualified'])
        self.assertTrue(self.store.get('binding','account')['requires_fresh_target_identity'])
        self.assertEqual(self.store.get('binding_epochs',content_hash(self.old['fingerprint'])),self.old)
        self.assertIsNone(self.store.get('account_rebind','pending'))
        for bucket,raw in self.protected.items():
            self.assertEqual(bytes(self.store.db.execute('SELECT payload FROM records WHERE bucket=?',(bucket,)).fetchone()[0]),raw)
    def test_changed_stored_observation_or_other_pending_rejects_atomically(self):
        self.store.put('console_identity','7:101',{**self.account,'wechat_id':'other'})
        with self.assertRaises(ConsoleError):self.commit()
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertIsNone(self.store.get('binding_epochs',content_hash(self.old['fingerprint'])))
    def test_archive_collision_is_not_overwritten(self):
        self.store.put('binding_epochs',content_hash(self.old['fingerprint']),dict(other=True))
        with self.assertRaisesRegex(ConsoleError,'archive_conflict'):self.commit()
        self.assertEqual(self.store.get('binding','account'),self.old)
    def test_other_unrestored_operation_blocks_epoch_change(self):
        self.store.put('thread_hook','pending',dict(other=True))
        with self.assertRaisesRegex(ConsoleError,'state_changed'):self.commit()
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertEqual(self.store.get('thread_hook','pending'),dict(other=True))
    def test_sqlite_abort_rolls_back_epoch_archive_completion_and_pending_clear(self):
        self.store.db.execute("CREATE TRIGGER abort_binding BEFORE INSERT ON records WHEN NEW.bucket='binding' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.commit()
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertIsNone(self.store.get('binding_epochs',content_hash(self.old['fingerprint'])))
        self.assertEqual(self.store.get('account_rebind','pending'),self.pending)
        self.assertIsNone(self.store.get('account_rebind','synthetic-adoption'))
    def test_post_write_checkpoint_failure_rolls_back_all_changes(self):
        check=Mock(side_effect=[None,ConsoleError('process_changed')])
        with self.assertRaisesRegex(ConsoleError,'process_changed'):self.commit(check)
        self.assertEqual(self.store.get('binding','account'),self.old)
        self.assertEqual(self.store.get('account_rebind','pending'),self.pending)


if __name__=='__main__':unittest.main()
