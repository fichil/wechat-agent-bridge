from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from backend.app.ai_provider import (INPUT_TEXT_LIMIT, OUTPUT_TEXT_LIMIT, BODY_LIMIT,
                                    build_request, split_input, system_instructions, encode, parse_response)
from backend.app.conversations import ConversationError
from test_roles import RoleTestCase, card
import test_ai_execution as execution


class InputBudgetTests(unittest.TestCase):
    def envelope(self):
        return dict(request_id='synthetic', instructions='中文提示🙂', character_card={}, memory='记忆👨‍👩‍👧‍👦', messages=[])

    def test_utf8_exact_boundary_includes_system_and_serialization(self):
        envelope = self.envelope()
        size = len(encode({k:v for k,v in envelope.items() if k != 'instructions'}).encode()) + len(system_instructions(envelope).encode())
        envelope['memory'] += 'x' * (INPUT_TEXT_LIMIT - size)
        split_input(envelope)
        original = deepcopy(envelope)
        envelope['memory'] += 'x'
        with self.assertRaises(ConversationError) as caught:
            split_input(envelope)
        self.assertEqual(caught.exception.code, 'ai_input_too_large')
        self.assertIn(str(INPUT_TEXT_LIMIT + 1), caught.exception.message)
        for label in ('角色卡', '记忆', '系统提示', '封装及其他字段', '尚未调用模型'):
            self.assertIn(label, caught.exception.message)
        self.assertNotIn(original['memory'], caught.exception.message)
        self.assertEqual(envelope['memory'], original['memory'] + 'x')

    def test_all_protocols_keep_large_fixed_content_and_actual_system(self):
        envelope = self.envelope()
        envelope.update(character_card={'data': {'system_prompt': '角色' * 5500}}, memory='记忆' * 4500,
                        reply_media={'capabilities': {'image': False, 'sticker': False}, 'stickers': [], 'images': []})
        envelope['instructions'] += '提示' * 1500
        self.assertGreater(len(encode(envelope).encode()), 65536)
        for protocol in ('openai_responses', 'openai_chat_completions', 'anthropic_messages', 'gemini_generate_content'):
            profile = dict(protocol=protocol, base_url='https://example.test/v1', model='synthetic', provider='custom', anthropic_version='2023-06-01')
            _, _, body = build_request(profile, 'synthetic-secret', envelope, 'schema')
            value = json.loads(body)
            if protocol == 'openai_responses':
                system, text = value['instructions'], value['input'][0]['content'][0]['text']
            elif protocol == 'openai_chat_completions':
                system, text = value['messages'][0]['content'], value['messages'][1]['content'][0]['text']
            elif protocol == 'anthropic_messages':
                system, text = value['system'], value['messages'][0]['content'][0]['text']
            else:
                system, text = value['systemInstruction']['parts'][0]['text'], value['contents'][0]['parts'][0]['text']
            self.assertEqual(system, system_instructions(envelope))
            self.assertEqual(json.loads(text), {k:v for k,v in envelope.items() if k != 'instructions'})

    def test_output_limit_is_unchanged(self):
        result = dict(memory='记忆🙂', reply='')
        size = len(encode(result).encode())
        result['memory'] += 'x' * (OUTPUT_TEXT_LIMIT - size)
        def response():
            return dict(choices=[dict(finish_reason='stop', message=dict(content=encode(result)))])
        self.assertEqual(parse_response('openai_chat_completions', response()), result)
        result['memory'] += 'x'
        with self.assertRaises(ConversationError) as caught:
            parse_response('openai_chat_completions', response())
        self.assertEqual(caught.exception.code, 'ai_output_capacity')

    def test_generation_budget_supports_complete_memory_without_changing_unknown_models(self):
        for provider, model, expected in (('grok', 'grok-4.7', 32768), ('deepseek', 'deepseek-flash', 32768),
                                          ('grok', 'legacy-model', 8192), ('deepseek', 'deepseek-chat', 8192),
                                          ('custom', 'grok-4.7', 8192), ('openai', 'synthetic-model', 8192)):
            for protocol in ('openai_responses', 'openai_chat_completions', 'anthropic_messages', 'gemini_generate_content'):
                with self.subTest(provider=provider, model=model, protocol=protocol):
                    profile = dict(provider=provider, model=model, protocol=protocol, base_url='https://example.test/v1', anthropic_version='2023-06-01')
                    envelope = self.envelope()
                    original = deepcopy(envelope)
                    _, _, body = build_request(profile, '', envelope, 'schema')
                    value = json.loads(body)
                    actual = value.get('max_output_tokens', value.get('max_completion_tokens', value.get('max_tokens', value.get('generationConfig', {}).get('maxOutputTokens'))))
                    self.assertEqual(actual, expected)
                    self.assertEqual(envelope, original)
                    self.assertEqual(OUTPUT_TEXT_LIMIT, 65536)

    def test_images_still_have_separate_body_limit(self):
        envelope = self.envelope()
        envelope['messages'] = [dict(message_id='img', content={'image': {'mime_type': 'image/jpeg', 'data_base64': 'A' * BODY_LIMIT}})]
        original = deepcopy(envelope)
        text, images = split_input(envelope)
        self.assertLess(len(text), INPUT_TEXT_LIMIT)
        self.assertEqual(len(images), 1)
        profile = dict(protocol='openai_responses', base_url='https://example.test/v1', model='synthetic')
        with self.assertRaises(ConversationError) as caught:
            build_request(profile, '', envelope, 'schema')
        self.assertIn(str(BODY_LIMIT), caught.exception.message)
        self.assertIn('尚未调用模型', caught.exception.message)
        self.assertEqual(envelope, original)


class InputBudgetExecutionTests(RoleTestCase):
    configured = execution.ExecutionTests.configured
    ingest = execution.ExecutionTests.ingest
    start = execution.ExecutionTests.start
    wait_run = execution.ExecutionTests.wait_run

    def test_large_card_and_memory_are_preserved_through_execution(self):
        document = card()
        document['data']['system_prompt'] = '角色' * 5500
        self.create(document)
        self.create_api()
        memory = '旧记忆🙂' * 3000
        self.assertEqual(self.update(ai_enabled=True, memory=memory).status_code, 200)
        for index in range(1, 23):
            self.ingest(index)
        with patch.object(self.provider, 'generate', return_value=dict(memory=memory, reply='', role_card_update='')) as provider:
            run = self.wait_run(self.start())
        self.assertEqual(run['status'], 'completed', run)
        envelope = provider.call_args.args[2]
        self.assertEqual(len(envelope['messages']), 22)
        self.assertEqual(envelope['memory'], memory)
        self.assertEqual(envelope['character_card'], document)
        self.assertGreater(len(encode(envelope).encode()), 65536)
        self.assertEqual(self.preferences()['memory'], memory)
