from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from bridge_portable.deploy import Deployment, digest, read, write, unpack
from backend.app.business_control import BusinessControl


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('inbox', 'data', 'releases', 'backups'): (self.root / name).mkdir()
        write(self.root / 'installation.json', {'version': 1})
        self.old, self.new = 'a' * 40, 'b' * 40
        write(self.root / 'current.json', {'commit': self.old})
        self.package(self.root / 'releases' / self.old, self.old)
        for name in ('bridge.sqlite3', 'auth.sqlite3'):
            with closing(sqlite3.connect(self.root / 'data' / name)) as db, db:
                db.execute('CREATE TABLE evidence(value TEXT)')
                db.execute("INSERT INTO evidence VALUES('preserve')")
                if name == 'bridge.sqlite3': db.execute('PRAGMA user_version=9')
        self.stage = self.root / 'candidate'
        self.package(self.stage, self.new)
        self.archive = self.root / 'inbox' / 'run-1.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for p in self.stage.iterdir(): z.write(p, 'package/' + p.name)
        self.request = dict(request_id='run-1', commit=self.new, sha256=digest(self.archive), created_at='2026-11-02')
        self.manager = Deployment(self.root)

    def package(self, root, commit):
        root.mkdir(exist_ok=True)
        (root / 'program.py').write_text('print("candidate")')
        write(root / 'portable-manifest.json', dict(commit=commit, development=False, database_schema=9,
                                                   files={'program.py': digest(root / 'program.py')}))

    def evidence(self):
        with closing(sqlite3.connect(self.root / 'data/bridge.sqlite3')) as db, db:
            return [r[0] for r in db.execute('SELECT value FROM evidence')]

    def test_success_pauses_candidate_before_activation_and_is_idempotent(self):
        states = []
        def healthy(root, commit, *, active, **kwargs):
            self.assertEqual(commit, self.new)
            states.append(active)
            self.assertEqual((root / 'data/deployment-hold.json').exists(), not active)
        with patch('bridge_portable.deploy.stop') as stop, patch('bridge_portable.deploy.start_task'), patch('bridge_portable.deploy.wait_healthy', side_effect=healthy):
            result = self.manager.apply(self.request)
            self.assertEqual(result['stage'], 'succeeded')
            self.assertEqual(self.evidence(), ['preserve'])
            self.assertTrue((self.root / 'backups/run-1/data/bridge.sqlite3').exists())
            self.assertEqual(self.manager.apply(self.request)['stage'], 'succeeded')
            self.assertEqual(stop.call_count, 1)
        self.assertEqual(states, [False, True, True])

    def test_corrupt_upload_never_stops_current_production(self):
        self.archive.write_bytes(b'incomplete')
        with patch('bridge_portable.deploy.stop') as stop:
            self.assertEqual(self.manager.apply(self.request)['stage'], 'failed')
            stop.assert_not_called()
        self.assertEqual(read(self.root / 'current.json')['commit'], self.old)

    def test_incompatible_schema_never_stops_current_production(self):
        with closing(sqlite3.connect(self.root / 'data/bridge.sqlite3')) as db, db: db.execute('PRAGMA user_version=10')
        with patch('bridge_portable.deploy.stop') as stop:
            self.assertEqual(self.manager.apply(self.request)['stage'], 'failed')
            stop.assert_not_called()

    def stage_upgrade(self, compatible_previous):
        previous = self.root / 'releases' / self.old / 'portable-manifest.json'
        if compatible_previous:
            write(previous, {**read(previous), 'database_compatible_schemas': [9, 10], 'database_output_schemas': [9, 10]})
        manifest = self.stage / 'portable-manifest.json'
        write(manifest, {**read(manifest), 'database_schema': 10, 'database_compatible_schemas': [9, 10]})
        with zipfile.ZipFile(self.archive, 'w') as z:
            for p in self.stage.iterdir(): z.write(p, 'package/' + p.name)
        self.request['sha256'] = digest(self.archive)

    def test_upgrade_requires_compatible_previous_release(self):
        self.stage_upgrade(False)
        with patch('bridge_portable.deploy.stop') as stop:
            result = self.manager.apply(self.request)
            self.assertEqual(result['stage'], 'failed')
            self.assertIn('bridge release', result['error'])
            stop.assert_not_called()

    def test_upgrade_and_post_activation_rollback_keep_v10_and_new_data(self):
        self.stage_upgrade(True)
        def healthy(root, commit, *, active, **kwargs):
            if commit == self.new:
                with closing(sqlite3.connect(root / 'data/bridge.sqlite3')) as db, db:
                    db.execute('PRAGMA user_version=10')
                    if active: db.execute("INSERT INTO evidence VALUES('new-send-result')")
                if active: raise TimeoutError('candidate failure')
        with patch('bridge_portable.deploy.stop'), patch('bridge_portable.deploy.start_task'), patch('bridge_portable.deploy.wait_healthy', side_effect=healthy):
            self.assertEqual(self.manager.apply(self.request)['stage'], 'rolled_back')
        self.assertEqual(self.evidence(), ['preserve', 'new-send-result'])
        with closing(sqlite3.connect(self.root / 'data/bridge.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)

    def test_start_failure_rolls_back_with_current_data(self):
        def healthy(root, commit, **kwargs):
            if commit == self.new: raise TimeoutError('candidate startup failed')
        with patch('bridge_portable.deploy.stop'), patch('bridge_portable.deploy.start_task'), patch('bridge_portable.deploy.wait_healthy', side_effect=healthy):
            self.assertEqual(self.manager.apply(self.request)['stage'], 'rolled_back')
        self.assertEqual(read(self.root / 'current.json')['commit'], self.old)
        self.assertEqual(self.evidence(), ['preserve'])
        self.assertFalse((self.root / 'data/deployment-hold.json').exists())

    def test_failure_after_activation_never_restores_old_database(self):
        def healthy(root, commit, *, active, **kwargs):
            if commit == self.new and active:
                with closing(sqlite3.connect(root / 'data/bridge.sqlite3')) as db, db: db.execute("INSERT INTO evidence VALUES('new-send-result')")
                raise TimeoutError('post-activation failure')
        with patch('bridge_portable.deploy.stop'), patch('bridge_portable.deploy.start_task'), patch('bridge_portable.deploy.wait_healthy', side_effect=healthy):
            self.assertEqual(self.manager.apply(self.request)['stage'], 'rolled_back')
        self.assertEqual(self.evidence(), ['preserve', 'new-send-result'])

    def test_interrupted_switch_resumes_journal_before_new_work(self):
        unpack(self.archive, self.root / 'releases' / self.new, self.request['sha256'], self.new)
        write(self.root / 'current.json', {'commit': self.new})
        write(self.root / 'transaction.json', {**self.request, 'previous': self.old, 'activated': False})
        write(self.root / 'data/deployment-hold.json', self.request)
        with patch('bridge_portable.deploy.start_task'), patch('bridge_portable.deploy.wait_healthy'):
            self.assertEqual(self.manager.apply(self.request)['stage'], 'succeeded')
        self.assertFalse((self.root / 'transaction.json').exists())
        self.assertEqual(read(self.root / 'history.json')[0]['previous'], self.old)

    def test_zip_traversal_rejected_without_destination_changes(self):
        with zipfile.ZipFile(self.archive, 'a') as z: z.writestr('package/../../outside.txt', 'bad')
        with self.assertRaises(ValueError):
            unpack(self.archive, self.root / 'releases' / self.new, digest(self.archive), self.new)
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_upload_interruption_does_not_publish_or_schedule_request(self):
        incoming = self.root / 'inbox/partial.zip.part'
        incoming.write_bytes(b'incomplete upload')
        with patch('bridge_portable.deploy.start_task') as start:
            with self.assertRaisesRegex(ValueError, 'Incomplete upload'):
                self.manager.submit('partial', self.new, self.request['sha256'])
            start.assert_not_called()
        self.assertFalse((self.root / 'inbox/partial.json').exists())
        self.assertEqual(read(self.root / 'current.json')['commit'], self.old)

    def test_pending_main_updates_only_apply_latest_request(self):
        first = {**self.request, 'created_at': '2026-11-02T01:00:00'}
        latest = {**self.request, 'request_id': 'run-2', 'created_at': '2026-11-02T01:00:01'}
        for request in (first, latest):
            write(self.root / 'inbox' / (request['request_id'] + '.json'), request)
            self.manager.record(request, 'queued')
        with patch.object(self.manager, 'apply', side_effect=lambda r: self.manager.record(r, 'succeeded')) as apply:
            self.manager.pending()
            apply.assert_called_once_with(latest)
        self.assertEqual(self.manager.status('run-1')['stage'], 'superseded')

    def test_duplicate_submission_preserves_identity_and_restarts_pending_task(self):
        self.archive.rename(self.archive.with_suffix('.zip.part'))
        with patch('bridge_portable.deploy.start_task') as start:
            first = self.manager.submit('run-1', self.new, self.request['sha256'])
            self.assertEqual(self.manager.submit('run-1', self.new, self.request['sha256']), first)
            self.assertEqual(start.call_count, 2)
            with self.assertRaisesRegex(ValueError, 'Conflicting request'):
                self.manager.submit('run-1', self.old, self.request['sha256'])

    def test_retention_counts_distinct_versions_after_rollback_and_reapply(self):
        older = 'c' * 40
        self.package(self.root / 'releases' / older, older)
        history = [dict(commit=commit, previous=older, request_id=identity) for commit, identity in
                   ((older, 'older'), (self.old, 'old-first'), (self.old, 'old-reapplied'))]
        write(self.root / 'history.json', history)
        for identity in ('older', 'old-first', 'old-reapplied', 'run-1'):
            (self.root / 'backups' / identity).mkdir()
        self.manager.complete(self.request, {'commit': self.old})
        self.assertEqual([r['commit'] for r in read(self.root / 'history.json')], [older, self.old, self.new])
        self.assertTrue((self.root / 'releases' / older).is_dir())
        self.assertTrue((self.root / 'backups/older').is_dir())
        self.assertTrue((self.root / 'backups/old-reapplied').is_dir())
        self.assertFalse((self.root / 'backups/old-first').exists())


class BusinessActivationTests(unittest.TestCase):
    def test_checks_wait_for_deploy_barrier_then_start_without_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            owner = BusinessControl.__new__(BusinessControl)
            owner.data = Path(temporary)
            owner.bridge, owner.ai, owner.sender = Mock(), Mock(), Mock()
            owner.monitor = owner.ai_auto = owner.send_auto = True
            owner.active = False
            owner.publish = Mock()
            marker = owner.data / 'deployment-hold.json'
            marker.write_text('{}')
            owner.step()
            owner.bridge.start_monitor.assert_not_called()
            owner.bridge.compatibility.reconcile.assert_not_called()
            marker.unlink()
            owner.step(); owner.step()
            owner.bridge.start_monitor.assert_called_once()
            owner.ai.start_automatic.assert_called_once()
            owner.sender.start_automatic.assert_called_once()
            self.assertEqual(owner.bridge.compatibility.reconcile.call_count, 2)
