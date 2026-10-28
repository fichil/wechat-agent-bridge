from dataclasses import replace
from unittest import TestCase
from unittest.mock import Mock
from types import SimpleNamespace

from tests.test_console import SyntheticProtector, Clock
from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.console_models import ConsoleError, Conversation
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore, digest
from wechat_agent_bridge.trial_reply import reply_text
from wechat_agent_bridge.adapters.private_catalog import collect_candidates


class PrivateScopeTests(TestCase):
    def setUp(self):
        self.db=ConsoleStore(':memory:',SyntheticProtector());self.addCleanup(self.db.close)
        self.a=MockConsoleAdapter();self.clock=Clock()
        self.s=ConsoleService(self.a,self.db,clock=self.clock)
        self.s.discover();self.s.authorize('mock-a',dict(read=True,upload=True,reply=True,paused=False))
        self.s.configure_reply_scope('mock-a',True);self.s.conversations('mock-a')
        self.s.resume();self.s.tick()
        self.reply=Mock(return_value='自然正文');self.s.provider.reply=self.reply

    def deliver(self):
        self.s.sync('mock-a','friend');self.clock.advance(4);self.s.tick()

    def test_baseline_is_not_uploaded_and_unmarked_reply_echo_does_not_loop(self):
        self.assertTrue(all(not r['upload_eligible'] for r in self.db.messages('mock-a','friend')))
        self.a.inject('mock-a','friend','fresh-private');self.deliver()
        self.assertEqual(self.reply.call_args.args[1][0]['text'],'fresh-private')
        self.assertEqual(len(self.a.sent),1)
        self.a.inject('mock-a','friend','自然正文',own=False);self.deliver()
        self.assertEqual(len(self.a.sent),1)
        self.reply.assert_called_once()

    def test_direction_unknown_and_own_do_not_trigger(self):
        self.a.inject('mock-a','friend','own',own=True)
        self.a.inject('mock-a','friend','unknown',own=None)
        self.deliver();self.reply.assert_not_called()

    def test_new_private_auto_baselines_and_keeps_context_separate(self):
        c=Conversation('mock-a','second','产品讨论','private','other-id')
        self.a.chats['mock-a'].append(c)
        self.a.events[('mock-a','second')]=[]
        self.a.states[('mock-a','second')]=dict(self.a.states[('mock-a','friend')])
        self.a.inject('mock-a','second','baseline-only')
        self.s.conversations('mock-a');self.s.sync('mock-a','second')
        self.a.inject('mock-a','second','second-new')
        self.s.sync('mock-a','second');self.clock.advance(4);self.s.tick()
        context=self.reply.call_args.args[1]
        self.assertEqual([r['text'] for r in context],['second-new'])
        self.assertEqual(self.s.memory('mock-a','friend')['summary'],'')

    def test_old_cached_memory_not_reused_after_opt_in(self):
        self.db.put('memory',digest('mock-a','friend'),dict(summary='old-private-secret',watermark=0,source_start=0))
        self.a.inject('mock-a','friend','fresh');self.deliver()
        self.assertEqual(self.reply.call_args.args[0],'')
        self.clock.advance(181);self.s.tick()
        self.assertNotIn('old-private-secret',self.s.memory('mock-a','friend')['summary'])

    def test_disable_cancels_pending_and_exclusion_prevents_read(self):
        self.a.inject('mock-a','friend','fresh');self.s.sync('mock-a','friend')
        self.s.configure_reply_scope('mock-a',False)
        with self.assertRaisesRegex(ConsoleError,'private_reply_scope_disabled'):
            self.s.sync('mock-a','friend')
        self.s.resume();self.s.tick();self.reply.assert_not_called()

    def test_restart_keeps_preference_but_pauses_and_baselines(self):
        restored=ConsoleService(self.a,self.db,clock=self.clock)
        self.assertTrue(restored.paused);self.assertTrue(restored._private_enabled('mock-a'))

    def test_bad_history_mode_and_other_account_denied(self):
        with self.assertRaises(ConsoleError):self.s.configure_reply_scope('mock-a',True,'all_history')
        with self.assertRaises(ConsoleError):self.s.configure_reply_scope('mock-b',True)

    def test_prefix_is_not_sent_even_if_model_repeats_old_marker(self):
        self.assertEqual(reply_text('【M1 AI】 【M1 AI】正文',500),'正文')
        self.assertEqual(reply_text('普通回复',500),'普通回复')
        self.assertEqual(len(reply_text('字'*600,500)),500)


class CatalogTests(TestCase):
    def test_names_are_not_contact_ids_and_preview_is_never_read(self):
        class Row:
            CurrentProcessId=7
            CurrentAutomationId='session_item_same-name'
            def GetRuntimeId(self):return [42,101,4,-2]
            @property
            def CurrentName(self):raise AssertionError('private preview accessed')
        def array(items):return SimpleNamespace(Length=len(items),GetElement=lambda i:items[i])
        listing=SimpleNamespace(FindAll=lambda *a:array([Row()]))
        root=SimpleNamespace(CurrentProcessId=7,CurrentNativeWindowHandle=101,FindAll=lambda *a:array([listing]))
        client=Mock();client.element_from_handle.return_value=root
        uia=SimpleNamespace(TreeScope_Descendants=4,TreeScope_Children=2,UIA_AutomationIdPropertyId=1,
            UIA_ControlTypePropertyId=2,UIA_ListItemControlTypeId=3)
        r=collect_candidates(client,uia,7,101)
        self.assertEqual(r['rows'][0]['kind'],'unknown')
        self.assertIsNone(r['rows'][0]['wechat_id']);self.assertFalse(r['complete'])
        with self.assertRaises(ConsoleError):collect_candidates(client,uia,8,101)
