"""Independent wire contracts derived from the official API docs, over real HTTP."""
import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from modlingo.core import ProjectError
from modlingo.provider import APIConfig, PRESETS, Translator, preset_config


# Expected wire paths are independent of the runtime endpoint builder.
CONTRACTS = {
    'deepseek': ('/chat/completions', 'chat'),
    'qwen': ('/compatible-mode/v1/chat/completions', 'chat'),
    'qwen_intl': ('/compatible-mode/v1/chat/completions', 'chat'),
    'kimi': ('/v1/chat/completions', 'chat'),
    'openai': ('/v1/responses', 'responses'),
    'openai_chat': ('/v1/chat/completions', 'chat'),
    'claude': ('/v1/messages', 'anthropic'),
    'gemini': ('/v1beta/openai/chat/completions', 'chat'),
    'siliconflow': ('/v1/chat/completions', 'chat'),
    'openrouter': ('/api/v1/chat/completions', 'chat'),
    'ollama': ('/v1/chat/completions', 'chat'),
    'lmstudio': ('/v1/chat/completions', 'chat'),
}


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.response_mode = 'normal'
        cls.failures_left = 0
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append((self.path, dict(self.headers), payload))
                if cls.response_mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/sink')
                    self.end_headers()
                    return
                if cls.response_mode == 'auth' or cls.failures_left:
                    status = 401 if cls.response_mode == 'auth' else 529
                    cls.failures_left = max(0, cls.failures_left-1)
                    body = b'{"error":{"message":"private-contract-key private body"}}'
                else:
                    status = 200
                    provider = payload['model']
                    protocol = CONTRACTS.get(provider, ('', provider))[1]
                    messages = payload.get('messages', payload.get('input'))
                    inputs = json.loads(messages[-1]['content'])['items']
                    translated = {key: value['text'].replace('Hello', '你好').replace('Bye', '再见') for key, value in inputs.items()}
                    text = json.dumps(translated, ensure_ascii=False)
                    # These are protocol-specific envelopes, not SDK convenience properties.
                    if protocol == 'anthropic':
                        envelope = {'type': 'message', 'role': 'assistant', 'stop_reason': 'end_turn',
                                    'content': [{'type': 'thinking', 'thinking': 'not translation'}, {'type': 'text', 'text': text}],
                                    'usage': {'input_tokens': 10, 'output_tokens': 15}}
                        if cls.response_mode == 'truncated':
                            envelope['stop_reason'] = 'max_tokens'
                    elif protocol == 'responses':
                        envelope = {'status': 'completed', 'output': [{'type': 'reasoning', 'summary': []},
                                    {'type': 'message', 'role': 'assistant', 'status': 'completed',
                                     'content': [{'type': 'output_text', 'text': text}]}],
                                    'usage': {'input_tokens': 10, 'output_tokens': 15, 'total_tokens': 25}}
                        if cls.response_mode == 'truncated':
                            envelope.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
                    else:
                        content = [{'type': 'text', 'text': text}] if cls.response_mode == 'blocks' else text
                        envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': content, 'reasoning_content': 'not translation'}}],
                                    'usage': {'prompt_tokens': 10, 'completion_tokens': 15, 'total_tokens': 25}}
                        if cls.response_mode == 'truncated':
                            envelope['choices'][0]['finish_reason'] = 'length'
                    body = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        type(self).requests = []
        type(self).response_mode = 'normal'
        type(self).failures_left = 0

    def local_config(self, provider):
        from urllib.parse import urlparse
        config = preset_config(provider, provider, 'private-contract-key')
        path = urlparse(config.base_url).path
        config.base_url = 'http://127.0.0.1:%d%s' % (self.server.server_port, path)
        return config

    def check_contract(self, provider):
        config = self.local_config(provider)
        if provider == 'claude':
            config.workspace_id = 'wrkspc_contract'
        translator = Translator(config)
        result = translator.translate([('greeting', 'Hello {{Name}}!$h\nBye.')], {})
        self.assertEqual(result, [('greeting', 'Hello {{Name}}!$h\nBye.', '你好 {{Name}}!$h\n再见.')])
        path, headers, body = self.requests[-1]
        headers = {key.lower(): value for key, value in headers.items()}
        expected_path, protocol = CONTRACTS[provider]
        self.assertEqual(path, expected_path)
        self.assertFalse(body['stream'])
        self.assertEqual(body['model'], provider)
        self.assertTrue(translator.last_usage)
        if protocol == 'anthropic':
            self.assertEqual(headers['x-api-key'], 'private-contract-key')
            self.assertEqual(headers['anthropic-version'], '2023-06-01')
            self.assertEqual(headers['anthropic-workspace-id'], 'wrkspc_contract')
            self.assertNotIn('authorization', headers)
            self.assertIn('system', body)
            self.assertEqual(body['messages'][0]['role'], 'user')
            self.assertEqual(body['max_tokens'], 8192)
        else:
            self.assertEqual(headers['authorization'], 'Bearer private-contract-key')
            self.assertNotIn('x-api-key', headers)
            if protocol == 'responses':
                self.assertIn('instructions', body)
                self.assertNotIn('messages', body)
                self.assertFalse(body['store'])
                self.assertEqual(body['max_output_tokens'], 8192)
            else:
                self.assertEqual(body['messages'][0]['role'], 'system')
                field = 'max_completion_tokens' if provider == 'openai_chat' else 'max_tokens'
                self.assertEqual(body[field], 8192)
        if provider in ('qwen', 'qwen_intl'):
            self.assertIs(body['enable_thinking'], False)
        else:
            self.assertNotIn('enable_thinking', body)

    def test_deepseek_http_contract(self): self.check_contract('deepseek')
    def test_qwen_http_contract(self): self.check_contract('qwen')
    def test_qwen_international_http_contract(self): self.check_contract('qwen_intl')
    def test_kimi_http_contract(self): self.check_contract('kimi')
    def test_openai_responses_http_contract(self): self.check_contract('openai')
    def test_openai_chat_http_contract(self): self.check_contract('openai_chat')
    def test_claude_native_http_contract(self): self.check_contract('claude')
    def test_gemini_compatible_http_contract(self): self.check_contract('gemini')
    def test_siliconflow_http_contract(self): self.check_contract('siliconflow')
    def test_openrouter_http_contract(self): self.check_contract('openrouter')
    def test_ollama_http_contract(self): self.check_contract('ollama')
    def test_lmstudio_http_contract(self): self.check_contract('lmstudio')

    def test_truncated_valid_json_is_rejected_for_every_protocol(self):
        type(self).response_mode = 'truncated'
        for provider in ('deepseek', 'openai', 'claude'):
            with self.subTest(provider=provider), self.assertRaises(ProjectError):
                Translator(self.local_config(provider)).translate([('a', 'Hello')], {})

    def test_http_auth_failures_are_sanitized_for_every_protocol(self):
        type(self).response_mode = 'auth'
        for provider in ('deepseek', 'openai', 'claude'):
            before = len(self.requests)
            with self.subTest(provider=provider), self.assertRaises(ProjectError) as caught:
                Translator(self.local_config(provider)).translate([('a', 'Hello')], {})
            self.assertNotIn('private-contract-key', str(caught.exception))
            self.assertNotIn('private body', str(caught.exception))
            self.assertEqual(len(self.requests), before + 1)

    def test_redirect_never_forwards_credentials(self):
        type(self).response_mode = 'redirect'
        for provider in ('deepseek', 'openai', 'claude'):
            with self.subTest(provider=provider), self.assertRaisesRegex(ProjectError, '重定向'):
                Translator(self.local_config(provider)).translate([('a', 'Hello')], {})
        self.assertEqual(len(self.requests), 3)
        self.assertFalse(any(path == '/sink' for path, _, _ in self.requests))

    def test_claude_overload_529_retries_then_succeeds(self):
        type(self).failures_left = 2
        with patch('modlingo.provider.time.sleep'):
            self.check_contract('claude')
        self.assertEqual(len(self.requests), 3)

    def test_chat_text_blocks_and_reasoning_are_separated(self):
        type(self).response_mode = 'blocks'
        self.check_contract('deepseek')

    def test_only_known_text_is_accepted_and_refusals_tools_are_rejected(self):
        cases = [
            ('chat', {'choices': [{'message': {'content': None, 'reasoning_content': '{"0":"fake"}'}}]}),
            ('chat', {'choices': [{'message': {'content': 'text', 'tool_calls': [{}]}}]}),
            ('chat', {'choices': [{'message': {'content': 'text', 'refusal': 'refused'}}]}),
            ('anthropic', {'stop_reason': 'end_turn', 'content': [{'type': 'thinking', 'thinking': 'fake'}]}),
            ('anthropic', {'stop_reason': 'end_turn', 'content': [{'type': 'tool_use', 'input': {}}]}),
            ('responses', {'status': 'completed', 'output': [{'type': 'reasoning'}]}),
            ('responses', {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}),
        ]
        for protocol, payload in cases:
            with self.subTest(protocol=protocol, payload=payload), self.assertRaises(ProjectError):
                Translator(APIConfig(protocol=protocol)).parse_response(payload)

    def test_endpoint_protocol_mismatch_and_invalid_limits_are_rejected(self):
        for protocol in ('chat', 'responses', 'anthropic'):
            suffix = {'chat': '/chat/completions', 'responses': '/responses', 'anthropic': '/messages'}[protocol]
            config = APIConfig('https://example.test/v1' + suffix, 'model', 'key', protocol=protocol)
            self.assertEqual(config.endpoint(), config.base_url)
        for config in [APIConfig('https://example.test/v1/messages', 'model', 'key'),
                       APIConfig('https://example.test', 'model', 'key', max_output_tokens=True),
                       APIConfig('https://example.test', 'model', 'key', timeout=0),
                       APIConfig('https://example.test', 'model', 'key', protocol='unknown'),
                       APIConfig('https://example.test', 'model', 'key', workspace_id='bad\r\nHeader'),
                       APIConfig('https://example.test', 'model', 'key', provider='claude')]:
            with self.subTest(config=repr(config).replace('key', '[redacted]')), self.assertRaises(ProjectError):
                config.endpoint()

    def test_optional_thinking_parameters_do_not_leak_to_other_vendors(self):
        for provider in ('deepseek', 'kimi'):
            config = preset_config(provider, 'model', 'key')
            config.disable_thinking = True
            _, body, _ = Translator(config).request_parts([])
            self.assertEqual(body['thinking'], {'type': 'disabled'})
        config = preset_config('openai', 'model', 'key')
        config.disable_thinking = True
        with self.assertRaises(ProjectError):
            config.endpoint()
