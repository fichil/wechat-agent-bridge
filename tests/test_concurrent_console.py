"""Synthetic concurrency, encrypted ready jobs, and composer-isolation contract."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock

from tests.test_console import SyntheticProtector, Clock
from wechat_agent_bridge.adapters.bound_console import BoundConsoleAdapter
from wechat_agent_bridge.adapters.bound_read import check_read_target
from wechat_agent_bridge.adapters.isolated_channel import IsolationProof, IsolatedChannel
from wechat_agent_bridge.console_models import Account, Conversation, ChatMessage, MessagePage, ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore


class ConcurrentConsoleTests(unittest.TestCase):
    def setUp(self):
        self.store=ConsoleStore(':memory:',SyntheticProtector()); self.addCleanup(self.store.close)
        self.clock=Clock(); self.rows=[]
        self.state=dict(account_id='a',conversation_id='c',connected=True,identity_certain=True,
                        user_active=True,has_draft=True)
        self.sender=Mock(return_value=dict(status='ui_confirmed',attempted=True))
        self.adapter=BoundConsoleAdapter(Account('a','synthetic','wx-synthetic'),[Conversation('a','c','synthetic')],
            guard=lambda:None,reader=lambda *a:MessagePage(tuple(self.rows)),
            inspector=lambda *a:dict(self.state),sender=self.sender)
        self.channel=dict(send_ready=False,reason='isolated_send_unverified')
        self.adapter.channel_status=lambda *a:dict(self.channel)
        self.s=ConsoleService(self.adapter,self.store,clock=self.clock)
        self.s.discover();self.s.authorize('a',dict(read=True,upload=True,reply=True,paused=False))
        self.s.conversations('a');self.s.resume();self.s.sync('a','c')
        self.reply=Mock(wraps=self.s.provider.reply);self.s.provider.reply=self.reply

    def new(self,i):
        self.rows.append(ChatMessage('a','c',str(i),'synthetic','synthetic question '+str(i),''))
        self.s.sync('a','c');self.clock.advance(4)

    def qualify_synthetic_channel(self):
        self.channel.update(send_ready=True,isolated_send=True,draft_isolation_verified=True)
        # This describes a separate synthetic composer; the human draft has
        # never been passed to the sender or replaced.
        self.state.update(isolated_send=True,has_draft=False)

    def test_active_user_with_draft_keeps_reading_and_generates_once_until_channel_ready(self):
        self.new(1);self.s.tick()
        job=self.store.values('job')[0]
        self.assertEqual(job['status'],'ready');self.assertIn('reply_text',job)
        self.assertEqual(self.store.outcomes(),[]);self.sender.assert_not_called()
        for _ in range(3):self.clock.advance(5);self.s.tick()
        self.reply.assert_called_once()
        self.assertEqual(self.store.usage(self.clock())['hour'],1)
        raw=self.store.db.execute("SELECT payload FROM records WHERE bucket='job'").fetchone()[0]
        self.assertNotIn(job['reply_text'].encode(),raw)
        self.qualify_synthetic_channel();self.s.tick('send')
        self.sender.assert_called_once();self.reply.assert_called_once()
        self.assertEqual(self.store.values('job')[0]['status'],'ui_confirmed')

    def test_waiting_channel_does_not_repeat_generation_or_block_new_reads_and_summary(self):
        self.new(1);self.s.tick();self.new(2);self.s.tick()
        self.reply.assert_called_once()
        self.assertEqual(len(self.store.messages('a','c')),2)
        self.clock.advance(181);self.s.tick()
        self.assertGreater(self.s.memory('a','c')['watermark'],0)
        self.assertEqual(sorted(j['status'] for j in self.store.values('job')),['pending','ready'])
        self.assertEqual(self.s.status()['execution']['ready'],1)

    def test_main_window_draft_wait_retains_generated_reply_without_claim_and_resumes_once(self):
        self.channel.update(send_ready=True,send_mode='main-window')
        self.state['user_active']=False
        self.new(1);self.s.tick()
        job=self.store.values('job')[0]
        self.assertEqual((job['status'],job['wait_reason']),('ready','existing_draft_or_unknown'))
        self.assertIn('reply_text',job);self.assertEqual(self.store.outcomes(),[])
        self.state['has_draft']=False
        self.adapter.prepare_send=Mock(side_effect=ConsoleError('human_takeover'))
        self.s.tick('send')
        self.assertEqual(self.s.status()['execution']['waiting'],['human_takeover'])
        self.assertEqual(self.store.outcomes(),[]);self.sender.assert_not_called()
        self.adapter.prepare_send.side_effect=None
        self.s.tick('send');self.s.tick('send')
        self.sender.assert_called_once();self.reply.assert_called_once()
        self.assertEqual(self.store.values('job')[0]['status'],'ui_confirmed')

    def test_prewrite_target_mismatch_does_not_defer_or_send(self):
        self.qualify_synthetic_channel()
        self.adapter.prepare_send=Mock(side_effect=ConsoleError('target_mismatch'))
        self.new(1);self.s.tick()
        self.assertEqual(self.store.values('job')[0]['status'],'blocked')
        self.assertEqual(self.store.outcomes(),[]);self.sender.assert_not_called()

    def test_slow_provider_does_not_block_reader(self):
        self.new(1);entered=threading.Event();release=threading.Event();failures=[]
        def slow(*args):
            entered.set();release.wait(3);return 'synthetic answer'
        self.s.provider.reply=slow
        def generate():
            try:self.s.tick('model')
            except Exception as exc:failures.append(exc)
        t=threading.Thread(target=generate);t.start()
        try:
            self.assertTrue(entered.wait(1))
            self.rows.append(ChatMessage('a','c','during','synthetic','arrives during generation',''))
            self.clock.advance(6);self.s.tick('read')
            self.assertEqual(len(self.store.messages('a','c')),2)
        finally:release.set();t.join(3)
        self.assertFalse(t.is_alive());self.assertEqual(failures,[])

    def test_pause_or_restart_discards_saved_reply_and_never_catches_up(self):
        self.new(1);self.s.tick();self.s.pause()
        job=self.store.values('job')[0]
        self.assertEqual(job['status'],'cancelled');self.assertNotIn('reply_text',job)
        self.s.resume();self.new(2);self.s.tick()
        restarted=ConsoleService(self.adapter,self.store,clock=self.clock)
        self.assertTrue(restarted.paused)
        self.assertTrue(all(j['status'] not in ('pending','processing','ready') for j in self.store.values('job')))
        self.assertTrue(all('reply_text' not in j for j in self.store.values('job')))
        self.sender.assert_not_called()

    def test_unknown_send_blocks_send_but_not_sync_and_memory(self):
        self.qualify_synthetic_channel();self.sender.side_effect=TimeoutError()
        self.new(1);self.s.tick();self.assertFalse(self.s.paused)
        self.assertEqual(self.store.outcomes()[0]['status'],'unknown')
        self.new(2);self.clock.advance(181);self.s.tick()
        self.assertGreater(self.s.memory('a','c')['watermark'],0)
        self.sender.assert_called_once()

    def test_read_target_ignores_draft_focus_and_clipboard_but_never_identity(self):
        e=dict(identity_verified=True,target_index=0,session_runtime_id=[1],input_runtime_id=[2],group_header_runtime_id=[3])
        for i in range(10):check_read_target(e,dict(e,draft_empty=False,user_active=True,clipboard=i,foreground=i))
        for key in ('session_runtime_id','input_runtime_id','group_header_runtime_id'):
            with self.assertRaises(ConsoleError):check_read_target(e,{**e,key:[999]})


class IsolationTests(unittest.TestCase):
    def proof(self):
        return IsolationProof(('synthetic-build',7,123),'a','c',101,102,(101,1),(102,2),frozenset(
            ('drafts_both_directions','caret_selection_preserved','ime_preserved','source_navigation_preserved',
             'focus_preserved','clipboard_untouched','send_echo','cleanup','same_build_recovery')),'synthetic-helper')

    def test_partial_proof_shared_window_or_shared_composer_never_qualifies(self):
        p=self.proof();p.require()
        for change in (dict(channel_window=101),dict(channel_input=p.source_input),dict(build=''),
                       *[dict(observations=p.observations-{k}) for k in p.observations]):
            with self.assertRaises(ConsoleError):replace(p,**change).require()

    def test_bound_channel_preserves_human_draft_and_rejects_reuse_and_changed_target(self):
        p=self.proof();current=[p];human={'draft':'人工未发送内容','caret':4,'selection':(1,3),'ime':'zuhe'}
        original=dict(human);sender=Mock(return_value=dict(status='ui_confirmed',attempted=True))
        state=dict(account='a',conversation='c',window=102,input_runtime_id=(102,2),draft_empty=True)
        channel=IsolatedChannel(p,current_binding=lambda:current[0],inspector=lambda:dict(state),sender=sender)
        self.assertTrue(channel.status('a','c')['send_ready'])
        self.assertEqual(channel.send('a','c','synthetic','r')['status'],'ui_confirmed')
        self.assertEqual(human,original)
        current[0]=replace(p,fingerprint=('synthetic-build',7,124))
        with self.assertRaises(ConsoleError):channel.send('a','c','synthetic','r2')
        current[0]=p;state['draft_empty']=False
        with self.assertRaises(ConsoleError):channel.send('a','c','synthetic','r3')
        state['draft_empty']=True;state['conversation']='other'
        with self.assertRaises(ConsoleError):channel.send('a','c','synthetic','r4')
        sender.assert_called_once()
