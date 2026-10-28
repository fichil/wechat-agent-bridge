from dataclasses import replace
import unittest
from unittest.mock import Mock

from tests.test_console import SyntheticProtector, Clock
from wechat_agent_bridge.trial_reply import LoadedTail, triggers, reply_text, PREFIX, REPLY_PREFIX
from wechat_agent_bridge.console_models import ChatMessage, MessagePage, Account, Conversation, ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore, digest
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.adapters.bound_console import BoundConsoleAdapter
from wechat_agent_bridge.adapters.console_send_trial import require_cleanup, ConsoleSendTrial, readonly_input_interruption
from wechat_agent_bridge.acceptance import AcceptanceLedger, AcceptanceSession


def message(i, **kw):
    return ChatMessage('a', 'c', str(i), 'unknown', PREFIX+'合成问题'+str(i), '', own=None, **kw)


class PrefixTrialTests(unittest.TestCase):
    def test_only_new_exact_prefix_text_not_own_or_labels(self):
        self.assertTrue(triggers(message(1)))
        for changes in (dict(historical=True), dict(own=True), dict(kind='unparsed'),
                        dict(upload_eligible=False), dict(content_status='client_label_only'),
                        dict(text='ordinary @name'), dict(text=' '+PREFIX+'x'), dict(text=PREFIX)):
            self.assertFalse(triggers(replace(message(1), **changes)))
        self.assertFalse(triggers(replace(message(1), text=reply_text(PREFIX+'repeat', 500)), outbound=True))
        self.assertLessEqual(len(reply_text('x'*500, 500)), 500)

    def test_all_new_text_includes_ordinary_and_at_but_not_echo_or_opaque(self):
        for text in ('普通群聊', '@合成账号 你好', PREFIX+'测试', '1'):
            self.assertTrue(triggers(replace(message(1), text=text), 'all_new_text'))
        for changes in (dict(historical=True), dict(own=True), dict(kind='unparsed'),
                        dict(upload_eligible=False), dict(content_status='client_label_only'),
                        dict(text=' '), dict(text=REPLY_PREFIX+'自己的回复'),
                        dict(text=' '+REPLY_PREFIX+'带空白的回显')):
            self.assertFalse(triggers(replace(message(1), **changes), 'all_new_text'))
        self.assertFalse(triggers(message(1), 'all_new_text', outbound=True))
        self.assertFalse(triggers(message(1), 'unrecognized'))

    def test_all_new_messages_coalesce_and_own_unknown_echo_never_retriggers(self):
        s, rows, sender, clock = self.service()
        extra = s.acceptance.ledger.extend('send', 'synthetic-echo-test', 1,
            authorization='synthetic-test-only', reason='allow second reply to detect an accidental own-message loop')
        s.trial_slots = ('group_reply', extra)
        s.enable_prefix_trial('a','c','all_new_text'); s.sync('a','c')
        rows.extend(replace(message(i), text=text) for i,text in
                    ((2,'普通聊天'),(3,'@合成账号 晚上吃啥'),(4,'继续聊上面的话题')))
        s.sync('a','c')
        pending = [j for j in s.store.values('job') if j['status']=='pending']
        self.assertEqual(len(pending),1); self.assertEqual(len(pending[0]['events']),3)
        clock.advance(4); s.tick(); sender.assert_called_once()
        self.assertFalse(s.paused)
        sent = sender.call_args.args[2]
        self.assertIsNotNone(s.store.get('outbound_echo',digest('a','c',sent)))
        rows.append(replace(message(5), text=sent, own=None))
        s.sync('a','c'); clock.advance(15); s.tick(); sender.assert_called_once()
        # A persisted outgoing digest remains a guard independently of the marker.
        s.store.put('outbound_echo',digest('a','c','synthetic raw echo'),dict(request_id='synthetic'))
        rows.append(replace(message(6), text='synthetic raw echo', own=None))
        s.sync('a','c'); clock.advance(15); s.tick(); sender.assert_called_once()

    def test_switch_to_all_new_text_rebaselines_existing_rows(self):
        s, rows, sender, clock = self.service()
        s.enable_prefix_trial('a','c'); s.sync('a','c')
        rows.append(replace(message(2),text='开启前的聊天'))
        s.enable_prefix_trial('a','c','all_new_text'); s.sync('a','c')
        clock.advance(4); s.tick(); sender.assert_not_called()
        self.assertEqual(s.prefix_trial['trigger_mode'],'all_new_text')
        self.assertIsNone(s.prefix_trial['prefix'])
        with self.assertRaises(ConsoleError): s.enable_prefix_trial('a','c','anything')

    def test_readonly_send_preflight_requires_paused_armed_scope_and_spends_nothing(self):
        s,rows,sender,clock=self.service()
        s.adapter.send_preflight=Mock(return_value=dict(status='preflight_verified',attempted=False))
        with self.assertRaisesRegex(ConsoleError,'paused_armed_trial_required'): s.send_preflight('a','c')
        s.enable_prefix_trial('a','c')
        with self.assertRaisesRegex(ConsoleError,'paused_armed_trial_required'): s.send_preflight('a','c')
        s.pause();before=s.acceptance.ledger.status()
        self.assertEqual(s.send_preflight('a','c')['status'],'preflight_verified')
        self.assertEqual(s.acceptance.ledger.status(),before)
        with self.assertRaises(ConsoleError):s.send_preflight('a','other')
        sender.assert_not_called()

    def test_exhausted_api_reason_survives_following_paused_error_and_extension_keeps_usage(self):
        s,rows,sender,clock=self.service()
        s.enable_prefix_trial('a','c');s.sync('a','c')
        for i in range(10):s.acceptance.ledger.reserve('api','synthetic-'+str(i))
        rows.append(message(2));s.sync('a','c');clock.advance(4);s.tick()
        self.assertTrue(s.paused)
        self.assertEqual(s.status()['pause_reason'],'acceptance_api_budget_exhausted')
        s._error('paused')
        self.assertEqual(s.status()['pause_reason'],'acceptance_api_budget_exhausted')
        with self.assertRaisesRegex(ConsoleError,'acceptance_api_budget_exhausted'):s.resume()
        s.acceptance.ledger.extend('api','synthetic-continued',10,
            authorization='synthetic-operator',reason='synthetic bounded continuation')
        s.resume();self.assertIsNone(s.status()['pause_reason'])
        self.assertEqual(s.acceptance.status()['api_used'],10)
        self.assertEqual(s.acceptance.status()['api_limit'],20)
        sender.assert_not_called()

    def test_trial_remaining_counts_only_current_launcher_slots_and_send_pause_has_reason(self):
        s,rows,sender,clock=self.service()
        s.enable_prefix_trial('a','c');s.sync('a','c')
        self.assertEqual(s.status()['acceptance']['trial_send_remaining'],1)
        s.acceptance.ledger.reserve('send','synthetic-used','group_reply')
        self.assertEqual(s.status()['acceptance']['trial_send_remaining'],0)
        rows.append(message(2));s.sync('a','c');clock.advance(4);s.tick()
        self.assertTrue(s.paused)
        self.assertEqual(s.status()['pause_reason'],'acceptance_send_budget_exhausted')
        sender.assert_not_called()

    def test_no_test_budget_runs_beyond_old_limits_without_slots_and_restart_stays_paused(self):
        s,rows,sender,clock=self.service()
        s.acceptance.test_budget_enforced=False;s.trial_slots=()
        for i in range(10):s.acceptance.ledger.reserve('api','old-api-'+str(i))
        for slot in ('fixed','group_reply','private_reply'):
            s.acceptance.ledger.reserve('send','old-'+slot,slot)
        s.enable_prefix_trial('a','c','all_new_text');s.sync('a','c')
        rows.append(message(2));s.sync('a','c');clock.advance(4);s.tick()
        self.assertFalse(s.paused);sender.assert_called_once()
        self.assertEqual(s.acceptance.status()['api_used'],11)
        self.assertEqual(s.acceptance.status()['send_used'],4)
        self.assertTrue(s.status()['prefix_trial_available'])
        s.tick();sender.assert_called_once()
        restarted=ConsoleService(s.adapter,s.store,acceptance=s.acceptance)
        self.assertTrue(restarted.paused)

    def test_tail_overlap_restart_prepending_anchor_loss_and_list_change(self):
        tracker = LoadedTail()
        def page(ids, listing=1, older=False):
            return tracker.page(dict(list_runtime_id=[listing]),
                MessagePage(tuple(message(i, historical=True) for i in ids)), older=older).messages
        self.assertTrue(all(m.historical for m in page([1, 2])))
        rows = page([0, 1, 2, 3])
        self.assertEqual([m.id for m in rows if not m.historical], ['3'])
        self.assertTrue(all(m.historical for m in page([0], older=True)))
        self.assertTrue(all(m.historical for m in page([3, 4], listing=2)))
        self.assertTrue(all(m.historical for m in page([7, 8], listing=2)))
        self.assertEqual([m.id for m in page([8, 9], listing=2) if not m.historical], ['9'])
        self.assertTrue(all(m.historical for m in LoadedTail().page(
            {'list_runtime_id':[2]}, MessagePage((message(9),))).messages))

    def service(self, native_status='ui_confirmed'):
        store = ConsoleStore(':memory:', SyntheticProtector()); self.addCleanup(store.close)
        ledger = AcceptanceLedger(':memory:'); self.addCleanup(ledger.close)
        account = Account('a', 'Synthetic', 'synthetic', identity_source='fixture', fingerprint={'fixture':1})
        chat = Conversation('a', 'c', 'Synthetic group', 'group', identity_source='fixture')
        rows = [message(1, historical=True)]
        sender = Mock(return_value=dict(status=native_status, attempted=True))
        adapter = BoundConsoleAdapter(account, [chat], guard=lambda:None,
            reader=lambda *a:MessagePage(tuple(rows)),
            inspector=lambda *a:dict(account_id='a', conversation_id='c', connected=True,
                identity_certain=True, has_draft=False, user_active=False), sender=sender)
        adapter.prepare_prefix_trial = Mock(return_value=dict(status='preflight_verified'))
        acceptance = AcceptanceSession(ledger); acceptance.bind_group_reading(account, chat)
        acceptance.validate_provider = Mock()  # Synthetic provider, no paid API.
        clock = Clock()
        service = ConsoleService(adapter, store, clock=clock, acceptance=acceptance, trial_slots=('group_reply',))
        service.discover(); service.authorize('a', dict(read=True, upload=True, paused=False))
        service.config['capabilities']['text'] = True
        service.conversations('a')
        return service, rows, sender, clock

    def test_realistic_scope_baseline_new_unknown_sender_reply_once_no_loop(self):
        s, rows, sender, clock = self.service()
        provider_reply = Mock(wraps=s.provider.reply)
        s.provider.reply = provider_reply
        s.enable_prefix_trial('a', 'c'); s.sync('a', 'c')
        self.assertFalse(s.paused); self.assertFalse(s.acceptance.read_only)
        sender.assert_not_called()
        rows.extend([replace(message(2), text='普通聊天'), message(3)])
        s.sync('a', 'c'); clock.advance(4); s.tick()
        sender.assert_called_once()
        text = sender.call_args.args[2]
        self.assertFalse(text.startswith(REPLY_PREFIX))
        context = provider_reply.call_args.args[1]
        self.assertTrue(any(r['text']==message(3).text for r in context))
        self.assertTrue(any(r['text']=='普通聊天' for r in context))
        self.assertTrue(all('reply_target' not in r for r in context))
        rows.append(replace(message(4), text=text))
        s.sync('a', 'c'); clock.advance(15); s.tick()
        sender.assert_called_once()
        restarted = ConsoleService(s.adapter, s.store, acceptance=AcceptanceSession(s.acceptance.ledger))
        self.assertTrue(restarted.paused); self.assertIsNone(restarted.prefix_trial)

    def test_bad_scope_no_preflight_and_failed_preflight_no_send_grant(self):
        s, rows, sender, clock = self.service()
        with self.assertRaises(ConsoleError): s.enable_prefix_trial('a', 'other')
        s.adapter.prepare_prefix_trial.assert_not_called()
        s.adapter.prepare_prefix_trial.return_value = dict(status='failed')
        with self.assertRaises(ConsoleError): s.enable_prefix_trial('a', 'c')
        self.assertTrue(s.paused); self.assertTrue(s.acceptance.read_only)
        self.assertFalse(s.grants['a'].reply)

    def test_pause_while_preparing_does_not_enable(self):
        s, rows, sender, clock = self.service()
        def prepare(*args):
            s.pause()
            return dict(status='preflight_verified')
        s.adapter.prepare_prefix_trial.side_effect = prepare
        with self.assertRaisesRegex(ConsoleError, 'operation_cancelled'): s.enable_prefix_trial('a','c')
        self.assertTrue(s.paused); self.assertTrue(s.acceptance.read_only)

    def test_arming_requires_per_send_preflight_without_sending(self):
        s, rows, sender, clock = self.service()
        s.adapter.prepare_prefix_trial.return_value = dict(status='armed', preflight_per_send=True)
        s.enable_prefix_trial('a','c')
        sender.assert_not_called(); self.assertFalse(s.paused)
        s.pause()
        s.adapter.prepare_prefix_trial.return_value = dict(status='armed', preflight_per_send=False)
        with self.assertRaises(ConsoleError): s.enable_prefix_trial('a','c')
        self.assertTrue(s.paused)

    def test_unknown_send_never_retried_and_budget_stays_spent(self):
        s, rows, sender, clock = self.service('unknown')
        s.enable_prefix_trial('a','c'); s.sync('a','c')
        rows.append(message(2)); s.sync('a','c'); clock.advance(4); s.tick()
        clock.advance(20); s.tick(); sender.assert_called_once()
        self.assertEqual(s.store.outcomes()[0]['status'], 'unknown')
        self.assertEqual(s.acceptance.ledger.status()['send_used'], 1)
        with self.assertRaises(ConsoleError): s.acceptance.reserve_send('a','c','new-request')

    def test_cleanup_requires_every_actual_recovery_signal(self):
        cleanup = dict(owner_exitcode=0, unhooked=1, drained=1, errors=[], post_unhook_delivered=True,
            post_unhook_no_callback=True, helper_unloaded=True, mapping_handles_closed=True)
        require_cleanup({'cleanup':cleanup})
        for name in cleanup:
            broken = dict(cleanup); broken.pop(name)
            with self.assertRaises(ConsoleError): require_cleanup({'cleanup':broken})

    def test_only_held_current_worker_can_own_helper_journal(self):
        trial = ConsoleSendTrial({'pid':1}, 'group', {}, 'synthetic', lambda:None, None)
        trial.process = Mock(pid=2); trial.process.is_alive.return_value = True
        trial.controller_created = 3; trial.build = {'build':'synthetic'}
        record = dict(fingerprint={'pid':1}, controller_pid=2, controller_created=3,
                      build={'build':'synthetic'}, mode='readonly_inspection')
        self.assertTrue(trial.owns_pending(record))
        for changes in (dict(controller_pid=4), dict(controller_created=4), dict(fingerprint={'pid':9}),
                        dict(build={}), dict(mode='other')):
            self.assertFalse(trial.owns_pending({**record, **changes}))
        trial.process.is_alive.return_value = False
        self.assertFalse(trial.owns_pending(record))

    def test_only_clean_mode_zero_input_interruption_can_repeat(self):
        report = dict(qt_method_called=False, send_invoked=False, guard_reason=2, metadata_checked=True,
            cleanup=dict(owner_exitcode=0, unhooked=1, drained=1, errors=[], post_unhook_delivered=True,
                post_unhook_no_callback=True, helper_unloaded=True, mapping_handles_closed=True))
        self.assertTrue(readonly_input_interruption(report))
        for changes in (dict(qt_method_called=True), dict(send_invoked=True), dict(guard_reason=5),
                        dict(metadata_checked=False), dict(cleanup={})):
            self.assertFalse(readonly_input_interruption({**report, **changes}))
