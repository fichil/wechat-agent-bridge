import contextlib
import ctypes as c
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import probe_bound_thread_hook as hook
from wechat_agent_bridge.console_models import ConsoleError


class BoundHookTests(unittest.TestCase):
    def test_default_cannot_load_helper(self):
        with patch.object(hook.original,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hook.main([]),0)

    def test_no_live_send_or_arbitrary_pid_switch(self):
        for flag in ('--send','--pid','--hwnd','--function'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                hook.main([flag,'1'])

    def test_native_binding_abi_is_fixed(self):
        self.assertEqual(c.sizeof(hook.BoundInspection),128)
        self.assertEqual(hook.BoundInspection.widget.offset,56)
        self.assertEqual(hook.BoundInspection.checked.offset,96)

    def test_same_build_all_cases_and_no_focus_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'; build={'bound_inspector':True}
            report=dict(passed=True,synthetic_only=True,build=build,guard_fixture={'passed':True},
                harness_unchanged=True,harness_sources=hook.harness_sources(),cases=[
                dict(case=x,passed=True,focus_changed=False) for x in hook.CASES])
            with patch.object(hook.original,'DIRECTORY',Path(directory)),patch.object(hook,'validate_build',return_value=build):
                path.write_text(json.dumps(report),encoding='utf-8')
                self.assertEqual(hook.require_recovery(path),build)
                for change in ('missing','different','focus','failure'):
                    broken=json.loads(json.dumps(report))
                    if change=='missing': broken['cases'].pop()
                    if change=='different': broken['build']={}
                    if change=='focus': broken['cases'][0]['focus_changed']=True
                    if change=='failure': broken['cases'][0]['passed']=False
                    path.write_text(json.dumps(broken),encoding='utf-8')
                    with self.assertRaisesRegex(ConsoleError,'same_build_recovery_required'): hook.require_recovery(path)

    def test_cleanup_does_not_clear_other_request_journal(self):
        p=object.__new__(hook.BoundProbe)
        p.s=p.b=p.owner=None; p.native=Mock(); p.maps=[]; p.report={'cleanup':{}}
        p.journal_owned=False; p.store=Mock()
        with patch.object(hook,'module_present',return_value=False): p.close()
        p.store.put.assert_not_called()
        p.store.close.assert_called_once()

    def test_cleanup_preserves_journal_if_module_stays(self):
        p=object.__new__(hook.BoundProbe)
        p.s=p.b=p.owner=None; p.native=Mock(); p.maps=[]; p.report={'cleanup':{}}
        p.journal_owned=True; p.store=Mock()
        with patch.object(hook,'module_present',return_value=True),patch.object(hook.time,'monotonic',side_effect=[1,4]),self.assertRaisesRegex(ConsoleError,'recovery_unconfirmed'):
            p.close()
        p.store.put.assert_not_called()
        self.assertFalse(p.report['cleanup']['helper_unloaded'])
