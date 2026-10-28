"""Synthetic console integration; never accesses Windows or paid providers."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from tests.test_console import SyntheticProtector, Clock
from wechat_agent_bridge.adapters.bound_console import BoundConsoleAdapter
from wechat_agent_bridge.console_models import Account, Conversation, ChatMessage, MessagePage, ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore


class BoundConsoleTests(unittest.TestCase):
    def setUp(self):
        self.account = Account('sender', 'Synthetic sender', 'synthetic_sender')
        self.chat = Conversation('sender', 'peer', 'Synthetic peer', wechat_id='synthetic_peer')
        self.rows = [ChatMessage('sender', 'peer', 'old', 'peer', 'history', '', historical=True)]
        self.state = dict(account_id='sender', conversation_id='peer', connected=True,
                          identity_certain=True, user_active=False, has_draft=False)
        self.reader = Mock(side_effect=lambda *args: MessagePage(tuple(self.rows)))
        self.sender = Mock(return_value=dict(status='ui_confirmed', attempted=True))
        self.guard = Mock()
        self.adapter = BoundConsoleAdapter(self.account, [self.chat], guard=self.guard,
            reader=self.reader, inspector=lambda *args:dict(self.state), sender=self.sender)
        self.store = ConsoleStore(':memory:', SyntheticProtector())
        self.addCleanup(self.store.close)
        self.clock = Clock()
        self.service = ConsoleService(self.adapter, self.store, clock=self.clock)
        self.service.discover()
        self.service.authorize('sender', dict(read=True, upload=True, reply=True, paused=False))

    def start(self):
        self.service.conversations('sender')
        self.service.resume()
        self.service.sync('sender', 'peer')

    def deliver(self):
        self.rows.append(ChatMessage('sender', 'peer', 'new', 'peer', 'synthetic new message', ''))
        self.service.sync('sender', 'peer')
        self.clock.advance(4)
        self.service.tick()

    def test_console_history_new_reply_memory_and_restart_pause(self):
        self.start()
        self.sender.assert_not_called()
        self.deliver()
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.args[:2], ('sender','peer'))
        self.assertEqual(len(self.service.read_messages('sender','peer')['messages']),2)
        self.clock.advance(180)
        self.service.tick()
        self.assertGreater(self.service.memory('sender','peer')['watermark'],0)
        restarted=ConsoleService(self.adapter,self.store,clock=self.clock)
        self.assertTrue(restarted.paused)
        restarted.discover()
        self.assertEqual(restarted.memory('sender','peer'),self.service.memory('sender','peer'))
        restarted.tick()
        self.sender.assert_called_once()

    def test_unknown_native_result_is_persisted_and_never_retried(self):
        self.start()
        self.sender.side_effect=TimeoutError('synthetic')
        self.deliver()
        self.assertEqual(self.store.outcomes()[0]['status'],'unknown')
        self.clock.advance(200)
        self.service.tick()
        self.sender.assert_called_once()

    def test_wrong_account_or_conversation_never_calls_reader_or_sender(self):
        for account,chat in [('other','peer'),('sender','other')]:
            with self.assertRaises(ConsoleError): self.adapter.messages(account,chat)
            with self.assertRaises(ConsoleError): self.adapter.send(account,chat,'test','request')
        self.reader.assert_not_called()
        self.sender.assert_not_called()

    def test_draft_and_human_takeover_block_send(self):
        for update in (dict(has_draft=True),dict(user_active=True)):
            original=dict(self.state)
            self.state.update(update)
            with self.assertRaises(ConsoleError): self.adapter.send('sender','peer','test','request')
            self.state=original
        self.sender.assert_not_called()

    def test_reader_cannot_return_another_account(self):
        self.reader.side_effect=None
        self.reader.return_value=MessagePage((replace(self.rows[0],account_id='other'),))
        with self.assertRaisesRegex(ConsoleError,'message_target_mismatch'):
            self.adapter.messages('sender','peer')

    def test_read_only_port_does_not_claim_send_capability(self):
        self.adapter.sender=None
        outcome=self.adapter.send('sender','peer','test','request')
        self.assertEqual(outcome['status'],'failed')
        self.assertFalse(outcome['attempted'])
        self.sender.assert_not_called()

    def test_identity_change_after_native_call_is_unknown(self):
        self.sender.side_effect=lambda *args: self.guard.configure_mock(side_effect=ConsoleError('changed'))
        outcome=self.adapter.send('sender','peer','test','request')
        self.assertEqual(outcome['status'],'unknown')
        self.assertTrue(outcome['attempted'])
