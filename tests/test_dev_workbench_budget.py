"""Workbench calls keep native resident grants and budget gates, without training."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from provider_bridge import ProviderBridge
from server import TownApp
from test_provider_bridge import MemoryCredentials, MockOpener

ENDPOINT = 'https://api.openai.com/v1'


class DevWorkbenchBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        response = {'choices': [{'message': {'content': 'offline response'}}],
                    'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}
        self.opener = MockOpener(body=json.dumps(response).encode())
        self.bridge = ProviderBridge(self.root / 'runtime', self.root / 'data', MemoryCredentials(), lambda: self.opener)
        self.bridge.save_settings({'mode': 'api', 'base_url': ENDPOINT, 'model': 'gpt-4o-mini'})
        self.app = TownApp(self.root / 'data', self.bridge, self.root / 'team', report_root=self.root / 'reports', owner_secret='e' * 64, start_growth=False)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def configure(self, **updates):
        payload = {'enabled': False, 'strict': True, 'residents': ['linghang'],
                   'endpoints': [{'base_url': ENDPOINT, 'model': 'gpt-4o-mini'}],
                   'daily_tokens': 100000, 'daily_calls': 12}
        payload.update(updates)
        intent = self.app.owner.issue({'action': 'policy', 'payload': payload})
        preview = self.app.owner.preview(intent['id'])
        self.app.owner.confirm({'id': intent['id'], 'digest': preview['digest'], 'approved': True})

    def call(self, agent='linghang', **options):
        return self.bridge.generate_result('研发资料的离线测试', threading.Event(), agent,
                                           {'controlled': True, 'workbench': True, **options})

    def ledger(self):
        with self.app.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM v2_calls ORDER BY rowid')]

    def test_authorized_workbench_reserves_and_settles_with_training_disabled(self):
        self.configure()
        self.assertFalse(self.app.budget.config()['enabled'])
        result = self.call()
        self.assertEqual('offline response', result['text'])
        self.assertEqual(1, len(self.opener.requests))
        row = self.ledger()[0]
        self.assertEqual('development', row['purpose'])
        self.assertEqual('settled', row['status'])
        self.assertEqual(1, row['sent'])
        self.assertEqual(8, row['charged'])
        self.assertGreaterEqual(row['reserved'], len(self.opener.requests[0].data) + 1024)
        with self.assertRaisesRegex(RuntimeError, '自动训练尚未启用'):
            self.call(workbench=False)
        self.assertEqual(1, len(self.opener.requests))

    def test_workbench_cannot_bypass_missing_or_revoked_resident_grants(self):
        with self.assertRaisesRegex(RuntimeError, '未授权|已暂停'):
            self.call()
        self.configure()
        with self.assertRaisesRegex(RuntimeError, '未授权|已暂停'):
            self.call('scout')
        self.app.budget.revoke('linghang')
        with self.assertRaisesRegex(RuntimeError, '已暂停'):
            self.call()
        self.assertEqual([], self.opener.requests)
        self.assertEqual([], self.ledger())

    def test_workbench_still_enforces_request_daily_lifetime_and_call_limits(self):
        for limit, value, message in (('request_total_cap', 1000, '单次上限'),
                                      ('daily_tokens', 1000, '每日上限'),
                                      ('lifetime_tokens', 1000, '累计额度')):
            with self.subTest(limit=limit):
                self.configure(**{limit: value})
                with self.assertRaisesRegex(RuntimeError, message):
                    self.call()
                self.assertEqual([], self.opener.requests)
                self.assertEqual([], self.ledger())
        self.configure(daily_calls=1)
        self.call()
        with self.assertRaisesRegex(RuntimeError, '今日调用次数'):
            self.call()
        self.assertEqual(1, len(self.opener.requests))
        self.assertEqual(1, len(self.ledger()))

    def test_paused_or_unapproved_endpoint_does_not_dispatch(self):
        self.configure(endpoints=[])
        with self.assertRaisesRegex(RuntimeError, '端点未经授权'):
            self.call()
        self.configure()
        self.app.budget.pause()
        with self.assertRaisesRegex(RuntimeError, '已暂停'):
            self.call()
        self.assertEqual([], self.opener.requests)

    def test_revocation_after_reservation_releases_unsent_workbench_call(self):
        self.configure()
        settlement_started = threading.Event()
        allow_settlement = threading.Event()
        settlement_finished = threading.Event()
        original_settle = self.app.budget.settle

        def delayed_settle(context):
            settlement_started.set()
            try:
                if not allow_settlement.wait(10):
                    raise RuntimeError('Test did not release the settlement barrier')
                original_settle(context)
            finally:
                settlement_finished.set()

        self.app.budget.settle = delayed_settle
        try:
            with self.assertRaisesRegex(RuntimeError, '已取消'):
                self.call(before_dispatch=lambda: self.app.budget.revoke('linghang'))
            self.assertTrue(settlement_started.wait(10), 'Cancelled worker did not start settlement')
            # Cancellation returns promptly; disk-backed accounting finishes in
            # the worker. Exercise that interval explicitly on every platform.
            self.assertEqual('reserved', self.ledger()[0]['status'])
        finally:
            allow_settlement.set()
        self.assertTrue(settlement_finished.wait(10), 'Cancelled worker did not finish settlement')
        self.assertEqual([], self.opener.requests)
        row = self.ledger()[0]
        self.assertEqual('development', row['purpose'])
        self.assertEqual('released', row['status'])
        self.assertEqual(0, row['sent'])
        self.assertEqual(0, row['charged'])


if __name__ == '__main__':
    unittest.main()
