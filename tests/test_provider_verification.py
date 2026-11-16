"""Local verification receipts and maintenance entry point; synthetic data only."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from provider_bridge import NoRedirect, ProviderBridge
from provider_verification import (EXTENDED_INPUT_LIMIT, EXTENDED_OUTPUT_LIMIT, EXTENDED_PROFILE,
    FILENAME, INPUT_LIMIT, MAX_FILE, OUTPUT_LIMIT, PROFILE, TTL,
    LocalBudgetProfiles, VerificationError, canonical, fingerprint, observation, probe_requests,
    run_verification, timestamp, validate_evidence)
from test_provider_bridge import MemoryCredentials, MockOpener
from test_provider_response import ResponseBudget
import verify_model_budget


CONFIG = {'mode': 'api', 'protocol': 'anthropic', 'auth_type': 'api_key',
          'base_url': 'https://synthetic-messages.example/private', 'model': 'synthetic-model'}
KEY = 'SYNTHETIC_VERIFICATION_CREDENTIAL'
BODY_MARKER = 'SYNTHETIC_RESPONSE_BODY_NOT_FOR_STORAGE'
THINKING_MARKER = 'SYNTHETIC_THINKING_NOT_FOR_STORAGE'
CASES = ('cap16', 'cap128', 'json4096', 'input65536')
EXTENDED = dict(output_limit=EXTENDED_OUTPUT_LIMIT, input_limit=EXTENDED_INPUT_LIMIT)
EXTENDED_CASES = ('cap16', 'cap128', 'json32768', 'input262144')


def response_for(case, cap):
    text = json.dumps({'summary': BODY_MARKER, 'scope': ['添加事项'],
                       'acceptance_criteria': ['标题不能为空'], 'questions': []}, ensure_ascii=False)
    if case not in ('json4096', 'json32768'):
        text = BODY_MARKER
    output = cap if case in ('cap16', 'cap128') else 12
    return {'type': 'message', 'role': 'assistant',
            'content': [{'type': 'thinking', 'thinking': THINKING_MARKER}, {'type': 'text', 'text': text}],
            'stop_reason': 'max_tokens' if case in ('cap16', 'cap128') else 'end_turn',
            'usage': {'input_tokens': 11, 'output_tokens': output,
                      'output_tokens_details': {'thinking_tokens': 2}}}


def evidence(config=None, **limits):
    result = []
    for case, body in probe_requests(config or CONFIG, **limits):
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        result.append(observation(case, payload, body['max_tokens'], response_for(case, body['max_tokens']), **limits))
    return result


class SyntheticResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status = status


class VerificationOpener:
    def __init__(self, transform=None, error=None):
        self.requests = []
        self.timeouts = []
        self.transform = transform
        self.error = error

    def open(self, request, timeout):
        index = len(self.requests)
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        body = json.loads(request.data)
        case = 'json%d' % body['max_tokens'] if index == 2 else CASES[index]
        value = response_for(case, body['max_tokens'])
        if self.transform:
            value = self.transform(index, request, value)
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        return SyntheticResponse(raw)


class ProbeValidationTest(unittest.TestCase):
    def test_legacy_probes_preserve_existing_receipt_request_hashes(self):
        expected = (
            ('cap16', 315, '6ef4ddff89925620b7bafac7f841a5bf696c5657b619cd1c430579d2671a4ab5'),
            ('cap128', 316, 'ae0f3b3458ec494279852dff4e67126a5fc297ef1da9d0da6281511b5de38c76'),
            ('json4096', 544, '0db8902c7031934119d3539f74461458b791f6ca930bad1ff8be008db69b50cd'),
            ('input65536', 65536, '6e99c3faf3a71384ae902e446bcf2767abadf178740d14a4b54f2b83d5037a8a'),
        )
        actual = []
        for case, body in probe_requests(CONFIG):
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            actual.append((case, len(payload), hashlib.sha256(payload).hexdigest()))
        self.assertEqual(tuple(actual), expected)

    def test_extended_probes_keep_four_short_outputs_and_exact_input_envelope(self):
        opener = VerificationOpener()
        records = run_verification(CONFIG, KEY, opener=opener, **EXTENDED)
        self.assertEqual(tuple(record['case'] for record in records), EXTENDED_CASES)
        self.assertEqual([record['max_tokens'] for record in records], [16, 128, 32768, 128])
        self.assertEqual(records[-1]['request_bytes'], 262144)
        self.assertEqual(opener.timeouts, [120] * 4)
        json_probe = json.loads(opener.requests[2].data)
        self.assertIn('简短即可', json_probe['messages'][0]['content'])
        self.assertLess(len(opener.requests[2].data), 1000)
        validate_evidence(CONFIG, records, **EXTENDED)
        with self.assertRaises(VerificationError):
            validate_evidence(CONFIG, records)
        with self.assertRaises(VerificationError):
            validate_evidence(CONFIG, evidence(), **EXTENDED)

    def test_invalid_or_mixed_limits_fail_before_opener_creation_or_dispatch(self):
        invalid = ((32768, 65536), (4096, 262144), (8192, 65536), (4096, 131072),
                   (32768.0, 262144), (4096, 65536.0), (True, 65536), ('4096', 65536))
        for output_limit, input_limit in invalid:
            with self.subTest(output_limit=output_limit, input_limit=input_limit):
                limits = dict(output_limit=output_limit, input_limit=input_limit)
                with patch('provider_verification.urllib.request.build_opener') as build:
                    with self.assertRaises(VerificationError):
                        run_verification(CONFIG, KEY, **limits)
                    build.assert_not_called()
                with self.assertRaises(VerificationError):
                    list(probe_requests(CONFIG, **limits))

    def test_fixed_probes_use_only_synthetic_material_and_exact_byte_envelope(self):
        config = dict(CONFIG, requirements='DO_NOT_SEND_REAL_REQUIREMENTS', api_key=KEY,
                      source_path='/synthetic/DO_NOT_READ_SOURCE')
        probes = list(probe_requests(config))
        self.assertEqual([case for case, body in probes], list(CASES))
        self.assertEqual([body['max_tokens'] for case, body in probes], [16, 128, 4096, 128])
        for case, body in probes:
            encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.assertLessEqual(len(encoded), INPUT_LIMIT)
            self.assertNotIn(b'DO_NOT_SEND', encoded)
            self.assertNotIn(b'DO_NOT_READ', encoded)
            self.assertNotIn(KEY.encode(), encoded)
            self.assertNotIn('tools', body)
            self.assertFalse(body['stream'])
        self.assertEqual(len(json.dumps(probes[-1][1], ensure_ascii=False).encode('utf-8')), INPUT_LIMIT)

    def test_run_uses_exact_identity_one_auth_header_and_persists_only_metadata(self):
        for auth in ('api_key', 'bearer'):
            with self.subTest(auth_type=auth):
                opener, progress = VerificationOpener(), []
                records = run_verification(dict(CONFIG, auth_type=auth), KEY, progress.append, opener)
                self.assertEqual(len(opener.requests), 4)
                self.assertEqual(opener.timeouts, [120] * 4)
                for index, request in enumerate(opener.requests):
                    headers = {name.lower(): value for name, value in request.header_items()}
                    self.assertEqual(request.full_url, CONFIG['base_url'] + '/v1/messages')
                    self.assertEqual(headers['anthropic-version'], '2023-06-01')
                    self.assertEqual(headers.get('x-api-key'), KEY if auth == 'api_key' else None)
                    self.assertEqual(headers.get('authorization'), 'Bearer ' + KEY if auth == 'bearer' else None)
                    self.assertEqual(records[index]['request_sha256'], hashlib.sha256(request.data).hexdigest())
                    expected = response_for(CASES[index], json.loads(request.data)['max_tokens'])
                    self.assertEqual(records[index]['response_sha256'], fingerprint(expected))
                persisted = json.dumps({'observations': records, 'progress': progress})
                for private in (KEY, BODY_MARKER, THINKING_MARKER):
                    self.assertNotIn(private, persisted)

    def test_redirect_has_no_retry_or_credential_forwarding_and_no_raw_error(self):
        error = urllib.error.HTTPError('https://synthetic.invalid/' + KEY, 307, KEY,
                                      {'Location': 'https://other.invalid/' + KEY}, io.BytesIO(KEY.encode()))
        opener = VerificationOpener(error=error)
        with patch('provider_verification.urllib.request.build_opener', return_value=opener) as build:
            with self.assertRaises(VerificationError) as caught:
                run_verification(CONFIG, KEY)
        self.assertEqual(len(opener.requests), 1)
        self.assertIn('307', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        handlers = build.call_args.args
        redirect = next(handler for handler in handlers if isinstance(handler, NoRedirect))
        self.assertIsNone(redirect.redirect_request(opener.requests[0], None, 307, KEY, {}, 'https://other.invalid'))
        self.assertEqual(next(handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)).proxies, {})

    def test_http_protocol_failures_are_fixed_errors_without_upstream_content(self):
        for error in (http.client.BadStatusLine(KEY), http.client.IncompleteRead(KEY.encode(), 100)):
            with self.subTest(error=type(error).__name__):
                opener = VerificationOpener(error=error)
                with self.assertRaises(VerificationError) as caught:
                    run_verification(CONFIG, KEY, opener=opener)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(len(opener.requests), 1)

    def test_invalid_credential_and_protocol_are_rejected_before_dispatch(self):
        for credential in ('', KEY + '\n', None, 'x' * 8193):
            with self.subTest(credential_type=type(credential).__name__):
                opener = VerificationOpener()
                with self.assertRaises(VerificationError):
                    run_verification(CONFIG, credential, opener=opener)
                self.assertEqual(opener.requests, [])
        for config in (dict(CONFIG, protocol='openai'), dict(CONFIG, mode='codex')):
            opener = VerificationOpener()
            with self.assertRaises(VerificationError):
                run_verification(config, KEY, opener=opener)
            self.assertEqual(opener.requests, [])

    def test_malformed_and_oversized_response_never_produce_receipt(self):
        for value in (b'not json', b'\xff', b'x' * 1000001, [], {'content': []},
                      {'content': [None], 'usage': {'input_tokens': 1, 'output_tokens': 1}}):
            with self.subTest(response_type=type(value).__name__, size=len(value)):
                opener = VerificationOpener(transform=lambda index, request, response: value)
                with self.assertRaises(VerificationError):
                    run_verification(CONFIG, KEY, opener=opener)
                self.assertEqual(len(opener.requests), 1)

    def test_usage_bounds_cache_tokens_zero_and_invalid_values_are_rejected(self):
        def mutations(request):
            cap = json.loads(request.data)['max_tokens']
            return ({'input_tokens': len(request.data) + 513, 'output_tokens': cap},
                    {'input_tokens': 1, 'output_tokens': cap + 1},
                    {'input_tokens': len(request.data), 'cache_read_input_tokens': 513, 'output_tokens': cap},
                    {'input_tokens': 0, 'output_tokens': cap},
                    {'input_tokens': True, 'output_tokens': cap},
                    {'input_tokens': 1, 'output_tokens': -1}, None, {})
        for index in range(8):
            with self.subTest(mutation=index):
                def changed(case, request, value):
                    value['usage'] = mutations(request)[index]
                    return value
                with self.assertRaises(VerificationError):
                    run_verification(CONFIG, KEY, opener=VerificationOpener(transform=changed))
        def zero_json_output(index, request, value):
            if index == 2:
                value['content'] = [block for block in value['content'] if block['type'] == 'text']
                value['usage'] = {'input_tokens': 11, 'output_tokens': 0}
            return value
        with self.assertRaises(VerificationError):
            run_verification(CONFIG, KEY, opener=VerificationOpener(transform=zero_json_output))

    def test_exact_input_allowance_and_output_cap_are_accepted(self):
        def at_boundary(index, request, value):
            value['usage']['input_tokens'] = len(request.data) + 512
            return value
        opener = VerificationOpener(transform=at_boundary)
        records = run_verification(CONFIG, KEY, opener=opener)
        for record, request in zip(records, opener.requests):
            self.assertEqual(record['usage']['prompt_tokens'], len(request.data) + 512)
        self.assertEqual(records[0]['usage']['completion_tokens'], 16)
        self.assertEqual(records[1]['usage']['completion_tokens'], 128)

    def test_observation_requires_an_integer_success_status(self):
        case, body = next(probe_requests(CONFIG))
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        for status in (200.0, True, '200', 201, None):
            with self.subTest(status=status), self.assertRaises(VerificationError):
                observation(case, payload, body['max_tokens'], response_for(case, body['max_tokens']), status)

    def test_json_probe_requires_real_work_record_field_types(self):
        base = {'summary': 'synthetic', 'scope': ['item'], 'acceptance_criteria': ['expected'], 'questions': []}
        invalid = ['not json', '[]', json.dumps({}), json.dumps(dict(base, scope=[123])),
                   json.dumps(dict(base, acceptance_criteria=[None])), json.dumps(dict(base, questions=[123])),
                   json.dumps(dict(base, questions=[{'text': 'need detail', 'blocking': 'yes'}]))]
        for text in invalid:
            with self.subTest(json_text=text):
                def changed(index, request, value):
                    if index == 2:
                        value['content'] = [{'type': 'text', 'text': text}]
                    return value
                with self.assertRaises(VerificationError):
                    run_verification(CONFIG, KEY, opener=VerificationOpener(transform=changed))

    def test_request_fingerprint_case_order_and_truncation_evidence_are_mandatory(self):
        original = evidence()
        changes = []
        changed = deepcopy(original); changed[0]['request_sha256'] = '0' * 64; changes.append(changed)
        changed = deepcopy(original); changed.reverse(); changes.append(changed)
        changed = deepcopy(original); changed[0]['stop_reason'] = 'end_turn'; changes.append(changed)
        changed = deepcopy(original); changed[0]['usage'] = {'prompt_tokens': 11, 'completion_tokens': 15, 'total_tokens': 26}; changes.append(changed)
        changed = deepcopy(original); changed[2]['json_schema_valid'] = False; changes.append(changed)
        changes.extend((original[:3], original + original[:1]))
        for records in changes:
            with self.subTest(records=len(records)):
                with self.assertRaises(VerificationError):
                    validate_evidence(CONFIG, records)
        with self.assertRaises(VerificationError):
            validate_evidence(dict(CONFIG, model='different-model'), original)


class LocalReceiptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = LocalBudgetProfiles(self.root)
        self.checked = 1700000000
        self.records = evidence()

    def record(self):
        return self.profiles.record(CONFIG, self.records, now=self.checked)

    def write_file(self, value):
        self.profiles.path.write_bytes(canonical(value))

    def test_receipt_has_exact_identity_and_canonical_equivalents_only(self):
        receipt = self.record()
        self.assertNotIn('limits', receipt)
        capability = self.profiles.capability(CONFIG, now=self.checked + 1)
        self.assertEqual(capability['profile'], PROFILE)
        self.assertEqual(capability['verification_id'], receipt['digest'])
        self.assertEqual((capability['max_output_tokens'], capability['max_request_bytes']), (OUTPUT_LIMIT, INPUT_LIMIT))
        self.assertEqual(capability['socket_timeout'], 120)
        for config in (dict(CONFIG, base_url=CONFIG['base_url'] + '/v1/messages'),
                       {key: value for key, value in CONFIG.items() if key != 'auth_type'}):
            self.assertTrue(self.profiles.capability(config, now=self.checked + 1)['enforce_supported'])
        for key, value in (('base_url', 'https://other.example/private'), ('model', 'other-model'),
                           ('auth_type', 'bearer'), ('protocol', 'openai'), ('mode', 'codex')):
            with self.subTest(changed=key):
                self.assertIsNone(self.profiles.capability(dict(CONFIG, **{key: value}), now=self.checked + 1))
        self.assertIsNone(LocalBudgetProfiles(self.root / 'other-machine').capability(CONFIG, now=self.checked + 1))

    def test_extended_receipt_records_exact_limits_and_replaces_only_matching_identity(self):
        other = dict(CONFIG, model='another-synthetic-model')
        self.profiles.record(other, evidence(other), now=self.checked)
        receipt = self.profiles.record(CONFIG, evidence(**EXTENDED), now=self.checked, **EXTENDED)
        expected = dict(max_output_tokens=32768, max_request_bytes=262144, socket_timeout=600)
        self.assertEqual(receipt['profile'], EXTENDED_PROFILE)
        self.assertEqual(receipt['limits'], expected)
        self.assertEqual(receipt['digest'], fingerprint({k: v for k, v in receipt.items() if k != 'digest'}))
        capability = self.profiles.capability(CONFIG, now=self.checked + 1)
        self.assertEqual(capability['profile'], EXTENDED_PROFILE)
        self.assertEqual({key: capability[key] for key in expected}, expected)
        self.assertEqual(self.profiles.capability(other, now=self.checked + 1)['max_output_tokens'], 4096)
        self.record()
        self.assertEqual(self.profiles.capability(CONFIG, now=self.checked + 1)['max_output_tokens'], 4096)
        self.assertEqual(len(json.loads(self.profiles.path.read_text())['entries']), 2)

    def test_extended_limits_are_digest_bound_and_strict_even_after_rehashing(self):
        self.profiles.record(CONFIG, evidence(**EXTENDED), now=self.checked, **EXTENDED)
        original = json.loads(self.profiles.path.read_text())
        mutations = []
        for key, changed in (('max_output_tokens', 4096), ('max_request_bytes', 65536),
                             ('socket_timeout', 120), ('max_output_tokens', 32768.0)):
            value = deepcopy(original)
            value['entries'][0]['limits'][key] = changed
            mutations.append(value)
        value = deepcopy(original); del value['entries'][0]['limits']; mutations.append(value)
        value = deepcopy(original); value['entries'][0]['limits']['extra'] = 1; mutations.append(value)
        value = deepcopy(original); value['entries'][0]['profile'] = PROFILE; mutations.append(value)
        value = deepcopy(original); value['entries'][0]['observations'] = self.records; mutations.append(value)
        for value in mutations:
            with self.subTest(row=value['entries'][0].get('limits')):
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))
                row = value['entries'][0]
                row['digest'] = fingerprint({k: v for k, v in row.items() if k != 'digest'})
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))

    def test_legacy_receipt_cannot_be_expanded_by_relabeling_or_adding_limits(self):
        self.record()
        original = json.loads(self.profiles.path.read_text())
        for profile in (PROFILE, EXTENDED_PROFILE):
            with self.subTest(profile=profile):
                value = deepcopy(original)
                row = value['entries'][0]
                row.update(profile=profile, limits=dict(max_output_tokens=32768, max_request_bytes=262144, socket_timeout=600))
                row['digest'] = fingerprint({k: v for k, v in row.items() if k != 'digest'})
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))
        with self.assertRaises(VerificationError):
            self.profiles.record(CONFIG, self.records, now=self.checked, **EXTENDED)
        with self.assertRaises(VerificationError):
            self.profiles.record(CONFIG, evidence(**EXTENDED), now=self.checked)

    def test_expiry_window_rejects_future_receipts_and_exact_expiration(self):
        self.record()
        self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked - 1))
        self.assertIsNotNone(self.profiles.capability(CONFIG, now=self.checked))
        self.assertIsNotNone(self.profiles.capability(CONFIG, now=self.checked + TTL - 1))
        self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + TTL))

    def test_fractional_clock_tick_matches_persisted_microsecond_precision(self):
        checked = 1794837848.1234567
        self.profiles.record(CONFIG, self.records, now=checked)
        self.assertIsNotNone(self.profiles.capability(CONFIG, now=checked))
        self.assertIsNone(self.profiles.capability(CONFIG, now=checked - 0.001))
        self.assertIsNotNone(self.profiles.capability(CONFIG, now=checked + TTL - 0.001))
        self.assertIsNone(self.profiles.capability(CONFIG, now=checked + TTL))

    def test_malformed_or_timezone_missing_timestamps_fail_closed_even_with_fresh_digest(self):
        self.record()
        original = json.loads(self.profiles.path.read_text())
        pairs = [(value, original['entries'][0]['expires_at']) for value in (None, True, 1, [], {}, 'not-a-date')]
        pairs.extend(((timestamp(self.checked).replace('+00:00', ''), timestamp(self.checked + TTL).replace('+00:00', '')),
                      ('2023-11-14', '2023-12-14')))
        for start, expiry in pairs:
            with self.subTest(start=start, expiry=expiry):
                value = deepcopy(original)
                row = value['entries'][0]
                row.update(verified_at=start, expires_at=expiry)
                row['digest'] = fingerprint({key: item for key, item in row.items() if key != 'digest'})
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 86400))

    def test_receipt_stores_hashes_counts_and_no_prompt_response_or_key(self):
        self.record()
        text = self.profiles.path.read_text(encoding='utf-8')
        for private in (KEY, BODY_MARKER, THINKING_MARKER, '从1开始逐行输出一千个整数', '材料 alpha'):
            self.assertNotIn(private, text)
        self.assertFalse(list(self.root.glob('.model-budget-verifications-*.tmp')))
        if os.name != 'nt':
            self.assertEqual(self.profiles.path.stat().st_mode & 0o777, 0o600)

    def test_revoke_is_exact_idempotent_and_cannot_reenable_receipt(self):
        self.record()
        self.profiles.revoke(dict(CONFIG, auth_type='bearer'))
        self.assertIsNotNone(self.profiles.capability(CONFIG, now=self.checked + 1))
        self.profiles.revoke(CONFIG, reason=KEY)
        self.profiles.revoke(CONFIG)
        self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))
        data = json.loads(self.profiles.path.read_text())
        self.assertEqual(data['entries'][0]['status'], 'revoked')
        self.assertNotIn(KEY, self.profiles.path.read_text())

    def test_tampering_and_duplicate_matching_receipts_fail_closed(self):
        receipt = self.record()
        original = json.loads(self.profiles.path.read_text())
        changes = []
        for key, value in (('profile', 'other'), ('expires_at', timestamp(self.checked + TTL + 1)),
                           ('verified_at', 'not-a-time'), ('status', 'untrusted'), ('digest', '0' * 64)):
            changed = deepcopy(original); changed['entries'][0][key] = value; changes.append(changed)
        changed = deepcopy(original); changed['entries'][0]['unrecognised'] = True; changes.append(changed)
        changed = deepcopy(original); changed['entries'].append(receipt); changes.append(changed)
        for value in changes:
            with self.subTest(entry_fields=list(value['entries'][0])):
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))

    def test_recomputed_digest_does_not_bypass_evidence_or_lifetime_checks(self):
        self.record()
        original = json.loads(self.profiles.path.read_text())
        for changed_field in ('request_hash', 'json_schema', 'expiry'):
            with self.subTest(changed_field=changed_field):
                value = deepcopy(original)
                row = value['entries'][0]
                if changed_field == 'request_hash':
                    row['observations'][0]['request_sha256'] = '0' * 64
                elif changed_field == 'json_schema':
                    row['observations'][2]['json_schema_valid'] = False
                else:
                    row['expires_at'] = timestamp(self.checked + TTL + 100)
                row['digest'] = fingerprint({key: item for key, item in row.items() if key != 'digest'})
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))

    def test_malformed_oversized_and_wrong_schema_files_fail_closed(self):
        self.record()
        original = json.loads(self.profiles.path.read_text())
        for value in ({}, [], {'schema': 1, 'entries': 'wrong'}, {'schema': 1, 'entries': [None]},
                      dict(original, schema=True), dict(original, schema=1.0),
                      dict(original, entries=original['entries'] * 9)):
            with self.subTest(value_type=type(value).__name__):
                self.write_file(value)
                self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))
        for raw in (b'{', b'\xff', b'x' * (MAX_FILE + 1)):
            self.profiles.path.write_bytes(raw)
            self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))

    def test_symlink_receipt_never_confers_capability_or_accepts_writes(self):
        self.record()
        backing = self.root / 'backing.json'
        self.profiles.path.rename(backing)
        try:
            self.profiles.path.symlink_to(backing)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks not available for this test account')
        self.assertIsNone(self.profiles.capability(CONFIG, now=self.checked + 1))
        with self.assertRaises(VerificationError):
            self.record()

    def test_resolved_capability_keeps_static_profiles_and_local_identity_separate(self):
        bridge = ProviderBridge(self.root / 'runtime', self.root, MemoryCredentials())
        self.addCleanup(bridge.close)
        bridge.save_settings(CONFIG)
        with patch('provider_verification.time.time', return_value=self.checked + 1):
            self.assertFalse(bridge.settings()['budget_capability']['enforce_supported'])
            self.record()
            self.assertTrue(bridge.settings()['budget_capability']['local_verification'])
            self.assertFalse(ProviderBridge.budget_capability(CONFIG)['enforce_supported'])
            self.assertFalse(bridge.resolved_budget_capability(dict(CONFIG, auth_type='bearer'))['enforce_supported'])
            builtin = {'mode': 'api', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}
            self.profiles.path.write_text('{malformed')
            self.assertTrue(bridge.resolved_budget_capability(builtin)['enforce_supported'])
            self.assertFalse(bridge.resolved_budget_capability(CONFIG)['enforce_supported'])

    def test_runtime_budget_violation_revokes_local_receipt_without_returning_text(self):
        self.profiles.record(CONFIG, self.records)
        response = response_for('json4096', 64)
        response.pop('usage')
        opener = MockOpener(body=json.dumps(response).encode())
        bridge = ProviderBridge(self.root / 'runtime', self.root, MemoryCredentials(), lambda: opener)
        self.addCleanup(bridge.close)
        bridge.save_settings(CONFIG)
        budget = ResponseBudget(enforce=True)
        bridge.set_budget(budget)
        self.assertTrue(bridge.settings()['budget_capability']['enforce_supported'])
        with self.assertRaises(RuntimeError):
            bridge.generate_result('SYNTHETIC_USER_TEXT', threading.Event(), 'synthetic-agent', {'controlled': True})
        self.assertEqual(budget.settled[-1]['budget_violation'], 'missing_usage')
        self.assertNotIn('reply', budget.settled[-1])
        self.assertFalse(bridge.settings()['budget_capability']['enforce_supported'])


class VerificationCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'provider.json').write_text(json.dumps(CONFIG))
        self.profiles = LocalBudgetProfiles(self.root)
        self.records = evidence()

    def call(self, verify=False, extra=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = verify_model_budget.main(['--data-dir', str(self.root)] + (['--verify'] if verify else []) + list(extra))
        return code, out.getvalue(), err.getvalue()

    def test_read_only_mode_never_reads_credentials_or_sends_requests(self):
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification') as run:
            code, out, err = self.call()
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)['enforce_supported'])
        self.assertEqual(err, '')
        credentials.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.profiles.path.exists())

    def test_explicit_verification_records_metadata_and_revokes_previous_before_probe(self):
        self.profiles.record(CONFIG, evidence(**EXTENDED), **EXTENDED)
        observed_revocation = []
        def run(config, credential, progress, **limits):
            observed_revocation.append(self.profiles.capability(config))
            self.assertEqual(credential, KEY)
            self.assertEqual(limits, dict(output_limit=4096, input_limit=65536))
            return self.records
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification', side_effect=run):
            credentials.return_value.get.return_value = KEY
            code, out, err = self.call(True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(observed_revocation, [None])
        self.assertEqual(json.loads(out)['status'], 'verified')
        self.assertIsNotNone(self.profiles.capability(CONFIG))
        self.assertEqual(self.profiles.capability(CONFIG)['profile'], PROFILE)
        self.assertEqual(self.profiles.capability(CONFIG)['socket_timeout'], 120)
        for private in (KEY, BODY_MARKER, THINKING_MARKER):
            self.assertNotIn(private, out + self.profiles.path.read_text())

    def test_explicit_extended_verification_and_read_only_lookup_preserve_receipt_limits(self):
        records = evidence(**EXTENDED)
        def run(config, credential, progress, **limits):
            self.assertEqual(limits, EXTENDED)
            self.assertEqual(credential, KEY)
            return records
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification', side_effect=run):
            credentials.return_value.get.return_value = KEY
            code, out, err = self.call(True, ('--output-limit', '32768', '--input-limit', '262144'))
        self.assertEqual((code, err), (0, ''))
        capability = json.loads(out)['capability']
        self.assertEqual(capability['profile'], EXTENDED_PROFILE)
        self.assertEqual((capability['max_output_tokens'], capability['max_request_bytes'], capability['socket_timeout']),
                         (32768, 262144, 600))
        before = self.profiles.path.read_bytes()
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification') as run:
            code, out, err = self.call()
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), capability)
        self.assertEqual(self.profiles.path.read_bytes(), before)
        credentials.assert_not_called()
        run.assert_not_called()

    def test_invalid_limit_flags_leave_existing_receipt_and_credentials_untouched(self):
        self.profiles.record(CONFIG, self.records)
        before = self.profiles.path.read_bytes()
        invalid = (('--output-limit', '32768'), ('--input-limit', '262144'),
                   ('--output-limit', '8192'), ('--input-limit', '131072'),
                   ('--output-limit', '32768.0', '--input-limit', '262144'))
        for flags in invalid:
            with self.subTest(flags=flags):
                with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification') as run:
                    with self.assertRaises(SystemExit) as caught:
                        self.call(True, flags)
                self.assertEqual(caught.exception.code, 2)
                credentials.assert_not_called()
                run.assert_not_called()
                self.assertEqual(self.profiles.path.read_bytes(), before)

    def test_configuration_change_during_probe_cannot_issue_new_receipt(self):
        self.profiles.record(CONFIG, self.records)
        def run(config, credential, progress, **limits):
            (self.root / 'provider.json').write_text(json.dumps(dict(CONFIG, auth_type='bearer')))
            return self.records
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification', side_effect=run):
            credentials.return_value.get.return_value = KEY
            code, out, err = self.call(True)
        self.assertEqual(code, 1)
        self.assertIn('设置发生变化', err)
        self.assertIsNone(self.profiles.capability(CONFIG))
        self.assertNotIn(KEY, out + err)

    def test_byte_only_configuration_change_during_probe_also_requires_reverification(self):
        self.profiles.record(CONFIG, self.records)
        config_path = self.root / 'provider.json'
        original = config_path.read_bytes()
        def run(config, credential, progress, **limits):
            config_path.write_bytes(original + b'\n')
            self.assertEqual(json.loads(original), json.loads(config_path.read_bytes()))
            return self.records
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification', side_effect=run):
            credentials.return_value.get.return_value = KEY
            code, out, err = self.call(True)
        self.assertEqual(code, 1)
        self.assertIn('设置发生变化', err)
        self.assertIsNone(self.profiles.capability(CONFIG))

    def test_cli_protocol_failure_is_redacted_and_previous_profile_stays_revoked(self):
        self.profiles.record(CONFIG, self.records)
        with patch.object(verify_model_budget, 'CredentialStore') as credentials, patch.object(verify_model_budget, 'run_verification', side_effect=http.client.BadStatusLine(KEY)):
            credentials.return_value.get.return_value = KEY
            code, out, err = self.call(True)
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, out + err)
        self.assertNotIn('Traceback', out + err)
        self.assertIsNone(self.profiles.capability(CONFIG))


if __name__ == '__main__':
    unittest.main()
