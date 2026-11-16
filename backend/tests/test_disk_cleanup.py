"""Exercise destructive maintenance only against disposable installation fixtures."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from bridge_portable import deploy as d


class DiskCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('data', 'inbox', 'status', 'backups', 'releases'):
            (self.root / name).mkdir()
        (self.root / 'deploy.lock').write_bytes(b'0')
        d.write(self.root / 'installation.json', {'version': 1})
        self.history = [dict(request_id='run-' + str(i), commit=char * 40, previous='0' * 40)
                        for i, char in enumerate('abc', 1)]
        self.current = self.history[-1]['commit']
        d.write(self.root / 'history.json', self.history)
        d.write(self.root / 'current.json', {'commit': self.current})
        for name in ('bridge.sqlite3', 'auth.sqlite3'):
            self.database(self.root / 'data' / name, name)
        self.originals = []
        for i, directory in enumerate(('migration-backups',) * 3 + ('maintenance-backups',)):
            path = self.root / 'data' / directory / ('snapshot-' + str(i) + '.sqlite3')
            path.parent.mkdir(exist_ok=True)
            self.database(path, str(i))
            self.originals.append(path)
        for entry in self.history:
            shutil.copytree(self.root / 'data', self.root / 'backups' / entry['request_id'] / 'data')
            self.upload(entry['request_id'], entry['commit'])
        self.obsolete = self.upload('obsolete', 'd' * 40)
        self.manager = d.Deployment(self.root)

    def database(self, path, value):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE evidence(value TEXT)')
            db.execute('INSERT INTO evidence VALUES(?)', (value,))
            db.execute('PRAGMA user_version=12')

    def upload(self, identity, commit, stage='succeeded', age=48):
        path = self.root / 'inbox' / (identity + '.zip')
        path.write_bytes(('synthetic package ' + identity).encode())
        d.write(path.with_suffix('.json'), dict(request_id=identity, commit=commit, sha256=d.digest(path)))
        d.write(self.root / 'status' / (identity + '.json'), dict(request_id=identity, commit=commit,
                stage=stage, updated_at=(datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()))
        return path

    def hashes(self):
        return {str(p.relative_to(self.root)): d.digest(p) for p in self.root.rglob('*') if p.is_file()}

    def duplicate(self, index=0):
        return self.root / 'backups/run-1/data' / self.originals[index].relative_to(self.root / 'data')

    def test_preview_is_read_only_and_preserves_three_versions(self):
        before = self.hashes()
        result = self.manager.cleanup()
        self.assertEqual(result['stage'], 'preview')
        self.assertEqual(len(result['candidates']), 13)
        self.assertEqual(result['estimated_bytes'], 3 * sum(p.stat().st_size for p in self.originals) + self.obsolete.stat().st_size)
        self.assertEqual(result['retained_requests'], ['run-1', 'run-2', 'run-3'])
        self.assertEqual(before, self.hashes())

    def test_apply_writes_recovery_references_before_deleting_and_is_idempotent(self):
        real_unlink = Path.unlink
        live = {str(p): d.digest(p) for p in (self.root / 'data').rglob('*') if p.is_file()}
        def checked_unlink(path, *args, **kwargs):
            if path.suffix == '.sqlite3':
                backup = path.parents[2]
                refs = d.read(backup / 'historical-snapshots.json')['files']
                relative = path.relative_to(backup / 'data').as_posix()
                self.assertTrue(any(ref['path'] == relative and ref['sha256'] == d.digest(path) for ref in refs))
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', checked_unlink):
            result = self.manager.cleanup(apply=True)
        self.assertEqual(result['stage'], 'completed')
        self.assertEqual(result['skipped'], [])
        self.assertEqual(len(result['removed']), 13)
        self.assertEqual(result['estimated_bytes'], result['removed_bytes'])
        self.assertEqual(d.read(result['receipt'])['removed_bytes'], result['removed_bytes'])
        self.assertEqual(live, {str(p): d.digest(p) for p in (self.root / 'data').rglob('*') if p.is_file()})
        for entry in self.history:
            backup = self.root / 'backups' / entry['request_id']
            d.database_check(backup / 'data')
            self.assertEqual(len(d.read(backup / 'historical-snapshots.json')['files']), 4)
            self.assertTrue((self.root / 'inbox' / (entry['request_id'] + '.zip')).is_file())
        again = self.manager.cleanup(apply=True)
        self.assertEqual(again['removed_bytes'], 0)
        self.assertTrue((self.root / 'inbox/obsolete.json').exists())
        self.assertTrue((self.root / 'status/obsolete.json').exists())

    def test_unique_missing_and_changed_originals_are_not_deleted(self):
        self.originals[0].unlink()
        self.duplicate(1).write_bytes(b'unique historical evidence')
        real_write = d.write
        def change_original(path, value):
            real_write(path, value)
            if value.get('stage') == 'applying' and not value.get('removed'):
                self.originals[2].write_bytes(b'changed since preview')
        with patch.object(d, 'write', side_effect=change_original):
            result = self.manager.cleanup(apply=True)
        for index in (0, 1, 2): self.assertTrue(self.duplicate(index).exists())
        self.assertTrue(result['skipped'])
        self.assertFalse(self.obsolete.exists())

    def test_unknown_failed_recent_partial_and_checksum_mismatch_uploads_are_preserved(self):
        paths = [self.upload('failed', 'e' * 40, stage='failed'),
                 self.upload('recent', 'f' * 40, age=23),
                 self.upload('altered', 'f' * 40),
                 self.root / 'inbox/unknown.zip', self.root / 'inbox/partial.zip.part']
        for path in paths[2:]: path.write_bytes(b'not eligible for deletion')
        self.manager.cleanup(apply=True)
        for path in paths: self.assertTrue(path.exists(), path)
        self.assertFalse(self.obsolete.exists())

    def test_active_transaction_and_pending_request_block_all_cleanup(self):
        for marker in ('transaction.json', 'bootstrap-request.json', 'data/deployment-hold.json'):
            path = self.root / marker
            d.write(path, {})
            before = self.hashes()
            self.assertEqual(self.manager.cleanup(apply=True)['stage'], 'blocked')
            self.assertEqual(before, self.hashes())
            path.unlink()
        self.upload('pending', 'e' * 40, stage='queued')
        self.assertEqual(self.manager.cleanup(apply=True)['stage'], 'blocked')
        self.assertTrue(self.obsolete.exists())

    def test_identity_change_during_cleanup_stops_before_deletion(self):
        real_write = d.write
        def change_current(path, value):
            real_write(path, value)
            if value.get('stage') == 'applying':
                real_write(self.root / 'current.json', {'commit': 'e' * 40})
        with patch.object(d, 'write', side_effect=change_current):
            result = self.manager.cleanup(apply=True)
        self.assertEqual(result['stage'], 'interrupted')
        self.assertEqual(result['removed_bytes'], 0)
        self.assertTrue(self.obsolete.exists())

    def test_deployment_lock_excludes_manual_cleanup(self):
        with d.lock(self.root):
            with self.assertRaises(OSError): self.manager.cleanup(apply=True)
        self.assertTrue(self.obsolete.exists())

    def test_interrupted_receipt_update_can_resume_without_losing_originals(self):
        real_write = d.write
        def crash_after_delete(path, value):
            if value.get('stage') == 'applying' and len(value.get('removed', [])) == 1:
                raise RuntimeError('simulated interruption after unlink')
            real_write(path, value)
        with patch.object(d, 'write', side_effect=crash_after_delete):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                self.manager.cleanup(apply=True)
        result = self.manager.cleanup(apply=True)
        self.assertEqual(len(result['removed']), 12)
        for path in self.originals: self.assertTrue(path.exists())
        for entry in self.history:
            self.assertEqual(len(d.read(self.root / 'backups' / entry['request_id'] / 'historical-snapshots.json')['files']), 4)

    @unittest.skipUnless(os.name == 'nt', 'Windows production sharing semantics')
    def test_locked_file_is_skipped_and_other_candidates_can_be_cleaned(self):
        import ctypes as c
        from ctypes import wintypes as w
        kernel = c.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        path = self.duplicate()
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, c.c_void_p(-1).value)
        try:
            result = self.manager.cleanup(apply=True)
            self.assertTrue(path.exists())
            self.assertTrue(any(item['path'] == str(path) for item in result['skipped']))
            self.assertFalse(self.obsolete.exists())
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(len(self.manager.cleanup(apply=True)['removed']), 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junctions')
    def test_junctions_and_outside_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            (external / 'evidence.sqlite3').write_bytes(b'keep outside installation')
            link = self.root / 'backups/run-1/data/migration-backups/redirect'
            subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(external)],
                           check=True, capture_output=True)
            try:
                with self.assertRaises(ValueError): d.checked_path(self.root, link / 'evidence.sqlite3')
                with self.assertRaises(ValueError): d.checked_path(self.root, external / 'evidence.sqlite3')
                result = self.manager.cleanup(apply=True)
                self.assertTrue(result['skipped'])
                self.assertEqual((external / 'evidence.sqlite3').read_bytes(), b'keep outside installation')
                self.assertTrue(self.duplicate().exists())
            finally:
                link.rmdir()  # Remove only the fixture junction, never its target.

    def test_managed_backup_restores_both_databases_and_referenced_history(self):
        backup = self.root / 'new-backup/data'
        backup.parent.mkdir()
        d.cold_backup(self.root / 'data', backup, exclude_history=True)
        d.database_check(backup)
        self.assertFalse((backup / 'migration-backups').exists())
        active_bytes = sum(p.stat().st_size for p in d.data_files(self.root / 'data', exclude_history=True))
        self.assertEqual(active_bytes, sum(p.stat().st_size for p in d.data_files(backup)))
        restored = self.root / 'restored'
        shutil.copytree(backup, restored)
        for ref in d.read(backup.parent / 'historical-snapshots.json')['files']:
            source = self.root / ref['source']
            self.assertEqual(d.file_evidence(self.root, source), {k: ref[k] for k in ('size_bytes', 'sha256')})
            target = restored / ref['path']
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
        d.database_check(restored)
        self.assertEqual({p.relative_to(self.root / 'data').as_posix(): d.digest(p) for p in d.data_files(self.root / 'data')},
                         {p.relative_to(restored).as_posix(): d.digest(p) for p in d.data_files(restored)})

    def test_portable_backup_still_copies_historical_directories(self):
        backup = self.root / 'portable-backup/data'
        backup.parent.mkdir()
        d.cold_backup(self.root / 'data', backup)
        self.assertEqual(len(d.data_files(backup)), len(d.data_files(self.root / 'data')))
        self.assertFalse((backup.parent / 'historical-snapshots.json').exists())

    def test_preflight_uses_same_exclusions_as_managed_backup(self):
        new = 'e' * 40
        for commit in (self.current, new):
            release = self.root / 'releases' / commit
            release.mkdir()
            (release / 'program.py').write_text('synthetic')
            d.write(release / 'portable-manifest.json', dict(commit=commit, database_schema=12,
                    files={'program.py': d.digest(release / 'program.py')}))
        archive = self.root / 'inbox/new.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            for path in (self.root / 'releases' / new).iterdir(): package.write(path, 'package/' + path.name)
        request = dict(request_id='new', commit=new, sha256=d.digest(archive))
        required = sum(p.stat().st_size for p in d.data_files(self.root / 'data', exclude_history=True)) + archive.stat().st_size * 4 + 512 * 1024**2
        usage = shutil.disk_usage(self.root)._replace(free=required)
        with patch.object(d.shutil, 'disk_usage', return_value=usage):
            self.assertEqual(self.manager.preflight(request)['commit'], self.current)
        with patch.object(d.shutil, 'disk_usage', return_value=usage._replace(free=required - 1)):
            with self.assertRaisesRegex(ValueError, 'Insufficient space'): self.manager.preflight(request)

    def test_cli_defaults_to_preview(self):
        before = self.hashes()
        result = subprocess.run([sys.executable, '-B', str(Path(d.__file__).resolve()), 'cleanup', '--root', str(self.root)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['stage'], 'preview')
        self.assertEqual(before, self.hashes())

    @unittest.skipUnless(os.name == 'nt', 'Windows short path aliases')
    def test_windows_short_path_root_is_valid_without_allowing_junctions(self):
        import ctypes as c
        from ctypes import wintypes as w
        kernel = c.WinDLL('kernel32', use_last_error=True)
        kernel.GetShortPathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        kernel.GetShortPathNameW.restype = w.DWORD
        buffer = c.create_unicode_buffer(32768)
        self.assertGreater(kernel.GetShortPathNameW(str(self.root), buffer, len(buffer)), 0)
        result = d.Deployment(Path(buffer.value)).cleanup()
        self.assertEqual(result['stage'], 'preview')
        self.assertEqual(len(result['candidates']), 13)


if __name__ == '__main__':
    unittest.main()
