"""Synthetic acceptance scopes and durable hard budgets; no live clients."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from wechat_agent_bridge.acceptance import AcceptanceLedger, AcceptanceSession, MODEL
from wechat_agent_bridge.console_models import Account, Conversation, ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.providers import MockProvider
from wechat_agent_bridge.openai_config import configure_openai
from tests.test_console import SyntheticProtector


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"budget.sqlite3"
        self.ledger = AcceptanceLedger(self.path)
        self.addCleanup(self.ledger.close)
        self.scope = AcceptanceSession(self.ledger)

    def bind(self):
        account = Account("mock-a", "synthetic", identity_source="synthetic-proof", fingerprint={"test":1})
        chats = [Conversation("mock-a", "group", "synthetic-group", "group", identity_source="synthetic-proof"),
                 Conversation("mock-a", "friend", "synthetic-private", "private", identity_source="synthetic-proof")]
        self.scope.bind(account, chats)

    def test_api_budget_survives_second_connection_and_restart(self):
        for i in range(10):
            other = AcceptanceLedger(self.path)
            try: other.reserve("api", str(i))
            finally: other.close()
        with self.assertRaisesRegex(ConsoleError, "api_budget_exhausted"):
            self.ledger.reserve("api", "eleventh")
        self.assertEqual(self.ledger.status()["api_used"], 10)

    def test_explicit_no_test_budget_keeps_cumulative_counts_and_duplicate_protection(self):
        self.bind()
        self.ledger.reserve('send','old-unknown','fixed')
        self.scope.test_budget_enforced=False
        for i in range(15): self.scope.reserve_api('continued-api-'+str(i))
        for i in range(8): self.scope.reserve_send('mock-a','group','continued-send-'+str(i))
        self.assertEqual((self.ledger.status()['api_used'],self.ledger.status()['send_used']),(15,9))
        self.assertFalse(self.scope.status()['test_budget_enforced'])
        with self.assertRaisesRegex(ConsoleError,'already_reserved'):
            self.scope.reserve_send('mock-a','group','continued-send-0')
        with self.assertRaisesRegex(ConsoleError,'already_reserved'):
            self.scope.reserve_send('mock-a','group','old-unknown')
        with self.assertRaises(ConsoleError):self.scope.reserve_send('wrong','group','wrong-scope')
        with self.assertRaises(ConsoleError):self.scope.reserve_send('mock-a','other','wrong-chat')
        other=AcceptanceLedger(self.path)
        try:
            self.assertEqual(other.status()['send_used'],9)
            with self.assertRaisesRegex(ConsoleError,'api_budget_exhausted'):other.reserve('api','bounded-again')
            with self.assertRaisesRegex(ConsoleError,'invalid_reservation'):
                other.reserve('send','bad','continuous:unbound',enforce_limit=False)
        finally:other.close()

    def test_extensions_are_audited_idempotent_and_preserve_old_slots(self):
        self.ledger.reserve('send','old-unknown','fixed')
        options=dict(authorization='operator-test-authorization',reason='new reviewed candidate synthetic test')
        slot=self.ledger.extend('send','candidate-one',1,**options)
        self.assertEqual(self.ledger.extend('send','candidate-one',1,**options),slot)
        self.ledger.reserve('send','new-candidate',slot)
        restarted=AcceptanceLedger(self.path)
        try:
            self.assertEqual((restarted.status()['send_used'],restarted.status()['send_limit']),(2,4))
            self.assertEqual(len(restarted.status()['extensions']),1)
            for request,target in (('another','fixed'),('again',slot),('old-unknown','group_reply')):
                with self.assertRaisesRegex(ConsoleError,'already_reserved'): restarted.reserve('send',request,target)
            with self.assertRaisesRegex(ConsoleError,'invalid_reservation'): restarted.reserve('send','bad','extra:ungranted')
            with self.assertRaisesRegex(ConsoleError,'extension_changed'):
                restarted.extend('send','candidate-one',1,authorization='different-authority',reason=options['reason'])
        finally: restarted.close()

    def test_api_extension_adds_budget_without_refunding_attempts(self):
        for i in range(10): self.ledger.reserve('api',str(i))
        self.ledger.extend('api','next-capability',2,authorization='operator-test-authorization',reason='bounded synthetic capability followup')
        self.ledger.reserve('api','eleven'); self.ledger.reserve('api','twelve')
        with self.assertRaisesRegex(ConsoleError,'budget_exhausted'): self.ledger.reserve('api','thirteen')
        self.assertEqual((self.ledger.status()['api_used'],self.ledger.status()['api_limit']),(12,12))

    def test_console_resume_honors_durable_extension_without_refunding_calls(self):
        from wechat_agent_bridge.providers import DEFAULT_CONFIG
        self.bind()
        store = ConsoleStore(':memory:', SyntheticProtector()); self.addCleanup(store.close)
        provider = MockProvider()
        service = ConsoleService(MockConsoleAdapter(), store, acceptance=self.scope,
                                 provider_factory=lambda c: provider)
        service.save_configuration({**DEFAULT_CONFIG, 'provider':'compatible',
            'base_url':'https://api.openai.com/v1', 'model':MODEL, 'api_key':'sk-SYNTHETIC'})
        for i in range(10): self.ledger.reserve('api', 'old-attempt-'+str(i))
        with self.assertRaisesRegex(ConsoleError, 'api_budget_exhausted'): service.resume()
        self.assertTrue(service.paused)
        other = AcceptanceLedger(self.path)
        try:
            other.extend('api','console-followup',1,authorization='operator-test-authorization',
                         reason='one separately reviewed synthetic followup')
        finally: other.close()
        self.assertEqual(service.resume(), {'paused':False})
        self.assertEqual(self.ledger.status()['api_used'], 10)
        with patch.object(provider, 'test', wraps=provider.test) as request:
            service.test_provider('text')
            self.assertEqual(self.ledger.status()['api_used'], 11)
            with self.assertRaisesRegex(ConsoleError, 'api_budget_exhausted'): service.resume()
            with self.assertRaisesRegex(ConsoleError, 'api_budget_exhausted'): service.test_provider('text')
            request.assert_called_once()
        restarted = ConsoleService(MockConsoleAdapter(), store, acceptance=self.scope,
                                   provider_factory=lambda c: provider)
        self.assertTrue(restarted.paused)
        with self.assertRaisesRegex(ConsoleError, 'api_budget_exhausted'): restarted.resume()

    def test_console_resume_rejects_exhausted_send_budget_until_audited_extension(self):
        self.bind()
        store = ConsoleStore(':memory:', SyntheticProtector()); self.addCleanup(store.close)
        service = ConsoleService(MockConsoleAdapter(), store, acceptance=self.scope)
        for slot in ('fixed', 'group_reply', 'private_reply'):
            self.ledger.reserve('send', 'old-'+slot, slot)
        with self.assertRaisesRegex(ConsoleError, 'send_budget_exhausted'): service.resume()
        self.assertTrue(service.paused)
        slot = self.ledger.extend('send','console-followup',1,authorization='operator-test-authorization',
                                 reason='new independently reviewed synthetic case')
        self.assertEqual(service.resume(), {'paused':False})
        self.assertEqual(self.ledger.status()['send_used'], 3)
        with self.assertRaisesRegex(ConsoleError, 'already_reserved'):
            self.ledger.reserve('send', 'new-id-cannot-reopen', 'fixed')
        self.ledger.reserve('send', 'new-distinct-case', slot)
        service.pause()
        with self.assertRaisesRegex(ConsoleError, 'send_budget_exhausted'): service.resume()

    def test_concurrent_connections_cannot_exceed_campaign_limit(self):
        def attempt(i):
            ledger = AcceptanceLedger(self.path)
            try:
                ledger.reserve("api", str(i)); return True
            except ConsoleError:
                return False
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(20))), 10)
        self.assertEqual(self.ledger.status()["api_used"], 10)

    def test_private_baseline_marked_historical_before_context(self):
        self.bind()
        store = ConsoleStore(":memory:", SyntheticProtector()); self.addCleanup(store.close)
        adapter = MockConsoleAdapter()
        service = ConsoleService(adapter, store, acceptance=self.scope)
        service.discover()
        service.authorize("mock-a", {"read":True, "upload":False, "reply":False, "paused":True, "excluded":[]})
        service.conversations("mock-a")
        adapter.inject("mock-a", "friend", "untrusted pre-start history", historical=False)
        service.sync("mock-a", "friend")
        rows = store.messages("mock-a", "friend")
        self.assertTrue(all(row["historical"] for row in rows))
        self.assertEqual(self.scope.filter_context("mock-a", "friend", rows), [])
        service.pause(); service.resume()
        adapter.inject("mock-a", "friend", "untrusted while paused", historical=False)
        service.sync("mock-a", "friend")
        self.assertEqual(self.scope.filter_context("mock-a", "friend", store.messages("mock-a", "friend")), [])

    def test_send_slots_cannot_be_reused_even_for_a_new_request(self):
        for i, slot in enumerate(("fixed", "group_reply", "private_reply")):
            self.ledger.reserve("send", str(i), slot)
            with self.assertRaisesRegex(ConsoleError, "already_reserved"):
                self.ledger.reserve("send", str(i)+"new", slot)
        self.assertEqual(self.ledger.status()["send_used"], 3)

    def test_duplicate_api_request_is_never_reissued(self):
        self.ledger.reserve("api", "request")
        with self.assertRaisesRegex(ConsoleError, "already_reserved"):
            self.ledger.reserve("api", "request")
        self.assertEqual(self.ledger.status()["api_used"], 1)

    def test_unverified_scope_rejected_and_restart_requires_rebinding(self):
        with self.assertRaisesRegex(ConsoleError, "verified_scope_required"):
            self.scope.bind(Account("mock-a", "name"), [])
        self.bind()
        for target in (("wrong", "friend"), ("mock-a", "wrong")):
            with self.assertRaisesRegex(ConsoleError, "target_not_bound"): self.scope.require(*target)
        with self.assertRaisesRegex(ConsoleError, "target_not_bound"):
            AcceptanceSession(self.ledger).require("mock-a", "friend")

    def test_single_group_read_scope_requires_identity_and_never_reserves_send(self):
        account = Account('mock-a','synthetic',identity_source='synthetic-proof',fingerprint={'test':1})
        chat = Conversation('mock-a','group','synthetic-group','group',identity_source='synthetic-proof')
        with self.assertRaisesRegex(ConsoleError,'verified_scope_required'): self.scope.bind(account,[chat])
        for bad in (replace(chat,kind='private'),replace(chat,account_id='other'),
                    replace(chat,identity_certain=False),replace(chat,identity_source='')):
            with self.assertRaisesRegex(ConsoleError,'verified_group_required'):
                self.scope.bind_group_reading(account,bad)
        self.scope.bind_group_reading(account,chat)
        self.scope.require('mock-a','group',permission='upload')
        self.assertTrue(self.scope.status()['read_only'])
        with self.assertRaisesRegex(ConsoleError,'target_not_bound'): self.scope.require('mock-a','friend')
        with self.assertRaisesRegex(ConsoleError,'read_only_scope'):
            self.scope.reserve_send('mock-a','group','no-send',fixed=True)
        with self.assertRaisesRegex(ConsoleError,'scope_already_bound'): self.scope.bind(account,[chat])
        self.assertEqual(self.ledger.status()['send_used'],0)

    def test_group_read_scope_allows_explicit_upload_but_blocks_reply_grant(self):
        account = Account('mock-a','synthetic',identity_source='synthetic-proof',fingerprint={'test':1})
        chat = Conversation('mock-a','group','synthetic-group','group',identity_source='synthetic-proof')
        self.scope.bind_group_reading(account,chat)
        store = ConsoleStore(':memory:',SyntheticProtector()); self.addCleanup(store.close)
        service = ConsoleService(MockConsoleAdapter(),store,acceptance=self.scope)
        service.discover()
        grant = {'read':True,'upload':True,'reply':False,'paused':False,'excluded':[]}
        service.authorize('mock-a',grant)
        with self.assertRaisesRegex(ConsoleError,'read_only_scope'):
            service.authorize('mock-a',{**grant,'reply':True})
        self.assertFalse(service.grants['mock-a'].reply)
        self.assertFalse(store.get('grant','mock-a')['grant']['reply'])
        service.resume()
        service._require('mock-a','group','upload',automatic=True)
        with self.assertRaisesRegex(ConsoleError,'read_only_scope'):
            service._require('mock-a','group','reply',automatic=True)

    def test_private_history_never_enters_context_group_is_limited_to_50(self):
        self.bind()
        rows = [{"historical": i < 50, "seq": i} for i in range(60)]
        self.assertEqual([r["seq"] for r in self.scope.filter_context("mock-a", "friend", rows)], list(range(50, 60)))
        self.assertEqual(len(self.scope.filter_context("mock-a", "group", rows)), 50)
        rows[0]["upload_eligible"] = False
        self.assertNotIn(0, [r["seq"] for r in self.scope.filter_context("mock-a", "group", rows)])

    def test_ineligible_history_over_one_page_does_not_starve_incremental_summary(self):
        from wechat_agent_bridge.console_models import ChatMessage
        from wechat_agent_bridge.providers import DEFAULT_CONFIG
        self.bind()
        store = ConsoleStore(":memory:", SyntheticProtector()); self.addCleanup(store.close)
        provider = MockProvider()
        service = ConsoleService(MockConsoleAdapter(), store, acceptance=self.scope, provider_factory=lambda c: provider)
        service.save_configuration({**DEFAULT_CONFIG, "provider":"compatible", "base_url":"https://api.openai.com/v1", "model":MODEL, "api_key":"sk-SYNTHETIC"})
        service.test_provider("text")
        service.discover()
        service.authorize("mock-a", {"read":True, "upload":True, "reply":False, "paused":False, "excluded":[]})
        service.resume()
        for i in range(220):
            store.append(ChatMessage("mock-a", "friend", str(i), "synthetic", "old synthetic history", "", historical=True, upload_eligible=False))
        store.append(ChatMessage("mock-a", "friend", "eligible-new", "synthetic", "new eligible test", ""))
        with patch.object(provider, "summarize", return_value="synthetic summary") as summarize:
            service._summarize("mock-a", "friend")
            context = summarize.call_args.args[1]
            self.assertEqual([row["text"] for row in context], ["new eligible test"])
        self.assertEqual(service.memory("mock-a", "friend")["watermark"], 221)

    def test_fixed_send_only_group_and_scoped(self):
        self.bind()
        with self.assertRaisesRegex(ConsoleError, "requires_group"):
            self.scope.reserve_send("mock-a", "friend", "request", fixed=True)
        self.assertEqual(self.ledger.status()["send_used"], 0)

    def test_all_provider_tests_charge_before_failure_and_never_reset(self):
        store = ConsoleStore(":memory:", SyntheticProtector()); self.addCleanup(store.close)
        adapter = MockConsoleAdapter(); adapter.simulation_only = False
        provider = MockProvider()
        service = ConsoleService(adapter, store, provider_factory=lambda c: provider, acceptance=self.scope)
        path = Path(self.temp.name)/"synthetic.env"
        path.write_text("OPENAI_API_KEY=sk-SYNTHETIC-ONLY", encoding="utf-8")
        configure_openai(service, path)
        self.assertTrue(service.paused)
        self.assertFalse(service.grants)
        self.assertEqual(service.config["vision_model"], MODEL)
        self.assertEqual(service.config["transcription_model"], "whisper-1")
        for kind in ("text", "vision", "transcription"):
            with patch.object(provider, "test", side_effect=ConsoleError("synthetic_timeout")):
                with self.assertRaisesRegex(ConsoleError, "synthetic_timeout"): service.test_provider(kind)
        configure_openai(service, path)
        self.assertEqual(self.ledger.status()["api_used"], 3)
        self.assertFalse(any(service.config["capabilities"].values()))
        with self.assertRaisesRegex(ConsoleError, "provider_locked"):
            service.save_configuration({"model": "different"})

    def test_delete_memory_does_not_delete_quota(self):
        self.bind()
        store = ConsoleStore(":memory:", SyntheticProtector()); self.addCleanup(store.close)
        self.ledger.reserve("send", "request", "fixed")
        self.ledger.reserve("api", "request")
        store.clear("mock-a")
        self.assertEqual((self.ledger.status()["api_used"], self.ledger.status()["send_used"]), (1, 1))
