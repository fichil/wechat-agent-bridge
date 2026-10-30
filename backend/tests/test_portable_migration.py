import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.app.auth import AuthStore
from backend.app.storage import Store
from backend.app.services import browser_values
from test_conversations import accounts, rows
from bridge_portable.migrate import export_data, import_data, resume_data, sha, offline


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'source'; self.source.mkdir()
        self.target = self.root/'目标 数据'; self.target.mkdir()
        store = Store(self.source/'bridge.sqlite3')
        self.aid = store.save_accounts(browser_values(accounts()))['accounts'][0]['account_id']
        store.save_conversations(self.aid, dict(items=rows(1), status='complete', issues=[]))
        self.cid = store.conversations(self.aid)['items'][0]['conversation_id']
        store.close()
        auth = AuthStore(self.source/'auth.sqlite3')
        auth.set_password('synthetic-password')
        auth.login('admin','synthetic-password','127.0.0.1')
        auth.close()
        with closing(sqlite3.connect(self.source/'bridge.sqlite3')) as db, db:
            db.execute('INSERT INTO ai_sessions(account_id,conversation_id,epoch,auto_pending,paused) VALUES(?,?,2,1,0)',(self.aid,self.cid))
            # Literal schema rows isolate migration semantics from AI eligibility.
            db.execute('INSERT INTO ai_runs VALUES(?,?,?,?,?,?)',('r',self.aid,self.cid,'request','running',json.dumps(dict(run_id='r',status='running'))))
            for i,status in enumerate(('queued','failed','sending','unknown','sent')):
                db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('r',i,self.aid,self.cid,2,'synthetic','synthetic',status,0,None,None,None,'2026-10-30'))
        self.archive = self.root/'private.zip'

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_import_preserves_history_and_hash_revokes_sessions_and_outbox(self):
        result=export_data(self.source,self.archive)
        self.assertEqual(sha(self.archive),result['sha256'])
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()),{'manifest.json','bridge.sqlite3','auth.sqlite3'})
        import_data(self.archive,self.target)
        with closing(sqlite3.connect(self.target/'bridge.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM accounts').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT epoch,auto_pending,paused FROM ai_sessions').fetchone(),(3,0,0))
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM deliveries ORDER BY part_index')],['cancelled','cancelled','unknown','unknown','sent'])
            self.assertEqual(db.execute('SELECT status FROM ai_runs').fetchone()[0],'failed')
        with closing(sqlite3.connect(self.source/'auth.sqlite3')) as src, closing(sqlite3.connect(self.target/'auth.sqlite3')) as dst:
            self.assertEqual(src.execute('SELECT password_hash FROM administrator').fetchone(),dst.execute('SELECT password_hash FROM administrator').fetchone())
            self.assertGreater(src.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
            self.assertEqual(dst.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
        self.assertTrue((self.target/'migration-pending.json').is_file())
        self.assertFalse((self.target/'native-send-acceptance.json').exists())

    def test_in_use_source_refuses_export(self):
        with offline(self.source):
            with self.assertRaises(ValueError): export_data(self.source,self.archive)
        self.assertFalse(self.archive.exists())

    def test_v8_import_upgrades_staged_copy_and_preserves_source_version(self):
        with closing(sqlite3.connect(self.source/'bridge.sqlite3')) as db, db:
            db.execute('DROP TABLE compatibility_state')
            db.execute('DELETE FROM schema_migrations WHERE version=9')
            db.execute('PRAGMA user_version=8')
        export_data(self.source,self.archive)
        import_data(self.archive,self.target)
        with closing(sqlite3.connect(self.source/'bridge.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
        with closing(sqlite3.connect(self.target/'bridge.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
            self.assertEqual(db.execute('SELECT count(*) FROM compatibility_state').fetchone()[0],0)

    def test_corrupt_or_traversal_archive_never_changes_destination(self):
        export_data(self.source,self.archive)
        (self.target/'untouched.txt').write_text('keep')
        with zipfile.ZipFile(self.archive,'a') as archive:
            archive.writestr('../escape.txt','no')
        with self.assertRaises(ValueError): import_data(self.archive,self.target)
        self.assertFalse((self.root/'escape.txt').exists())
        self.assertEqual((self.target/'untouched.txt').read_text(),'keep')
        self.assertFalse((self.target/'bridge.sqlite3').exists())

    def test_checksum_mismatch_rejected(self):
        export_data(self.source,self.archive)
        broken=self.root/'broken.zip'
        with zipfile.ZipFile(self.archive) as src, zipfile.ZipFile(broken,'w') as dst:
            for name in src.namelist():
                data=src.read(name)
                dst.writestr(name,data+b'x' if name=='bridge.sqlite3' else data)
        with self.assertRaises(ValueError): import_data(broken,self.target)
        self.assertFalse((self.target/'bridge.sqlite3').exists())

    def test_partial_replace_rolls_back_original_files(self):
        export_data(self.source,self.archive)
        import_data(self.archive,self.target)
        before={name:sha(self.target/name) for name in ('bridge.sqlite3','auth.sqlite3','migration-pending.json')}
        import os
        replace=os.replace
        failed=False
        def fault(src,dst):
            nonlocal failed
            if not failed and Path(src).parent.name.startswith('import-') and Path(dst)==self.target/'auth.sqlite3':
                failed=True
                raise OSError('synthetic failure')
            return replace(src,dst)
        with patch('bridge_portable.migrate.os.replace',side_effect=fault):
            with self.assertRaises(OSError): import_data(self.archive,self.target)
        self.assertEqual(before,{name:sha(self.target/name) for name in before})

    def test_resume_keeps_unknown_sends_blocked_and_requires_ready_accounts(self):
        export_data(self.source,self.archive)
        import_data(self.archive,self.target)
        # No AI enabled, so no live identity is required. Unknown attempts remain.
        resume_data(self.target,[])
        self.assertFalse((self.target/'migration-pending.json').exists())
        with closing(sqlite3.connect(self.target/'bridge.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE status='unknown'").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT paused FROM ai_sessions').fetchone()[0],0)

    def test_user_pause_survives_import_and_explicit_migration_resume(self):
        with closing(sqlite3.connect(self.source/'bridge.sqlite3')) as db, db:
            db.execute('UPDATE ai_sessions SET paused=1')
        export_data(self.source,self.archive)
        import_data(self.archive,self.target)
        resume_data(self.target,[])
        with closing(sqlite3.connect(self.target/'bridge.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT paused,auto_pending FROM ai_sessions').fetchone(),(1,0))


class PortableBindingTests(unittest.TestCase):
    def test_acceptance_from_another_machine_or_build_is_rejected(self):
        from wechat_sender import compatibility
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'acceptance.json'
            payload=dict(passed=True,accounts=['synthetic'],dll_sha256='dll',core_sha256='core',
                         binding='machine-a',checks={name:'passed' for name in compatibility.REQUIRED_CHECKS})
            path.write_text(json.dumps(payload))
            with patch.object(compatibility,'ACCEPTANCE',path), patch.object(compatibility,'machine_binding',return_value='machine-b'):
                self.assertFalse(compatibility.accepted('synthetic','core','dll'))
            with patch.object(compatibility,'ACCEPTANCE',path), patch.object(compatibility,'machine_binding',return_value='machine-a'):
                self.assertTrue(compatibility.accepted('synthetic','core','dll'))
                self.assertFalse(compatibility.accepted('synthetic','core','changed'))
