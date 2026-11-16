"""Development settings use state-bound native approval without expanding access."""
import json
import unittest

import test_dev_workbench_http as fixtures


class DevelopmentBudgetHTTPTest(unittest.TestCase):
    setUp = fixtures.DevWorkbenchHTTPTest.setUp
    tearDown = fixtures.DevWorkbenchHTTPTest.tearDown
    request = fixtures.DevWorkbenchHTTPTest.request
    expect = fixtures.DevWorkbenchHTTPTest.expect
    native = fixtures.DevWorkbenchHTTPTest.native

    def test_native_budget_is_separate_and_reversible(self):
        initial = self.expect('GET', '/api/dev/workspace')
        policy = self.app.budget.config()
        self.assertFalse(initial['development_budget']['relaxed'])
        self.assertEqual(policy['max_output_tokens'], initial['development_budget']['effective_output_tokens'])
        self.assertTrue(all(row['budget_scope'] == 'development' for row in initial['development_balances']))
        self.native('development-budget', {'relaxed': True, 'max_output_tokens': 32768})
        active = self.expect('GET', '/api/dev/workspace')
        self.assertTrue(active['development_budget']['relaxed'])
        self.assertEqual(32768, active['development_budget']['effective_output_tokens'])
        self.assertTrue(all(row['daily_remaining'] is None and row['calls_remaining'] is None
                            and row['lifetime_remaining'] is None for row in active['development_balances']))
        self.assertEqual(policy, self.app.budget.config())
        self.assertNotIn(self.secret, json.dumps(active))
        self.native('development-budget', {'relaxed': False, 'max_output_tokens': 32768})
        final = self.expect('GET', '/api/dev/workspace')
        self.assertFalse(final['development_budget']['relaxed'])
        self.assertEqual(policy['max_output_tokens'], final['development_budget']['effective_output_tokens'])
        self.assertEqual(2, final['development_budget']['revision'])
        self.assertEqual(policy, self.app.budget.config())

    def test_browser_cannot_approve_or_expand_scope(self):
        payload = {'relaxed': True, 'max_output_tokens': 32768}
        for extra in ({'allow_source': True}, {'endpoints': []}, {'revision': 9}, {'residents': ['all']}):
            self.expect('POST', '/api/owner/intents', {'action': 'development-budget', 'payload': {**payload, **extra}}, 400)
        for incomplete in ({}, {'relaxed': True}, {'max_output_tokens': 32768}):
            self.expect('POST', '/api/owner/intents', {'action': 'development-budget', 'payload': incomplete}, 400)
        intent = self.expect('POST', '/api/owner/intents', {'action': 'development-budget', 'payload': payload})
        preview = self.expect('GET', '/internal/owner/intents/' + intent['id'], headers={'X-Town-Owner': self.secret})
        confirm = {'id': intent['id'], 'digest': preview['digest'], 'approved': True}
        self.expect('POST', '/internal/owner/confirm', confirm, 403)
        self.expect('POST', '/internal/owner/confirm', confirm, 403, {'X-Town-Owner': 'wrong'})
        self.assertFalse(self.app.budget.development_config()['relaxed'])
        self.expect('POST', '/internal/owner/confirm', {**confirm, 'approved': False}, headers={'X-Town-Owner': self.secret})
        self.assertFalse(self.app.budget.development_config()['relaxed'])
        self.expect('POST', '/internal/owner/confirm', confirm, 409, {'X-Town-Owner': self.secret})

    def test_concurrent_intent_cannot_overwrite_new_budget(self):
        intent = self.expect('POST', '/api/owner/intents', {'action': 'development-budget',
            'payload': {'relaxed': True, 'max_output_tokens': 8192}})
        preview = self.expect('GET', '/internal/owner/intents/' + intent['id'], headers={'X-Town-Owner': self.secret})
        self.native('development-budget', {'relaxed': True, 'max_output_tokens': 32768})
        self.expect('POST', '/internal/owner/confirm', {'id': intent['id'], 'digest': preview['digest'], 'approved': True},
                    409, {'X-Town-Owner': self.secret})
        self.assertEqual(32768, self.app.budget.development_config()['max_output_tokens'])


if __name__ == '__main__':
    unittest.main()
