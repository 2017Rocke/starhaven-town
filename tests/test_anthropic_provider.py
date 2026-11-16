"""Anthropic Messages compatibility; synthetic credentials and offline responses only."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from provider_bridge import ProviderBridge, SettingsError, endpoint_identity, normalize_base_url, request_url
from test_provider_bridge import MemoryCredentials, MockOpener


def message(text='已完成回复', **updates):
    value = {'type': 'message', 'role': 'assistant', 'content': [{'type': 'text', 'text': text}],
             'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 4}}
    value.update(updates)
    return json.dumps(value).encode('utf-8')


class RecordedBudget:
    def __init__(self):
        self.settled = []
        self.admitted = []

    def config(self):
        return {'max_output_tokens': 321}

    def admit(self, config, payload, context):
        self.admitted.append((config, json.loads(payload)))
        context['call_id'] = 'synthetic-call'

    def dispatch(self, context):
        pass

    def settle(self, context):
        self.settled.append(dict(context))

    def blocked(self, config, context):
        pass

    @staticmethod
    def usage(raw):
        return raw if isinstance(raw, dict) else None


class AnthropicProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.credentials = MemoryCredentials()
        self.opener = MockOpener(body=message())
        self.bridge = ProviderBridge(self.root / 'runtime', self.root / 'data', self.credentials, lambda: self.opener)
        self.config = {'mode': 'api', 'protocol': 'anthropic', 'base_url': 'https://messages.example', 'model': 'synthetic-model'}
        self.bridge.save_settings(self.config)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def call(self):
        return self.bridge.generate_result('用户材料', threading.Event(), 'synthetic-agent')

    def test_messages_urls_normalize_without_duplicate_v1_and_keep_prefix(self):
        for prefix in ('https://messages.example', 'https://gateway.example/private/proxy'):
            for suffix in ('', '/', '/v1', '/v1/', '/v1/messages', '/v1/messages/'):
                with self.subTest(prefix=prefix, suffix=suffix):
                    self.assertEqual(prefix, normalize_base_url(prefix + suffix, 'anthropic'))
                    self.assertEqual(prefix + '/v1/messages', request_url({**self.config, 'base_url': prefix + suffix}))
        self.assertEqual('https://openai.example/v1', normalize_base_url('https://openai.example/v1/chat/completions'))
        self.assertEqual('https://openai.example/chat/completions', request_url({'base_url': 'https://openai.example'}))
        for invalid in ('https://user:pass@messages.example', 'https://messages.example?api_key=secret', 'http://remote.example', 'file:///tmp/private'):
            with self.subTest(invalid=invalid), self.assertRaises(SettingsError):
                normalize_base_url(invalid, 'anthropic')

    def test_endpoint_identity_preserves_legacy_grants_and_binds_new_auth(self):
        old = {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}
        self.assertEqual(old, endpoint_identity(old))
        self.assertEqual(old, endpoint_identity({**old, 'protocol': 'openai', 'auth_type': 'bearer'}))
        anthropic = endpoint_identity({**self.config, 'base_url': 'https://messages.example/v1/messages'})
        self.assertEqual({'base_url': 'https://messages.example', 'model': 'synthetic-model', 'protocol': 'anthropic', 'auth_type': 'api_key'}, anthropic)
        self.assertNotEqual(anthropic, endpoint_identity({**self.config, 'auth_type': 'bearer'}))
        self.assertTrue(ProviderBridge.budget_capability(old)['enforce_supported'])
        for config in ({**old, 'protocol': 'anthropic'}, {**old, 'auth_type': 'api_key'}, self.config):
            self.assertFalse(ProviderBridge.budget_capability(config)['enforce_supported'])

    def test_unknown_protocol_and_openai_api_key_auth_are_rejected(self):
        for updates in ({'protocol': 'other'}, {'protocol': 'openai', 'auth_type': 'api_key'}, {'auth_type': 'cookie'}, {'protocol': None}):
            with self.subTest(updates=updates), self.assertRaises(SettingsError):
                self.bridge.save_settings({**self.config, **updates})
        with self.assertRaises(SettingsError):
            request_url({**self.config, 'auth_type': 'cookie'})

    def test_old_saved_configuration_loads_as_openai_bearer(self):
        old = {'mode': 'api', 'base_url': 'https://old.example/v1', 'model': 'old-model'}
        self.bridge.path.write_text(json.dumps(old))
        restored = ProviderBridge(self.root / 'restored-runtime', self.root / 'data', self.credentials, lambda: self.opener)
        self.addCleanup(restored.close)
        settings = restored.settings()
        self.assertEqual('openai', settings['protocol'])
        self.assertEqual('bearer', settings['auth_type'])
        self.assertEqual(old['base_url'], settings['base_url'])
        self.assertEqual(old['model'], settings['model'])

    def test_canonical_address_is_the_only_credential_storage_key(self):
        key = 'synthetic-messages-key'
        saved = self.bridge.save_settings({**self.config, 'base_url': 'https://messages.example/v1/messages', 'api_key': key})
        self.assertEqual({'https://messages.example': key}, self.credentials.items)
        self.assertTrue(saved['has_api_key'])
        self.assertNotIn(key, json.dumps(saved))
        self.assertNotIn(key, self.bridge.path.read_text())
        for suffix in ('', '/v1', '/v1/messages'):
            self.assertTrue(self.bridge.save_settings({**self.config, 'base_url': 'https://messages.example' + suffix})['has_api_key'])
        self.assertFalse(self.bridge.save_settings({**self.config, 'base_url': 'https://different.example'})['has_api_key'])

    def test_x_api_key_top_level_system_required_cap_and_text_blocks(self):
        key = 'synthetic-messages-key'
        self.bridge.save_settings({**self.config, 'api_key': key})
        self.opener.body = message(content=[{'type': 'thinking', 'thinking': 'PRIVATE_REASONING'},
                                             {'type': 'text', 'text': '第一段'},
                                             {'type': 'text', 'text': '第二段'},
                                             {'type': 'tool_use', 'name': 'not_executed', 'input': {'secret': 'PRIVATE_TOOL'}}])
        result = self.call()
        request = self.opener.requests[0]
        headers = {k.lower(): v for k, v in request.header_items()}
        payload = json.loads(request.data)
        self.assertEqual('https://messages.example/v1/messages', request.full_url)
        self.assertEqual('2023-06-01', headers['anthropic-version'])
        self.assertEqual(key, headers['x-api-key'])
        self.assertNotIn('authorization', headers)
        self.assertEqual(False, payload['stream'])
        self.assertEqual(1024, payload['max_tokens'])
        self.assertIsInstance(payload['system'], str)
        self.assertEqual([{'role': 'user', 'content': '用户材料'}], payload['messages'])
        self.assertNotIn('tools', payload)
        self.assertEqual('第一段\n\n第二段', result['text'])
        self.assertNotIn('PRIVATE', result['text'])

    def test_explicit_bearer_is_exclusive_and_key_echo_is_redacted(self):
        key = 'synthetic-bearer-key'
        self.bridge.save_settings({**self.config, 'auth_type': 'bearer', 'api_key': key})
        self.opener.body = message('echo ' + key)
        self.assertNotIn(key, self.call()['text'])
        headers = {k.lower(): v for k, v in self.opener.requests[0].header_items()}
        self.assertEqual('Bearer ' + key, headers['authorization'])
        self.assertNotIn('x-api-key', headers)
        self.assertEqual('2023-06-01', headers['anthropic-version'])

    def test_usage_includes_cache_tokens_and_preserves_budget_output_limit(self):
        budget = RecordedBudget()
        self.bridge.set_budget(budget)
        self.opener.body = message(usage={'input_tokens': 10, 'output_tokens': 7, 'cache_creation_input_tokens': 20, 'cache_read_input_tokens': 30})
        result = self.call()
        expected = {'prompt_tokens': 60, 'completion_tokens': 7, 'total_tokens': 67}
        self.assertEqual(expected, result['usage'])
        self.assertEqual('reported', result['usage_quality'])
        self.assertEqual(expected, budget.settled[-1]['usage'])
        self.assertEqual(321, json.loads(self.opener.requests[-1].data)['max_tokens'])
        self.assertEqual('anthropic', budget.admitted[-1][0]['protocol'])

    def test_missing_or_invalid_usage_remains_unknown_without_zero_fabrication(self):
        budget = RecordedBudget()
        self.bridge.set_budget(budget)
        invalid = (None, {}, {'input_tokens': 1}, {'input_tokens': True, 'output_tokens': 3},
                   {'input_tokens': 2, 'output_tokens': '3'}, {'input_tokens': -1, 'output_tokens': 3},
                   {'input_tokens': 1, 'output_tokens': 2, 'cache_read_input_tokens': None},
                   {'input_tokens': 1, 'output_tokens': 2, 'cache_creation_input_tokens': 1.5},
                   {'input_tokens': 1000000000, 'output_tokens': 1})
        for usage in invalid:
            with self.subTest(usage=usage):
                self.opener.body = message(usage=usage)
                result = self.call()
                self.assertIsNone(result['usage'])
                self.assertEqual('unknown', result['usage_quality'])
                self.assertIsNone(budget.settled[-1]['usage'])

    def test_truncated_reply_is_rejected_but_usage_is_settled(self):
        budget = RecordedBudget()
        self.bridge.set_budget(budget)
        self.opener.body = message('{"incomplete":', stop_reason='max_tokens')
        with self.assertRaisesRegex(RuntimeError, '上限'):
            self.call()
        self.assertNotIn('reply', budget.settled[-1])
        self.assertEqual({'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}, budget.settled[-1]['usage'])

    def test_non_text_or_unfinished_content_never_becomes_reply(self):
        for updates in ({'content': [{'type': 'thinking', 'thinking': 'hidden'}]},
                        {'content': [{'type': 'tool_use', 'name': 'command', 'input': {'cmd': 'ignored'}}], 'stop_reason': 'tool_use'},
                        {'stop_reason': 'pause_turn'}, {'stop_reason': None},
                        {'content': [{'type': 'text', 'text': 123}]}):
            with self.subTest(updates=updates):
                self.opener.body = message(**updates)
                with self.assertRaises(RuntimeError):
                    self.call()

    def test_status_messages_distinguish_auth_permission_and_missing_path_safely(self):
        secret = 'synthetic-response-secret'
        for status, clue in ((401, '认证失败'), (403, '拒绝访问'), (404, '不存在')):
            with self.subTest(status=status):
                self.opener = MockOpener(error=urllib.error.HTTPError('https://messages.example/' + secret, status, secret, {}, io.BytesIO(secret.encode())))
                with self.assertRaisesRegex(RuntimeError, clue) as caught:
                    self.call()
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn(secret, str(caught.exception))


class AnthropicLoopbackTest(unittest.TestCase):
    def test_messages_path_headers_and_redirect_never_forward_credentials(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append({'path': self.path, 'headers': dict(self.headers), 'body': json.loads(body)})
                self.send_response(307)
                self.send_header('Location', 'http://127.0.0.1:%d/leaked' % self.server.server_port)
                self.send_header('Content-Length', '0')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                bridge = ProviderBridge(Path(directory) / 'runtime', directory, MemoryCredentials())
                try:
                    bridge.save_settings({'mode': 'api', 'protocol': 'anthropic', 'base_url': 'http://127.0.0.1:%d/custom/v1/messages' % server.server_port,
                                          'model': 'local-synthetic', 'api_key': 'loopback-only-fake-key'})
                    with self.assertRaisesRegex(RuntimeError, '重定向'):
                        bridge.generate('local only', threading.Event())
                finally:
                    bridge.close()
            self.assertEqual(1, len(received))
            self.assertEqual('/custom/v1/messages', received[0]['path'])
            headers = {key.lower(): value for key, value in received[0]['headers'].items()}
            self.assertEqual('loopback-only-fake-key', headers['x-api-key'])
            self.assertNotIn('authorization', headers)
            self.assertEqual('2023-06-01', headers['anthropic-version'])
            self.assertEqual(1024, received[0]['body']['max_tokens'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)


if __name__ == '__main__':
    unittest.main()
