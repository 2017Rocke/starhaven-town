"""Response completion and controlled-budget checks, using offline doubles only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from provider_bridge import ProviderBridge
from test_provider_bridge import MemoryCredentials, MockOpener


MISSING = object()
FINAL_TEXT = '这是最终正文。'
PRIVATE_REASONING = 'SYNTHETIC_PRIVATE_REASONING_DO_NOT_RETURN'


def openai_response(content=FINAL_TEXT, usage=MISSING, finish_reason=MISSING, **message_fields):
    message = dict(message_fields)
    if content is not MISSING:
        message['content'] = content
    choice = {'message': message}
    if finish_reason is not MISSING:
        choice['finish_reason'] = finish_reason
    result = {'choices': [choice]}
    if usage is not MISSING:
        result['usage'] = usage
    return result


def anthropic_response(usage=MISSING, stop_reason='end_turn'):
    result = {'type': 'message', 'role': 'assistant',
              'content': [{'type': 'text', 'text': FINAL_TEXT}], 'stop_reason': stop_reason}
    if usage is not MISSING:
        result['usage'] = usage
    return result


class ResponseOpener(MockOpener):
    """Build response usage from the actual transmitted request when needed."""
    def __init__(self):
        super().__init__()
        self.response_factory = None

    def open(self, request, timeout):
        if self.response_factory is not None:
            self.body = json.dumps(self.response_factory(request), ensure_ascii=False).encode('utf-8')
        return super().open(request, timeout)


class ResponseBudget:
    def __init__(self, enforce=False, max_output_tokens=64):
        self.enforce = enforce
        self.max_output_tokens = max_output_tokens
        self.after_admit_max_output_tokens = None
        self.admitted = []
        self.dispatched = []
        self.settled = []

    def config(self):
        return {'max_output_tokens': self.max_output_tokens}

    def admit(self, config, payload, context):
        self.admitted.append((dict(config), payload))
        context['call_id'] = 'synthetic-response-call'
        context['enforce'] = self.enforce
        if self.after_admit_max_output_tokens is not None:
            self.max_output_tokens = self.after_admit_max_output_tokens

    def dispatch(self, context):
        self.dispatched.append(context['call_id'])

    def settle(self, context):
        self.settled.append(dict(context))

    def blocked(self, config, context):
        pass

    @staticmethod
    def usage(raw):
        if not isinstance(raw, dict):
            return None
        values = [raw.get(name) for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')]
        return raw if all(type(value) is int and value >= 0 for value in values) else None


class ProviderResponseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.opener = ResponseOpener()
        self.bridge = ProviderBridge(self.root / 'runtime', self.root / 'data', MemoryCredentials(),
                                     opener_factory=lambda: self.opener)
        self.config = {'mode': 'api', 'base_url': 'https://offline.example/v1', 'model': 'synthetic-model'}
        self.bridge.save_settings(self.config)
        self.budget = ResponseBudget()
        self.bridge.set_budget(self.budget)
        self.reported_usage = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def respond(self, value):
        self.opener.response_factory = None
        self.opener.body = json.dumps(value, ensure_ascii=False).encode('utf-8')

    def call(self, options=None):
        return self.bridge.generate_result('用户的中文材料与 emoji：🌳', threading.Event(),
                                           'synthetic-resident', options=options)

    def assert_rejected(self, pattern, violation=None, options=None):
        settled_before = len(self.budget.settled)
        with self.assertRaisesRegex(RuntimeError, pattern) as caught:
            self.call(options)
        self.assertEqual(settled_before + 1, len(self.budget.settled))
        context = self.budget.settled[-1]
        self.assertNotIn('reply', context)
        self.assertNotIn(PRIVATE_REASONING, str(caught.exception))
        if violation is not None:
            self.assertEqual(violation, context.get('budget_violation'))
        return context

    def use_anthropic(self):
        self.bridge.save_settings({**self.config, 'protocol': 'anthropic'})

    def test_legacy_missing_finish_reason_and_normal_stop_keep_final_text(self):
        for reason in (MISSING, 'stop'):
            with self.subTest(reason=reason):
                self.respond(openai_response(usage=self.reported_usage, finish_reason=reason))
                result = self.call()
                self.assertEqual(FINAL_TEXT, result['text'])
                self.assertEqual(self.reported_usage, self.budget.settled[-1]['usage'])

    def test_openai_length_rejects_even_nonempty_text_and_settles_reported_usage(self):
        usage = {**self.reported_usage, 'completion_tokens_details': {'reasoning_tokens': 5}}
        for content in ('', FINAL_TEXT):
            with self.subTest(content=content):
                self.respond(openai_response(content, usage, 'length'))
                context = self.assert_rejected('上限|不完整')
                self.assertEqual(usage, context['usage'])

    def test_openai_tool_and_filter_finish_reasons_reject_any_text(self):
        for reason, pattern in (('tool_calls', '工具'), ('function_call', '工具'),
                                ('content_filter', '过滤|安全|审核|限制')):
            with self.subTest(reason=reason):
                self.respond(openai_response(usage=self.reported_usage, finish_reason=reason))
                context = self.assert_rejected(pattern)
                self.assertEqual(self.reported_usage, context['usage'])

    def test_reasoning_only_reports_missing_final_text_without_exposing_reasoning(self):
        for content in (MISSING, None, '', ' \n\t'):
            with self.subTest(content=content):
                self.respond(openai_response(content, self.reported_usage, 'stop',
                                             reasoning_content=PRIVATE_REASONING))
                context = self.assert_rejected('最终.*(正文|文字|回复)')
                self.assertEqual(self.reported_usage, context['usage'])

    def test_reasoning_with_final_text_returns_only_final_text(self):
        self.respond(openai_response(usage=self.reported_usage, finish_reason='stop',
                                     reasoning_content=PRIVATE_REASONING))
        result = self.call()
        self.assertEqual(FINAL_TEXT, result['text'])
        self.assertNotIn(PRIVATE_REASONING, json.dumps(result))

    def test_anthropic_truncation_still_rejects_and_settles_normalized_usage(self):
        self.use_anthropic()
        for reason in ('max_tokens', 'model_context_window_exceeded'):
            with self.subTest(reason=reason):
                self.respond(anthropic_response({'input_tokens': 11, 'output_tokens': 7}, reason))
                context = self.assert_rejected('输出上限' if reason == 'max_tokens' else '上下文容量已满')
                self.assertEqual(self.reported_usage, context['usage'])

    def test_enforced_regular_chat_rejects_missing_usage_without_controlled_option(self):
        self.budget.enforce = True
        for usage in (MISSING, None):
            with self.subTest(usage=usage):
                self.respond(openai_response(usage=usage))
                self.assert_rejected('用量', 'missing_usage')

    def test_controlled_option_rejects_missing_usage_without_budget_enforce(self):
        self.assertFalse(self.budget.enforce)
        self.respond(openai_response())
        self.assert_rejected('用量', 'missing_usage', options={'controlled': True})

    def test_controlled_invalid_usage_is_rejected_and_original_report_is_settled(self):
        invalid = ({}, [], 'not-usage',
                   {'prompt_tokens': 1, 'completion_tokens': 2},
                   {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3},
                   {'prompt_tokens': 1, 'completion_tokens': False, 'total_tokens': 1},
                   {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': True},
                   {'prompt_tokens': 1.0, 'completion_tokens': 2, 'total_tokens': 3},
                   {'prompt_tokens': 1, 'completion_tokens': '2', 'total_tokens': 3},
                   {'prompt_tokens': -1, 'completion_tokens': 2, 'total_tokens': 1},
                   {'prompt_tokens': 1, 'completion_tokens': -1, 'total_tokens': 0},
                   {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 4},
                   {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 2})
        for usage in invalid:
            with self.subTest(usage=usage):
                self.respond(openai_response(usage=usage))
                context = self.assert_rejected('用量', 'invalid_usage', options={'controlled': True})
                self.assertEqual(usage, context['usage'])

    def test_input_bound_uses_full_transmitted_utf8_bytes_and_accepts_exact_limit(self):
        self.budget.enforce = True
        for overrun in (0, 1):
            with self.subTest(overrun=overrun):
                def response(request):
                    prompt = len(request.data) + 512 + overrun
                    return openai_response(usage={'prompt_tokens': prompt, 'completion_tokens': 7,
                                                   'total_tokens': prompt + 7})
                self.opener.response_factory = response
                if overrun:
                    context = self.assert_rejected('用量|输入|上限', 'input_overrun')
                    self.assertEqual(len(self.opener.requests[-1].data) + 513,
                                     context['usage']['prompt_tokens'])
                else:
                    self.assertEqual(FINAL_TEXT, self.call()['text'])
                request_bytes = self.opener.requests[-1].data
                self.assertGreater(len(request_bytes), len(request_bytes.decode('utf-8')))
                self.assertEqual(request_bytes, self.budget.admitted[-1][1])

    def test_output_bound_uses_sent_max_tokens_even_if_config_changes_after_admission(self):
        self.budget.enforce = True
        for completion in (64, 65):
            with self.subTest(completion=completion):
                self.budget.max_output_tokens = 64
                self.budget.after_admit_max_output_tokens = 4096
                usage = {'prompt_tokens': 11, 'completion_tokens': completion, 'total_tokens': 11 + completion}
                self.respond(openai_response(usage=usage))
                if completion > 64:
                    context = self.assert_rejected('用量|输出|上限', 'output_overrun')
                    self.assertEqual(usage, context['usage'])
                else:
                    self.assertEqual(FINAL_TEXT, self.call()['text'])
                self.assertEqual(64, json.loads(self.opener.requests[-1].data)['max_tokens'])
                self.assertEqual(4096, self.budget.max_output_tokens)

    def test_controlled_anthropic_missing_and_invalid_usage_are_distinguished(self):
        self.use_anthropic()
        for usage, violation in ((MISSING, 'missing_usage'), (None, 'missing_usage'),
                                 ({}, 'invalid_usage'), ({'input_tokens': 1}, 'invalid_usage'),
                                 ({'input_tokens': True, 'output_tokens': 1}, 'invalid_usage'),
                                 ({'input_tokens': 1, 'output_tokens': 2,
                                   'cache_read_input_tokens': '3'}, 'invalid_usage')):
            with self.subTest(usage=usage):
                self.respond(anthropic_response(usage))
                self.assert_rejected('用量', violation, options={'controlled': True})

    def test_anthropic_input_bound_includes_cache_creation_and_cache_read_tokens(self):
        self.use_anthropic()
        self.budget.enforce = True
        for overrun in (0, 1):
            with self.subTest(overrun=overrun):
                def response(request):
                    return anthropic_response({'input_tokens': len(request.data), 'output_tokens': 7,
                                               'cache_creation_input_tokens': 300,
                                               'cache_read_input_tokens': 212 + overrun})
                self.opener.response_factory = response
                if overrun:
                    context = self.assert_rejected('用量|输入|上限', 'input_overrun')
                else:
                    self.assertEqual(FINAL_TEXT, self.call()['text'])
                    context = self.budget.settled[-1]
                prompt = len(self.opener.requests[-1].data) + 512 + overrun
                self.assertEqual({'prompt_tokens': prompt, 'completion_tokens': 7, 'total_tokens': prompt + 7},
                                 context['usage'])

    def test_anthropic_output_overrun_rejects_and_preserves_reported_usage(self):
        self.use_anthropic()
        self.respond(anthropic_response({'input_tokens': 11, 'output_tokens': 65}))
        context = self.assert_rejected('用量|输出|上限', 'output_overrun', options={'controlled': True})
        self.assertEqual({'prompt_tokens': 11, 'completion_tokens': 65, 'total_tokens': 76}, context['usage'])

    def test_uncontrolled_text_remains_usable_with_missing_or_unknown_usage(self):
        for usage in (MISSING, None, {}, 'unknown'):
            with self.subTest(usage=usage):
                self.respond(openai_response(usage=usage))
                result = self.call()
                self.assertEqual(FINAL_TEXT, result['text'])
                self.assertEqual('unknown', result['usage_quality'])
                self.assertNotIn('budget_violation', self.budget.settled[-1])

    def test_uncontrolled_legacy_total_remains_compatible(self):
        usage = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 25}
        self.respond(openai_response(usage=usage))
        result = self.call()
        self.assertEqual(FINAL_TEXT, result['text'])
        self.assertEqual(usage, result['usage'])
        self.assertEqual(usage, self.budget.settled[-1]['usage'])
        self.assertNotIn('budget_violation', self.budget.settled[-1])


if __name__ == '__main__':
    unittest.main()
