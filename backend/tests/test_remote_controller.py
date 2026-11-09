import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from bridge_portable.deploy import lock
from scripts.deploy_remote import controller_source, controller_refresh_code


class RemoteControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'controller').mkdir(); (self.root / 'inbox').mkdir()
        self.current = self.root / 'controller/deploy.py'
        shutil.copy2(Path(__file__).resolve().parents[2] / 'bridge_portable/deploy.py', self.current)
        self.previous = self.current.read_bytes()
        self.candidate = self.root / 'inbox/run-1.controller.py'
        self.candidate.write_bytes(self.previous + b'\n# exact tested controller\n')
        self.checksum = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.code = controller_refresh_code(str(self.root), 'run-1', self.checksum)

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()): exec(self.code, {})

    def test_refresh_is_atomic_preserves_backup_and_is_idempotent(self):
        expected = self.candidate.read_bytes()
        self.execute()
        self.assertEqual(self.current.read_bytes(), expected)
        self.assertEqual((self.root / 'controller/deploy.before-run-1.py').read_bytes(), self.previous)
        self.assertFalse(self.candidate.exists())
        self.candidate.write_bytes(expected)
        self.execute()
        self.assertEqual((self.root / 'controller/deploy.before-run-1.py').read_bytes(), self.previous)

    def test_active_deployment_and_corrupt_transfer_preserve_controller(self):
        with lock(self.root):
            with self.assertRaises(PermissionError): self.execute()
        self.assertEqual(self.current.read_bytes(), self.previous)
        self.candidate.write_bytes(b'changed in transit')
        with self.assertRaisesRegex(ValueError, 'checksum'): self.execute()
        self.assertEqual(self.current.read_bytes(), self.previous)

    def test_only_matching_manifest_verified_source_is_uploaded(self):
        archive = self.root / 'package.zip'
        manifest = {'commit': 'a' * 40, 'files': {'bridge_portable/deploy.py': self.checksum}}
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('package/portable-manifest.json', json.dumps(manifest))
            bundle.writestr('package/bridge_portable/deploy.py', self.candidate.read_bytes())
        self.assertEqual(controller_source(archive, 'a' * 40), self.candidate.read_bytes())
        with self.assertRaisesRegex(ValueError, 'commit mismatch'): controller_source(archive, 'b' * 40)
