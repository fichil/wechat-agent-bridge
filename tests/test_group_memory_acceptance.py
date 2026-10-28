import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from scripts import verify_group_memory as trial
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_models import Account,Conversation,ConsoleError
from wechat_agent_bridge.providers import DEFAULT_CONFIG


class GroupMemoryTests(unittest.TestCase):
    def setUp(self):
        self.account=Account('synthetic-account','synthetic')
        self.chat=Conversation(self.account.id,'synthetic-group','synthetic','group')
        self.row=dict(runtime_id=[42,123,4,-100],label='Synthetic numbered test',class_name='mmui::ChatTextItemView')
        self.value=dict(rows=[self.row],historical=True,coverage='accessible_loaded_tail_only')
    def page(self,value=None):
        return trial.message_page(self.value if value is None else value,self.account,self.chat,123)
    def fixture(self,memory=None,rows=None):
        memory=memory or dict(summary='M1 validation 002',watermark=1)
        rows=rows or [dict(id=self.page().messages[0].id,seq=1)]
        previous=dict(state='timer_summary_saved_and_restart_paused',fingerprint={'hwnd':123},cleanup_errors=[],
            summary=dict(sha256=hashlib.sha256(memory['summary'].encode()).hexdigest(),watermark=memory['watermark']))
        sent=dict(kind='bound_group_memory_fixture_candidate',candidate_case='memory-003',fingerprint={'hwnd':123},
            state='message_echo_observed',desktop_unchanged=True,events_observed=0,patch_restored=True,
            worker_exited=True,sources_unchanged=True,cleanup_errors=[],worker=dict(draft_empty_after=True,
                new_exact_echo_count=1,echo_runtime_id=[42,123,4,-99],native_evidence=dict(method_returned=True,
                    exception_code=0,entries=1,gui_thread_match=True,cleanup=dict(helper_unloaded=True,errors=[]))))
        return previous,sent,memory,rows,{'hwnd':123}

    def test_incremental_requires_saved_summary_and_confirmed_fixture_send(self):
        arguments=self.fixture()
        trial.IncrementalFixture(*arguments)
        for index,change in ((0,{'fingerprint':{'hwnd':999}}),(0,{'state':'failed'}),
                             (1,{'state':'unknown'}),(1,{'events_observed':1}),
                             (1,{'candidate_case':'send-002'}),(2,{'summary':'changed'})):
            values=copy.deepcopy(arguments); values=list(values)
            values[index].update(change)
            with self.subTest(index=index,change=change),self.assertRaises(ConsoleError):
                trial.IncrementalFixture(*values)

    def test_incremental_rejects_runtime_rebuild_additional_text_or_changed_fixture(self):
        fixture=trial.IncrementalFixture(*self.fixture())
        value=copy.deepcopy(self.value)
        value['rows'].append({**self.row,'runtime_id':[42,123,4,-99],'label':fixture.text})
        fixture.page(self.page(value))
        for change in ('rebuilt','extra','fixture'):
            bad=copy.deepcopy(value)
            if change=='rebuilt': bad['rows'][0]['runtime_id']=[42,123,4,-98]
            if change=='extra': bad['rows'].append({**self.row,'runtime_id':[42,123,4,-97]})
            if change=='fixture': bad['rows'][1]['label']='other text'
            with self.assertRaisesRegex(ConsoleError,'incremental_rows_changed'): fixture.page(self.page(bad))

    def test_incremental_context_must_contain_exactly_the_new_fixture(self):
        fixture=trial.IncrementalFixture(*self.fixture())
        provider=Mock(); provider.summarize.return_value='synthetic result'
        limited=trial.OneSummaryProvider(provider,Mock(),Mock(last_capture=180),0,
            clock=lambda:180,context_check=fixture.context)
        context=[dict(text=fixture.text,seq=2)]
        for summary,rows in (('changed',context),('M1 validation 002',context*2),
                             ('M1 validation 002',[dict(text=fixture.text,seq=1)])):
            with self.assertRaisesRegex(ConsoleError,'incremental_context_mismatch'):
                limited.summarize(summary,rows)
        provider.summarize.assert_not_called()
        limited.summarize('M1 validation 002',context)
        provider.summarize.assert_called_once_with('M1 validation 002',context)
        self.assertTrue(limited.calls[0]['incremental_context_verified'])

    def test_incremental_loaded_tail_can_drop_only_a_known_leading_prefix(self):
        from scripts.verify_bound_signal_send import MEMORY_TEXT
        rows=[{**self.row,'runtime_id':[42,123,4,-n],'label':'old-'+str(n)} for n in (106,105,104)]
        old_page=self.page({'rows':rows})
        stored=[dict(id=m.id,seq=i+1) for i,m in enumerate(old_page.messages)]
        fixture=trial.IncrementalFixture(*self.fixture(dict(summary='M1 002',watermark=3),stored))
        new={**self.row,'runtime_id':[42,123,4,-99],'label':MEMORY_TEXT}
        fixture.page(self.page({'rows':rows[1:]+[new]}))
        self.assertEqual(fixture.last_page_coverage['known_leading_rows_outside_loaded_tail'],1)
        for bad in ([new],[rows[0],rows[2],new],list(reversed(rows))+[new],rows+[new,rows[-1]]):
            with self.assertRaises(ConsoleError): fixture.page(self.page({'rows':bad}))

    def test_fixture_facts_distinguish_old_and_new_fact_loss(self):
        complete='M1 002 BRIDGE-MEM-003 2026-10-27 14:30'
        self.assertTrue(all(trial.IncrementalFixture.facts(complete).values()))
        for removed in ('002','BRIDGE-MEM-003','2026-10-27','14:30'):
            self.assertFalse(all(trial.IncrementalFixture.facts(complete.replace(removed,'')).values()))
    def test_default_cli_never_opens_client_or_provider(self):
        with patch.object(trial,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(trial.main([]),0)
    def test_text_evidence_is_separate_from_failed_vision(self):
        report=dict(kind='synthetic_provider_acceptance',model=trial.MODEL,contains_real_chat=False,
                    results=[dict(capability='text',attempted=True,ok=True),dict(capability='vision',attempted=True,ok=False)])
        trial.text_capability(report)
        for change in ({'model':'other'},{'contains_real_chat':True},{'results':[dict(capability='vision',ok=True,attempted=True)]}):
            with self.assertRaisesRegex(ConsoleError,'text_evidence_required'): trial.text_capability({**report,**change})
    def test_unknown_sender_remains_unknown_and_nontext_is_not_attachment_content(self):
        value=copy.deepcopy(self.value)
        value['rows'].append({**self.row,'runtime_id':[42,123,4,-99],'class_name':'mmui::ChatItemView','label':'do not parse as a file'})
        page=self.page(value)
        self.assertEqual(len(page.messages),1)
        self.assertIsNone(page.messages[0].own)
        self.assertIsNone(page.messages[0].mentions_self)
        self.assertTrue(page.messages[0].historical)
        self.assertEqual(page.messages[0].timestamp,'')
        self.assertFalse(page.complete)
        self.assertIn('skipped_rows=1',page.limitation)
    def test_wrong_window_duplicate_runtime_and_oversize_text_stop(self):
        for rows in ([{**self.row,'runtime_id':[42,999,4,-100]}],[self.row,self.row],
                     [{**self.row,'label':'x'*32769}],[],[self.row]*51):
            with self.assertRaises(ConsoleError): self.page({'rows':rows})
    def test_scope_checked_before_read_and_send_is_always_closed(self):
        reader=Mock(return_value=self.value); guard=Mock()
        adapter=trial.GroupReadAdapter(self.account,self.chat,123,reader,guard)
        with self.assertRaisesRegex(ConsoleError,'target_mismatch'): adapter.messages(self.account.id,'other')
        reader.assert_not_called()
        self.assertEqual(len(adapter.messages(self.account.id,self.chat.id).messages),1)
        self.assertIsNotNone(adapter.last_capture)
        with self.assertRaisesRegex(ConsoleError,'read_only_scope'): adapter.send(self.account.id,self.chat.id,'text','request')
        with self.assertRaisesRegex(ConsoleError,'paging_unverified'): adapter.messages(self.account.id,self.chat.id,'cursor')
    def test_summary_waits_real_interval_requires_fresh_read_and_runs_once(self):
        provider=Mock(); provider.summarize.return_value='synthetic summary'
        now=[179.9]; adapter=Mock(last_capture=179)
        limited=trial.OneSummaryProvider(provider,Mock(),adapter,0,clock=lambda:now[0])
        with self.assertRaisesRegex(ConsoleError,'real_timer_not_elapsed'): limited.summarize('',[])
        provider.summarize.assert_not_called()
        now[0]=180
        adapter.last_capture=160
        with self.assertRaisesRegex(ConsoleError,'fresh_read_required'): limited.summarize('',[])
        adapter.last_capture=180
        self.assertEqual(limited.summarize('',[{'text':'synthetic'}]),'synthetic summary')
        with self.assertRaisesRegex(ConsoleError,'one_summary_only'): limited.summarize('',[])
        provider.summarize.assert_called_once()
    def test_cancelled_result_or_timeout_cannot_be_retried(self):
        for failure in (TimeoutError(),ConsoleError('group_memory_emergency_stop')):
            provider=Mock(); guard=Mock()
            if isinstance(failure,TimeoutError): provider.summarize.side_effect=failure
            else: guard.side_effect=[None,failure,None]
            limited=trial.OneSummaryProvider(provider,guard,Mock(last_capture=180),0,clock=lambda:180)
            with self.assertRaises(type(failure)): limited.summarize('',[])
            self.assertFalse(limited.calls[0]['returned'])
            with self.assertRaisesRegex(ConsoleError,'one_summary_only'): limited.summarize('',[])
            provider.summarize.assert_called_once()
    def test_capture_requires_current_target_and_complete_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.dpapi'; protector=SyntheticProtector()
            encrypted=protector.protect(json.dumps(self.value).encode()); path.write_bytes(encrypted)
            target=dict(identity_verified=True,target_index=0,input_runtime_id=[1],session_runtime_id=[2],
                        draft_empty=True,value_readonly=False)
            report=dict(state='preflight_verified',attempted=False,patch_restored=True,worker_exited=True,
                desktop_unchanged=True,events_observed=0,source_unchanged=True,cleanup_errors=[],observed_target_metadata=target,
                worker=dict(setvalue_called=False,invoke_called=False,protected_capture=str(path),
                            protected_sha256=hashlib.sha256(encrypted).hexdigest()))
            self.assertEqual(trial.decode_capture(report,target,protector,directory),self.value)
            for change in ({'patch_restored':False},{'events_observed':1},{'source_unchanged':False},
                           {'observed_target_metadata':{**target,'session_runtime_id':[3]}}):
                with self.assertRaises(ConsoleError): trial.decode_capture({**report,**change},target,protector,directory)
            path.write_bytes(encrypted+b'changed')
            with self.assertRaisesRegex(ConsoleError,'hash_changed'): trial.decode_capture(report,target,protector,directory)

    def test_application_timer_saves_only_new_group_rows_and_does_not_reply(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.ExitStack() as cleanup:
            ledger=trial.AcceptanceLedger(Path(directory)/'quota.sqlite3')
            cleanup.callback(ledger.close)
            account=Account(self.account.id,'synthetic',identity_source='synthetic-proof',fingerprint={'fixture':1})
            chat=Conversation(account.id,self.chat.id,'synthetic','group',identity_source='synthetic-proof')
            scope=trial.AcceptanceSession(ledger); scope.bind_group_reading(account,chat)
            store=trial.ConsoleStore(':memory:',SyntheticProtector()); cleanup.callback(store.close)
            store.put('settings','provider',{**DEFAULT_CONFIG,'provider':'compatible',
                'base_url':'https://api.openai.com/v1','model':trial.MODEL,'api_key':'sk-SYNTHETIC',
                'capabilities':{'text':True,'vision':False,'transcription':False}})
            now=[0.0]; raw_provider=Mock(); raw_provider.summarize.return_value='M1 validation 002'
            adapter=trial.GroupReadAdapter(account,chat,123,lambda:self.value,lambda:None)
            provider=trial.OneSummaryProvider(raw_provider,lambda:None,adapter,0,clock=lambda:now[0])
            service=trial.ConsoleService(adapter,store,acceptance=scope,
                provider_factory=lambda c:provider,clock=lambda:10000+now[0])
            service.discover()
            service.authorize(account.id,dict(read=True,upload=True,reply=False,paused=False,excluded=[]))
            service.resume()
            with patch.object(trial.time,'monotonic',side_effect=lambda:now[0]):
                service.tick()
                now[0]=179; service.tick(); raw_provider.summarize.assert_not_called()
                now[0]=180; service.tick()
                saved=service.memory(account.id,chat.id)
                self.assertEqual(saved['summary'],'M1 validation 002')
                self.assertEqual(saved['watermark'],1)
                context=raw_provider.summarize.call_args.args[1]
                self.assertIsNone(context[0]['own'])
                now[0]=360; service.tick()
            raw_provider.summarize.assert_called_once()
            self.assertEqual(ledger.status()['api_used'],1)
            self.assertEqual(ledger.status()['send_used'],0)
            self.assertEqual(store.values('job'),[])
            # A second timer cycle receives the saved summary and only one new row.
            fixture=trial.IncrementalFixture(*self.fixture(saved,store.messages(account.id,chat.id)))
            adapter.validate_page=fixture.page
            self.value['rows'].append({**self.row,'runtime_id':[42,123,4,-99],'label':fixture.text})
            next_provider=Mock(); next_provider.summarize.return_value='M1 002 BRIDGE-MEM-003 2026-10-27 14:30'
            service.provider=trial.OneSummaryProvider(next_provider,lambda:None,adapter,360,
                clock=lambda:now[0],context_check=fixture.context)
            with patch.object(trial.time,'monotonic',side_effect=lambda:now[0]):
                now[0]=365; service.tick()
                now[0]=540; service.tick()
                now[0]=720; service.tick()
            next_provider.summarize.assert_called_once()
            context=next_provider.summarize.call_args.args
            self.assertEqual(context[0],saved['summary'])
            self.assertEqual(len(context[1]),1)
            self.assertEqual(context[1][0]['text'],fixture.text)
            self.assertEqual(service.memory(account.id,chat.id)['watermark'],2)
            self.assertEqual(ledger.status()['api_used'],2)
            self.assertEqual(ledger.status()['send_used'],0)
            self.assertEqual(store.values('job'),[])
