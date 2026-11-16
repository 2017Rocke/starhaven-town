"""Messages accounting and grants use offline replies and temporary state only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from provider_bridge import ProviderBridge
from server import TownApp
from town_store import TownError
import test_dev_runtime as runtime_fixtures
from test_provider_bridge import MemoryCredentials, MockOpener


class MessagesBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.opener = MockOpener(body=json.dumps({
            'type': 'message', 'role': 'assistant', 'stop_reason': 'end_turn',
            'content': [{'type': 'text', 'text': '离线回执'}],
            'usage': {'input_tokens': 7, 'output_tokens': 3,
                      'cache_creation_input_tokens': 11, 'cache_read_input_tokens': 13},
        }).encode())
        self.bridge = ProviderBridge(root/'runtime', root/'data', MemoryCredentials(), lambda:self.opener)
        self.config = dict(mode='api', protocol='anthropic', auth_type='api_key',
                           base_url='https://messages.example', model='provided-model')
        self.bridge.save_settings(self.config)
        self.app = TownApp(root/'data', self.bridge, root/'team', report_root=root/'reports',
                           owner_secret='b'*64, start_growth=False)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def authorize(self, endpoints, strict=True):
        intent = self.app.owner.issue(dict(action='policy', payload=dict(enabled=False,
            strict=strict, residents=['linghang'], endpoints=endpoints)))
        preview = self.app.owner.preview(intent['id'])
        self.app.owner.confirm(dict(id=intent['id'], digest=preview['digest'], approved=True))

    def test_ordinary_chat_counts_cache_tokens_and_records_protocol(self):
        result = self.bridge.generate_result('合成资料', threading.Event(), 'linghang')
        self.assertEqual('离线回执', result['text'])
        self.assertEqual(34, result['usage']['total_tokens'])
        with self.app.db() as db:
            row = db.execute('SELECT * FROM v2_calls').fetchone()
            self.assertEqual(34, row['charged'])
            self.assertEqual('settled', row['status'])
            event = json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='call_reserved'").fetchone()[0])
            self.assertEqual('anthropic', event['protocol'])
            self.assertEqual('api_key', event['auth_type'])

    def test_no_inherited_controlled_budget_capability(self):
        with self.assertRaisesRegex(RuntimeError, '用量上限适配'):
            self.bridge.generate_result('合成需求', threading.Event(), 'linghang',
                                        dict(controlled=True, workbench=True))
        self.assertEqual([], self.opener.requests)

    def test_native_policy_normalizes_messages_route_and_denies_other_protocol(self):
        self.authorize([dict(base_url='https://messages.example/v1/messages', model='provided-model',
                             protocol='anthropic', auth_type='api_key')])
        self.assertEqual('https://messages.example', self.app.budget.config()['endpoints'][0]['base_url'])
        self.app.budget.check_network('https://messages.example/v1/messages', 'model')
        with self.assertRaises(TownError):
            self.app.budget.check_network('https://messages.example/chat/completions', 'model')

    def test_auth_switch_needs_distinct_native_model_grant(self):
        endpoint = {k:v for k,v in self.config.items() if k!='mode'}
        self.authorize([endpoint])
        changed = dict(self.config, auth_type='bearer')
        # A synthetic verified profile isolates authorization from capability.
        with patch.object(ProviderBridge, 'budget_capability', return_value={'enforce_supported':True}):
            with self.assertRaisesRegex(TownError, '端点未经授权'):
                self.app.budget.admit(changed, b'{"max_tokens":128}',
                                     {'agent_id':'linghang','options':{'controlled':True,'workbench':True}})

    def test_empty_endpoint_cannot_enter_native_policy(self):
        for invalid in ('', '  ', None):
            with self.subTest(base_url=invalid), self.assertRaises(TownError):
                self.authorize([dict(base_url=invalid, model='provided-model', protocol='anthropic')])


class MessagesSourceGrantTest(unittest.TestCase):
    def setUp(self):
        self.f = runtime_fixtures.DevRuntimeTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_switching_protocol_invalidates_existing_source_candidate(self):
        f = self.f
        f.connect(); f.ready()
        candidate = f.develop()
        original = f.bridge.settings
        f.bridge.settings = lambda: dict(original(), protocol='anthropic', auth_type='api_key')
        with self.assertRaisesRegex(TownError, '授权已改变'):
            f.app.dev_runtime.normalize_apply(f.apply_payload(candidate))
        self.assertIn('value = 1', (f.repo/'example.py').read_text())

    def test_explicit_legacy_openai_defaults_do_not_revoke_existing_grant(self):
        f = self.f
        f.connect(); f.ready()
        candidate = f.develop()
        original = f.bridge.settings
        f.bridge.settings = lambda: dict(original(), protocol='openai', auth_type='bearer')
        self.assertEqual(candidate['run_id'], f.app.dev_runtime.normalize_apply(f.apply_payload(candidate))['run_id'])


if __name__ == '__main__':
    unittest.main()
