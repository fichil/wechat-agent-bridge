"""Synthetic diagnostics only. No debugger, WeChat, desktop or provider calls."""
from pathlib import Path
import tempfile
import unittest
import copy
import platform
import threading
import ctypes
from itertools import product
from unittest.mock import patch, Mock
from types import SimpleNamespace

from wechat_agent_bridge.adapters.focus_diagnostics import DiagnosticLedger, TraceWindow, check_draft_target, classify_trace, require_native_recovery
from wechat_agent_bridge.adapters.desktop_events import EventEvidence
from wechat_agent_bridge.console_models import ConsoleError


class DiagnosticTests(unittest.TestCase):
    def synthetic_engine(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine, _Events
        engine = DebugEngine.__new__(DebugEngine)
        engine.owner_thread_id = threading.get_ident()
        # COM callbacks are Python-owned synthetic functions; no DLL is loaded.
        with patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True):
            engine.events = _Events()
        return engine

    def test_ledger_survives_restart_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"diag.db"
            ledger = DiagnosticLedger(path)
            request = ledger.reserve("fingerprint")
            ledger.finish(request, "failed")
            ledger.close()
            ledger = DiagnosticLedger(path)
            with self.assertRaisesRegex(ConsoleError, "already_used"):
                ledger.reserve("other_fingerprint")
            self.assertFalse(ledger.status()["send_enabled"])
            ledger.close()

    def test_only_one_draft_with_matching_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = DiagnosticLedger(Path(folder)/"diag.db")
            request = ledger.reserve("fp")
            for token, fp in (("wrong", "fp"), (request, "reused_pid")):
                with self.assertRaises(ConsoleError): ledger.consume_draft(token, fp)
            ledger.consume_draft(request, "fp")
            with self.assertRaises(ConsoleError): ledger.consume_draft(request, "fp")
            self.assertTrue(ledger.status()["draft_attempted"])
            ledger.close()

    def test_finished_attempt_cannot_write(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = DiagnosticLedger(Path(folder)/"diag.db")
            request = ledger.reserve("fp")
            ledger.finish(request, "finished")
            with self.assertRaises(ConsoleError): ledger.consume_draft(request, "fp")
            ledger.close()

    def test_collection_stops_before_recovery_deadline(self):
        now = [100]
        window = TraceWindow(lambda: now[0])
        now[0] = 145
        self.assertFalse(window.collecting())
        self.assertEqual(window.remaining(), 15)
        now[0] = 170
        self.assertEqual(window.remaining(), 0)

    def test_target_and_draft_checks(self):
        target = dict(identity_verified=True, target_index=0, input_runtime_id=[1,2],
                      session_runtime_id=[3,4], draft_empty=True, value_readonly=False)
        check_draft_target(target, target)
        for changes in ({"identity_verified":False}, {"target_index":1}, {"draft_empty":False},
                        {"draft_empty":None}, {"value_readonly":True}, {"input_runtime_id":[9]},
                        {"session_runtime_id":[9]}):
            with self.subTest(changes=changes), self.assertRaises(ConsoleError):
                check_draft_target(target, dict(target, **changes))

    def test_event_keeps_source_and_short_lived_focus_change(self):
        evidence = EventEvidence()
        evidence.record("focus", True, thread_id=22, event_ms=1000)
        evidence.record("foreground", False, thread_id=23, event_ms=1001)
        self.assertEqual(evidence.events[0]["source_thread"], 22)
        self.assertEqual(evidence.events[0]["system_event_ms"], 1000)
        with self.assertRaises(ConsoleError): evidence.check()

    def test_proximity_is_not_a_causal_chain(self):
        event = {"kind":"activation", "target_window":True}
        self.assertEqual(classify_trace([event], instance_bound=True, focus_changed=True), "trace_incomplete")
        event["queued_delivery_verified"] = True
        self.assertEqual(classify_trace([event], instance_bound=True, focus_changed=True), "trace_incomplete")
        event["setter_on_stack"] = True
        self.assertEqual(classify_trace([event], instance_bound=True, focus_changed=True), "synchronous_activation_observed")
        self.assertEqual(classify_trace([event], instance_bound=False, focus_changed=True), "instance_unverified")

    def test_recovery_requires_every_mode_and_current_helpers(self):
        fp = {'helper':'synthetic'}
        report = self.valid_recovery_report(fp)
        require_native_recovery(report,fp)
        for mode in range(3):
            report['results'][mode]['target_resumed']=False
            with self.assertRaisesRegex(ConsoleError,'recovery_not_verified'):require_native_recovery(report,fp)
            report['results'][mode]['target_resumed']=True
        with self.assertRaisesRegex(ConsoleError,'stale'):require_native_recovery(report,{'helper':'changed'})
        report['results'].pop()
        with self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def valid_recovery_report(self, fp):
        state={'debugger_present':False,'prior_suspend_count':0,'debug_registers':[0]*6}
        return {'schema':2,'backend':'dbgeng','os':platform.version(),'coverage':'all_target_threads',
                'repetitions':10,'fingerprints':fp,'passed':True,'results':[
                    dict(stage=s,topology=t,mode=m,repeat=r,passed=True,target_resumed=True,
                         stable_two_seconds=True,target_alive=True,all_threads_checked=True,
                         target_exitcode=None,initial_thread=copy.deepcopy(state),final_thread=copy.deepcopy(state),
                         initial_threads={'synthetic':copy.deepcopy(state)},final_threads={'synthetic':copy.deepcopy(state)})
                    for s,t,m,r in product(('attached','breakpoint','cleared','detached'),
                                          ('single','multi'),('normal','crash','watchdog'),range(10))]}

    def test_recovery_rejects_old_partial_or_control_reports(self):
        fp={'helper':'synthetic'}
        for change in ({'schema':1},{'backend':'win32-control'},{'os':'different'},
                       {'coverage':'primary_thread_only'},{'repetitions':1},{'passed':False}):
            report=self.valid_recovery_report(fp);report.update(change)
            with self.subTest(change=change),self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_recovery_rejects_residual_debugger_suspend_or_registers(self):
        fp={'helper':'synthetic'}
        for field,value in (('debugger_present',True),('prior_suspend_count',1),('debug_registers',[1]*6)):
            report=self.valid_recovery_report(fp)
            report['results'][0]['final_thread'][field]=value
            with self.subTest(field=field),self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_recovery_rejects_duplicate_case_even_with_correct_count(self):
        fp={'helper':'synthetic'};report=self.valid_recovery_report(fp)
        report['results'][-1]=copy.deepcopy(report['results'][0])
        with self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_recovery_requires_stability_all_threads_and_alive(self):
        fp={'helper':'synthetic'}
        for field in ('stable_two_seconds','all_threads_checked','target_alive'):
            report=self.valid_recovery_report(fp);report['results'][0][field]=False
            with self.subTest(field=field),self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_recovery_rejects_disappeared_thread_evidence(self):
        fp={'helper':'synthetic'};report=self.valid_recovery_report(fp)
        report['results'][0]['final_thread']=None
        with self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_recovery_rejects_changed_secondary_thread(self):
        fp={'helper':'synthetic'};report=self.valid_recovery_report(fp)
        report['results'][0]['final_threads']['new_thread']={'prior_suspend_count':1}
        with self.assertRaises(ConsoleError):require_native_recovery(report,fp)

    def test_hardware_slots_exhausted_before_native_call(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine();engine.breakpoints=[1,2,3,4]
        with patch('wechat_agent_bridge.adapters.debug_engine._call') as call:
            with self.assertRaisesRegex(ConsoleError,'slots_exhausted'):engine.add_execution_breakpoint(0x10000)
            call.assert_not_called()

    def test_detach_clears_breakpoints_and_handles_event_first(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine()
        engine.attached=True;engine.breakpoints=[1,2];engine.control=3;engine.client=4
        engine.events.record('breakpoint')
        with patch('wechat_agent_bridge.adapters.debug_engine._call', return_value=0) as call:
            engine.detach()
            self.assertEqual([x.args[1] for x in call.call_args_list],[73,73,50,93,25])
        self.assertFalse(engine.attached)

    def test_detach_failure_never_claims_recovered(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine()
        engine.attached=True;engine.breakpoints=[];engine.client=4
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=ConsoleError('synthetic_detach_failed')):
            with self.assertRaises(ConsoleError):engine.detach()
        self.assertTrue(engine.attached)

    def test_live_debugger_requires_bootstrap(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine();engine.prepared=False
        with patch('wechat_agent_bridge.adapters.debug_engine._call') as call:
            with self.assertRaisesRegex(ConsoleError,'bootstrap_required'):engine.attach(123)
            call.assert_not_called()

    def test_debugger_rejects_other_thread_before_native_call(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine();engine.owner_thread_id=-1
        with patch('wechat_agent_bridge.adapters.debug_engine._call') as call:
            with self.assertRaisesRegex(ConsoleError,'wrong_owner_thread'):engine.interrupt()
            call.assert_not_called()

    def test_partial_cleanup_does_not_repeat_removed_breakpoint(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine();engine.breakpoints=[11,22];engine.control=3
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=[0,ConsoleError('failed')]):
            with self.assertRaises(ConsoleError):engine.clear_breakpoints()
        self.assertEqual(engine.breakpoints,[22])
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0) as call:
            engine.clear_breakpoints()
            self.assertEqual(call.call_args.args[3],(22,))
        self.assertEqual(engine.breakpoints,[])

    def test_application_exception_is_not_swallowed_during_detach(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine()
        engine.attached=True;engine.breakpoints=[];engine.control=3;engine.client=4
        engine.events.record('exception',0xC0000005)
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0) as call:
            engine.detach()
            self.assertEqual(call.call_args_list[0].args[3],(3,))

    def test_failed_close_preserves_interfaces_for_cleanup(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=self.synthetic_engine();engine.interfaces=['synthetic-interface']
        with patch.object(engine,'detach',side_effect=ConsoleError('detach_failed')):
            with self.assertRaisesRegex(ConsoleError,'detach_failed'):engine.close()
        self.assertEqual(engine.interfaces,['synthetic-interface'])

    def test_missing_owner_identity_is_rejected(self):
        from wechat_agent_bridge.adapters.debug_engine import DebugEngine
        engine=DebugEngine.__new__(DebugEngine)
        with patch('wechat_agent_bridge.adapters.debug_engine._call') as call:
            with self.assertRaisesRegex(ConsoleError,'wrong_owner_thread'):engine.interrupt()
            call.assert_not_called()

    def test_real_constructor_records_owner_before_mock_com_calls(self):
        from wechat_agent_bridge.adapters import debug_engine as module
        def create(iid, output):
            ctypes.cast(output,ctypes.POINTER(module.PTR))[0]=module.PTR(1)
            return 0
        dll=SimpleNamespace(DebugCreate=Mock(side_effect=create))
        def native(pointer,index,types=(),values=(),**kwargs):
            if index==0:
                ctypes.cast(values[1],ctypes.POINTER(module.PTR))[0]=module.PTR(2)
            return 0
        with patch.object(module,'os',SimpleNamespace(name='nt',environ={'SystemRoot':'C:/Windows'})), \
                patch.object(ctypes,'WinDLL',return_value=dll,create=True) as loader, \
                patch.object(ctypes,'WINFUNCTYPE',ctypes.CFUNCTYPE,create=True), \
                patch.object(module,'_call',side_effect=native) as call:
            engine=module.DebugEngine()
            self.assertEqual(engine.owner_thread_id,threading.get_ident())
            self.assertEqual([row.args[1] for row in call.call_args_list],[0,0,0,46,54])
            self.assertEqual(loader.call_args.kwargs,{'winmode':0x800})
            observed=[]
            def other_thread():
                try: engine.interrupt()
                except Exception as exc: observed.append(exc)
            before=call.call_count
            worker=threading.Thread(target=other_thread)
            worker.start();worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(call.call_count,before)
            self.assertEqual(len(observed),1)
            self.assertIsInstance(observed[0],ConsoleError)
            self.assertIn('wrong_owner_thread',str(observed[0]))
            # The fake pointers have no native interfaces to release.
            engine.interfaces.clear()
            engine.close()

    def test_callback_sequence_replaces_current_pending_event(self):
        engine=self.synthetic_engine()
        events=engine.events
        self.assertEqual(events.functions[4](events.pointer,None),6)
        first=events.pending['sequence']
        code=ctypes.c_uint32(0xC0000005)
        self.assertEqual(events.functions[5](events.pointer,ctypes.byref(code),1),6)
        self.assertGreater(events.pending['sequence'],first)
        self.assertEqual(events.pending['kind'],'exception')
        self.assertEqual(events.pending['exception'],code.value)

    def test_continued_event_does_not_drive_later_detach(self):
        engine=self.synthetic_engine()
        engine.control=3;engine.client=4;engine.attached=True;engine.breakpoints=[]
        engine.events.record('breakpoint')
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0):
            engine.resume()
        self.assertIsNotNone(engine.events.pending)
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=1):
            self.assertFalse(engine.wait(100))
        self.assertIsNone(engine.events.pending)
        self.assertIsNone(engine.events.kind)
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0) as call:
            engine.detach()
            self.assertEqual([row.args[1] for row in call.call_args_list],[25])

    def test_timeout_without_continuation_preserves_pending_event(self):
        engine=self.synthetic_engine();engine.control=3
        engine.events.record('breakpoint')
        pending=engine.events.pending.copy()
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=1):
            self.assertFalse(engine.wait(100))
        self.assertEqual(engine.events.pending,pending)

    def test_new_exception_survives_previous_event_continuation(self):
        engine=self.synthetic_engine()
        engine.control=3;engine.client=4;engine.attached=True;engine.breakpoints=[]
        engine.events.record('breakpoint')
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0):engine.resume()
        def delivered(*args,**kwargs):
            engine.events.record('exception',0xC0000005)
            return 0
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=delivered):
            self.assertTrue(engine.wait(100))
        self.assertEqual(engine.events.pending['kind'],'exception')
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0) as call:
            engine.detach()
            self.assertEqual(call.call_args_list[0].args[3],(3,))

    def test_failed_continuation_preserves_pending_obligation(self):
        engine=self.synthetic_engine();engine.control=3
        engine.events.record('breakpoint')
        pending=engine.events.pending.copy()
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=ConsoleError('resume_failed')):
            with self.assertRaises(ConsoleError):engine.resume()
        self.assertEqual(engine.events.pending,pending)
        self.assertIsNone(engine.events.continuation_sequence)

    def test_new_stop_during_status_call_is_not_marked_continued(self):
        engine=self.synthetic_engine();engine.control=3
        engine.events.record('breakpoint')
        def delivered(*args,**kwargs):
            engine.events.record('exception',0xC0000005)
            return 0
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=delivered):engine.resume()
        self.assertEqual(engine.events.pending['kind'],'exception')
        self.assertIsNone(engine.events.continuation_sequence)
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=1):engine.wait(100)
        self.assertEqual(engine.events.pending['kind'],'exception')

    def test_failed_wait_retains_requested_continuation_for_retry(self):
        engine=self.synthetic_engine()
        engine.control=3;engine.client=4;engine.attached=True;engine.breakpoints=[]
        engine.events.record('breakpoint')
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0):engine.resume()
        pending=engine.events.pending.copy()
        with patch('wechat_agent_bridge.adapters.debug_engine._call',side_effect=ConsoleError('wait_failed')):
            with self.assertRaises(ConsoleError):engine.wait(100)
        self.assertEqual(engine.events.pending,pending)
        self.assertEqual(engine.events.continuation_sequence,pending['sequence'])
        with patch('wechat_agent_bridge.adapters.debug_engine._call',return_value=0) as call:
            engine.detach()
            self.assertEqual([row.args[1] for row in call.call_args_list],[93,25])
