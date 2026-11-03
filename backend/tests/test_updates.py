import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient as Anonymous

from auth_support import create_app, TestClient
from bridge_portable import update_feed as feed, deploy as d
from bridge_portable.updates import Updates
from bridge_portable.update_engine import location, pointer


def release(version='0.4.1', **overrides):
    tag = 'v' + version
    name = 'WeChatAgentBridge-' + version + '-windows-x64'
    assets = []
    for i, suffix in enumerate(('.zip', '.zip.sha256', '.manifest.json')):
        file = name + suffix
        assets.append(dict(id=i, state='uploaded', size=100, name=file,
                           browser_download_url=feed.RELEASES_URL + '/download/' + tag + '/' + file))
    return dict(dict(id=42, tag_name=tag, published_at='2026-11-03', assets=assets), **overrides)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        manifest = dict(version='0.4.0', commit='a'*40, architecture='windows-x64')
        self.point = pointer(manifest)
        self.release = location(self.root, self.point)
        d.write(self.root/'installation.json', {'mode':'portable'})
        d.write(self.root/'current.json', self.point)
        d.write(self.release/'portable-manifest.json', manifest)
        self.updates = Updates(self.release, self.root/'data')

    def test_selects_highest_stable_and_rejects_wrong_assets(self):
        items = [release('1.0.0',draft=True),release('2.0.0',prerelease=True),
                 release('0.3.9'),release('0.4.11'),release('0.4.2'),release('2.0.0-rc.1')]
        self.assertEqual(feed.select_release(items,'0.4.0')['version'],'0.4.11')
        self.assertIsNone(feed.select_release(items,'0.5.0'))
        self.assertFalse(feed.select_release([release(assets=[])],'0.4.0')['ready'])
        wrong = release()
        wrong['assets'][0]['browser_download_url'] = 'https://example.com/package.zip'
        with self.assertRaises(ValueError): feed.select_release([wrong],'0.4.0')
        for url in ('http://github.com/', 'https://evilgithub.com/', 'https://github.com@example.org/', 'https://github.com:9000'):
            with self.assertRaises(ValueError): feed.allowed_url(url)

    def test_manual_check_retries_errors_cache_and_confirmation_is_shared(self):
        selected = feed.select_release([release()], '0.4.0')
        with patch.object(feed,'latest',side_effect=[OSError('offline'),ValueError('GitHub 暂时限流'), selected]) as fetch:
            self.assertTrue(self.updates.check()['error'])
            self.updates.check()
            self.assertEqual(fetch.call_count, 1)
            self.assertIn('限流',self.updates.check(True)['error'])
            self.assertEqual(self.updates.check(True)['latest']['version'],'0.4.1')
        other = Updates(self.release,self.root/'data')
        with self.assertRaises(ValueError): other.install('0.4.2',42)
        with patch('bridge_portable.update_engine.spawn_worker') as spawn:
            first = other.install('0.4.1',42)
            second = self.updates.install('0.4.1',42)
            self.assertEqual(first['task']['task_id'], second['task']['task_id'])
            spawn.assert_called_once()
        self.assertFalse((self.root/'data/production/stop.request').exists())

    def test_source_and_managed_are_read_only_and_api_requires_login_csrf(self):
        d.write(self.root/'installation.json', {'repository':feed.REPOSITORY})
        for mode, updates in [('managed',Updates(self.release)),('source',Updates(self.root/'source'))]:
            self.assertEqual(updates.query()['mode'],mode)
            with self.assertRaises(ValueError): updates.install('0.4.1',42)
        with Anonymous(create_app(lambda: {}),base_url='http://127.0.0.1:8765') as client:
            res=client.post('/api/v1/updates/query',json={},headers={'Origin':'http://127.0.0.1:5173'})
            self.assertEqual(res.status_code,401)
        with TestClient(create_app(lambda: {}),base_url='http://127.0.0.1:8765') as client:
            self.assertEqual(client.post('/api/v1/updates/query',json={}).json()['mode'],'source')
            self.assertEqual(client.post('/api/v1/updates/install',json={'version':'0.4.1','release_id':42}).status_code,409)
            client.headers.pop('X-CSRF-Token')
            self.assertEqual(client.post('/api/v1/updates/install',json={'version':'0.4.1','release_id':42}).status_code,403)

    def test_damaged_download_never_stops_or_switches(self):
        from bridge_portable.update_engine import Engine
        target = feed.select_release([release()], '0.4.0')
        old = dict(version='0.4.0',commit='a'*40,architecture='windows-x64',files={},database_schema=11)
        new = dict(old,version='0.4.1',commit='b'*40)
        d.write(self.release/'portable-manifest.json',old)
        self.updates.data.mkdir()
        d.write(self.updates.directory/'feed.json',dict(latest=target))
        with patch('bridge_portable.update_engine.spawn_worker'):
            self.updates.install('0.4.1',42)
        def bad_file(url, *, output=None, **kwargs):
            if output is not None: output.write(b'x'*100); return b''
            if '/tags/' in url: return json.dumps(release()).encode()
            if url.endswith('.sha256'): return ('0'*64+'  WeChatAgentBridge-0.4.1-windows-x64.zip').encode()
            return json.dumps(new).encode()
        with patch.object(feed,'fetch',side_effect=bad_file), patch.object(d,'stop') as stop:
            Engine(self.root).apply()
            stop.assert_not_called()
        self.assertEqual(self.updates.query()['task']['stage'],'failed')
        self.assertEqual(d.read(self.root/'current.json'),self.point)

    def test_drain_waits_for_both_executors_before_acknowledgment(self):
        from backend.app.business_control import BusinessControl
        from types import SimpleNamespace
        import threading
        control=BusinessControl.__new__(BusinessControl)
        control.data=self.root
        control.ai=SimpleNamespace(lock=threading.RLock(),pending={'model'})
        control.sender=SimpleNamespace(lock=threading.RLock(),pending={'already-sending'})
        d.write(self.root/'update-drain.json',{'task_id':'test'})
        control.step()
        self.assertTrue(control.ai.update_draining and control.sender.update_draining)
        control.ai.pending.clear(); control.step()
        self.assertFalse((self.root/'production/update-drained.json').exists())
        control.sender.pending.clear(); control.step()
        self.assertEqual(d.read(self.root/'production/update-drained.json')['task_id'],'test')
