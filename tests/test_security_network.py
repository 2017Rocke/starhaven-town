"""The request boundary must consult the local office before opening a socket."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provider_bridge import ProviderBridge
from scout_collect import PublicGitHubClient, ScoutError
from town_security import SecurityOffice


class Credentials:
    storage = 'memory'
    supports_persistence = False
    def get(self, url):
        return 'synthetic-private-authentication'


class Office:
    def __init__(self, allowed=True, broken=False):
        self.allowed, self.broken = allowed, broken
        self.checks, self.results, self.external = [], [], []
    def network_check(self, **data):
        if self.broken:
            raise OSError('unavailable')
        self.checks.append(data)
        return {'allowed': self.allowed and not data.get('credential_in_payload'),
                'check_id': len(self.checks), 'reason': '等待复核'}
    def network_result(self, check, **result):
        self.results.append((check, result))
    def external_notice(self, **data):
        self.external.append(data)


class Response(io.BytesIO):
    headers = {}
    status = 200
    def geturl(self):
        return 'https://api.github.com/repos/example/project'


class NetworkBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.opener = Mock()
        self.opener.open.return_value = io.BytesIO(json.dumps({'choices': [{'message': {'content': '回复'}}]}).encode())
        self.bridge = ProviderBridge(self.root/'runtime', self.root/'data', Credentials(), lambda:self.opener)
        self.bridge.save_settings({'mode':'api','base_url':'https://models.example/v1','model':'example'})
    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()
    def test_model_authorized_request_has_identity_and_never_audits_auth_header(self):
        office = Office()
        self.bridge.set_security_office(office)
        self.assertEqual(self.bridge.generate_for_agent('普通消息', threading.Event(), 'resident-officer'), '回复')
        self.assertEqual(office.checks[0]['agent_id'], 'resident-officer')
        self.assertNotIn('synthetic-private-authentication', json.dumps(office.checks))
        self.assertEqual(office.results[0][1]['ok'], True)
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Authorization'), 'Bearer synthetic-private-authentication')
    def test_model_block_and_audit_failure_never_open_connection(self):
        for office in (Office(allowed=False), Office(broken=True)):
            self.bridge.set_security_office(office)
            with self.assertRaises(RuntimeError):
                self.bridge.generate('普通消息', threading.Event())
        self.opener.open.assert_not_called()
    def test_invalid_audit_result_cannot_be_truthy_approval(self):
        for invalid in (None, [], {'allowed':'false'}):
            office = Office()
            office.network_check = Mock(return_value=invalid)
            self.bridge.set_security_office(office)
            with self.assertRaises(RuntimeError):
                self.bridge.generate('普通消息', threading.Event())
            opener = Mock()
            with self.assertRaises(ScoutError):
                PublicGitHubClient(opener=opener, security=office).fetch('https://api.github.com/repos/example/project')
            opener.open.assert_not_called()
        self.opener.open.assert_not_called()
    def test_result_record_failure_is_safe_and_does_not_resend(self):
        office = Office()
        office.network_result = Mock(side_effect=OSError('synthetic-private-log-error'))
        self.bridge.set_security_office(office)
        with self.assertRaises(RuntimeError) as error:
            self.bridge.generate('普通消息', threading.Event())
        self.assertNotIn('synthetic-private-log-error', str(error.exception))
        self.assertEqual(self.opener.open.call_count, 1)
        opener = Mock()
        opener.open.return_value = Response(b'{}')
        with self.assertRaises(ScoutError) as error:
            PublicGitHubClient(opener=opener, security=office).fetch('https://api.github.com/repos/example/project')
        self.assertNotIn('synthetic-private-log-error', str(error.exception))
        self.assertEqual(opener.open.call_count, 1)
    def test_external_audit_failure_does_not_start_codex(self):
        office = Office()
        office.external_notice = Mock(side_effect=OSError('synthetic-private-log-error'))
        self.bridge.set_security_office(office)
        self.bridge.save_settings({'mode':'codex','base_url':'','model':''})
        self.bridge.codex.generate = Mock()
        with self.assertRaises(RuntimeError):
            self.bridge.generate('普通消息', threading.Event())
        self.bridge.codex.generate.assert_not_called()
    def test_custom_authentication_in_prompt_is_blocked_even_without_token_prefix(self):
        office = Office()
        self.bridge.set_security_office(office)
        with self.assertRaises(RuntimeError):
            self.bridge.generate('请查看 synthetic-private-authentication', threading.Event())
        self.assertTrue(office.checks[0]['credential_in_payload'])
        self.opener.open.assert_not_called()
    def test_model_field_is_part_of_outbound_payload_inspection(self):
        office = Office()
        self.bridge.set_security_office(office)
        self.bridge.save_settings({'mode':'api','base_url':'https://models.example/v1',
                                   'model':'synthetic-private-authentication'})
        with self.assertRaises(RuntimeError):
            self.bridge.generate('普通消息', threading.Event())
        self.assertTrue(office.checks[0]['credential_in_payload'])
        self.opener.open.assert_not_called()
    def test_cancel_during_audit_does_not_send_after_audit_finishes(self):
        for kind in ('model', 'scout'):
            with self.subTest(kind=kind):
                entered, release, audited, cancelled, done = (threading.Event() for _ in range(5))
                class SlowOffice(Office):
                    def network_check(self, **data):
                        entered.set()
                        release.wait(3)
                        return super().network_check(**data)
                    def network_result(self, check, **result):
                        super().network_result(check, **result)
                        audited.set()
                office, opener, errors = SlowOffice(), Mock(), []
                if kind == 'model':
                    self.bridge.set_security_office(office)
                    action = lambda:self.bridge.generate('普通消息', cancelled)
                    actual_opener = self.opener
                else:
                    client = PublicGitHubClient(opener=opener, security=office)
                    action = lambda:client.fetch('https://api.github.com/repos/example/project', cancel_event=cancelled)
                    actual_opener = opener
                def run():
                    try:
                        action()
                    except RuntimeError as exc:
                        errors.append(exc)
                    finally:
                        done.set()
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    cancelled.set()
                    self.assertTrue(done.wait(2))
                finally:
                    release.set()
                    worker.join(3)
                self.assertTrue(audited.wait(2))
                self.assertTrue(errors)
                actual_opener.open.assert_not_called()
    def test_codex_records_external_coverage_instead_of_false_network_clearance(self):
        office = Office()
        self.bridge.set_security_office(office)
        self.bridge.save_settings({'mode':'codex','base_url':'','model':''})
        self.bridge.codex.generate = Mock(return_value='外部回复')
        self.assertEqual(self.bridge.generate_for_agent('普通消息', threading.Event(), 'scout'), '外部回复')
        self.assertEqual(office.external[0]['agent_id'], 'scout')
        self.assertEqual(office.checks, [])
    def test_scout_success_and_blocked_requests_are_audited(self):
        office = Office()
        opener = Mock()
        opener.open.return_value = Response(b'{}')
        client = PublicGitHubClient(opener=opener, security=office)
        self.assertEqual(client.fetch('https://api.github.com/repos/example/project'), b'{}')
        self.assertTrue(office.results[0][1]['ok'])
        self.assertEqual(office.checks[0]['agent_id'], 'scout')
        office.allowed = False
        with self.assertRaises(ScoutError):
            client.fetch('https://api.github.com/repos/example/project')
        self.assertEqual(opener.open.call_count, 1)
    def test_scout_audit_failure_and_untrusted_destination_open_no_connection(self):
        office = Office(broken=True)
        opener = Mock()
        client = PublicGitHubClient(opener=opener, security=office)
        with self.assertRaises(ScoutError):
            client.fetch('https://api.github.com/repos/example/project')
        office.broken = False
        with self.assertRaises(ScoutError):
            client.fetch('https://untrusted.example/collect')
        self.assertEqual(len(office.checks), 1)
        opener.open.assert_not_called()
    def test_real_office_records_both_boundaries_without_saving_prompts_or_headers(self):
        office = SecurityOffice(self.root/'data', start_scheduler=False)
        try:
            self.bridge.set_security_office(office)
            self.assertEqual(self.bridge.generate_for_agent('private-conversation-marker', threading.Event(), 'resident-officer'), '回复')
            opener = Mock()
            opener.open.return_value = Response(b'{}')
            PublicGitHubClient(opener=opener, security=office).fetch('https://api.github.com/repos/example/project')
            with self.assertRaises(RuntimeError):
                self.bridge.generate('synthetic-private-authentication', threading.Event())
            records = office.records('inspection')['records']
            encoded = json.dumps(records)
            self.assertNotIn('private-conversation-marker', encoded)
            self.assertNotIn('synthetic-private-authentication', encoded)
            self.assertEqual(sum(r['kind']=='network_result' for r in records), 2)
            self.assertEqual(sum(r['status']=='blocked' for r in records), 1)
            self.assertTrue(office.integrity()['valid'])
        finally:
            office.close()


if __name__ == '__main__':
    unittest.main()
