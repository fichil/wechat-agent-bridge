"""Synthetic DeepSeek protocol/configuration tests; no external requests."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from tests.test_console import SyntheticProtector
from wechat_agent_bridge.acceptance import AcceptanceLedger, AcceptanceSession, MODEL
from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.providers import CompatibleProvider, DEFAULT_CONFIG, MockProvider
from wechat_agent_bridge.provider_presets import preset


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.store=ConsoleStore(':memory:',SyntheticProtector())
        self.addCleanup(self.store.close)
        self.provider=MockProvider()
        self.service=ConsoleService(MockConsoleAdapter(),self.store,provider_factory=lambda c:self.provider)

    def test_switch_clears_previous_service_key_and_cancels_old_upload_grant(self):
        self.service.save_configuration(dict(provider='compatible',base_url='https://api.openai.com/v1',
                                             model=MODEL,api_key='sk-SYNTHETIC-OPENAI'))
        self.service.discover()
        self.service.authorize('mock-a',dict(read=True,upload=True,reply=True,paused=False))
        self.service.resume()
        self.service.select_provider_preset('deepseek')
        self.assertEqual(self.service.config['api_key'],'')
        self.assertTrue(self.service.paused)
        self.assertFalse(self.service.configuration()['has_api_key'])
        with self.assertRaisesRegex(ConsoleError,'upload_not_authorized'):
            self.service._require('mock-a','friend','upload')

    def test_same_preset_preserves_key_evidence_and_call_count(self):
        self.service.select_provider_preset('deepseek')
        self.service.save_configuration(dict(api_key='sk-SYNTHETIC-DEEPSEEK'))
        self.service.test_provider('text')
        usage=self.service.status()['usage']
        self.service.select_provider_preset('deepseek')
        self.assertEqual(self.service.config['api_key'],'sk-SYNTHETIC-DEEPSEEK')
        self.assertTrue(self.service.config['capabilities']['text'])
        self.assertEqual(self.service.status()['usage'],usage)

    def test_reply_uses_deepseek_json_and_non_thinking_without_key_in_body(self):
        transport=Mock(return_value={'choices':[{'message':{'content':'{"reply":"测试成功"}'},'finish_reason':'stop'}]})
        p=CompatibleProvider({**DEFAULT_CONFIG,**preset('deepseek'),'api_key':'sk-SYNTHETIC'},transport)
        self.assertEqual(p.reply('',[{'text':'synthetic'}]),'测试成功')
        path,body,mime=transport.call_args.args
        body=json.loads(body)
        self.assertEqual(path,'/chat/completions')
        self.assertEqual(body['thinking'],{'type':'disabled'})
        self.assertEqual(body['response_format'],{'type':'json_object'})
        self.assertNotIn('sk-SYNTHETIC',str(body))
        self.assertNotIn('store',body)
        p.summarize('',[{'text':'synthetic'}])
        self.assertNotIn('response_format',json.loads(transport.call_args.args[1]))

    def test_missing_key_and_empty_or_truncated_response_do_not_generate_reply(self):
        transport=Mock()
        p=CompatibleProvider({**DEFAULT_CONFIG,**preset('deepseek')},transport)
        with self.assertRaisesRegex(ConsoleError,'api_key_missing'): p.test()
        transport.assert_not_called()
        p=CompatibleProvider({**DEFAULT_CONFIG,**preset('deepseek'),'api_key':'sk-SYNTHETIC'},transport)
        for content,finish in [('', 'stop'),('{"reply":"test"}', 'length')]:
            transport.return_value={'choices':[{'message':{'content':content},'finish_reason':finish}]}
            with self.assertRaises(ConsoleError): p.test()

    def test_deepseek_acceptance_reuses_ledger_and_keeps_failures_charged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=AcceptanceLedger(Path(tmp)/'quota.sqlite3')
            try:
                ledger.reserve('api','previous-openai-request')
                scope=AcceptanceSession(ledger,provider_profile='deepseek')
                service=ConsoleService(MockConsoleAdapter(),self.store,provider_factory=lambda c:self.provider,acceptance=scope)
                service.select_provider_preset('deepseek')
                with self.assertRaisesRegex(ConsoleError,'api_key_missing'): service.test_provider('text')
                self.assertEqual(ledger.status()['api_used'],1)
                service.save_configuration(dict(api_key='sk-SYNTHETIC-DEEPSEEK'))
                self.provider.test=Mock(side_effect=ConsoleError('synthetic_timeout'))
                with self.assertRaisesRegex(ConsoleError,'synthetic_timeout'): service.test_provider('text')
                self.assertEqual(ledger.status()['api_used'],2)
                with self.assertRaisesRegex(ConsoleError,'provider_locked'):
                    service.save_configuration(dict(base_url='https://api.openai.com/v1',model=MODEL))
            finally: ledger.close()
