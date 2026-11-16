"""Durable resident analysis, permissions, clarification and restart; no network."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from server import TownApp
from town_interaction import FRAGMENT_BYTES, InteractionOffice, split_text
from town_store import TownError, digest, uid


def answer(**values):
    result = dict(summary='已核对当前材料', scope=['仅处理明确范围'], acceptance_criteria=['可按来源核验'], questions=[])
    result.update(values)
    return result


class AnalysisBridge:
    def __init__(self):
        self.calls, self.responses = [], []
        self.callback = None
        self.model = 'synthetic'

    def settings(self):
        return dict(mode='api', base_url='https://synthetic.invalid/v1', model=self.model)

    def resolved_budget_capability(self, config):
        return dict(supports_vision=True)

    def generate_result(self, prompt, cancel, agent, options):
        options['before_dispatch']()
        self.calls.append(dict(prompt=prompt, agent=agent, options=options))
        if options.get('on_progress'):
            options['on_progress'](dict(phase='receiving', text_chars=20, thinking='NOT_PUBLIC', partial='NOT_PUBLIC'))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        result = self.responses.pop(0) if self.responses else answer()
        if isinstance(result, Exception):
            raise result
        return dict(text=json.dumps(result, ensure_ascii=False), call_id=uid())

    def close(self):
        pass


class Documents:
    def __init__(self):
        self.docs, self.resolutions, self.denied = {}, [], False

    def add(self, identity, owner, image=False, partial=False):
        fragment = dict(id='fragment-' + identity, label='第1页', text='资料 ' + identity)
        images = []
        if image:
            fragment['image_id'] = 'image-' + identity
            images = [dict(id=fragment['image_id'], mime='image/png', content_base64='c3ludGhldGlj', sha256='synthetic')]
        self.docs[identity] = dict(id=identity, name=identity, owner=owner, sha256=digest(fragment), fragments=[fragment],
            images=images, status='partial' if partial else 'ready', warnings=['第2页没有解析'] if partial else [])

    def read(self, identity, agent_id, project_id=None):
        document = self.docs[identity]
        if self.denied or document['owner'] != agent_id:
            raise TownError(403, '资料不属于该居民')
        return copy.deepcopy(document)

    def resolve(self, ids, agent_id, project_id=None, endpoint=None, fragment_ids=None):
        self.resolutions.append(dict(ids=ids, agent_id=agent_id, project_id=project_id, fragment_ids=fragment_ids))
        return [self.read(identity, agent_id, project_id) for identity in ids]


class InteractionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = AnalysisBridge()
        self.app = TownApp(Path(self.temp.name) / 'town', self.bridge, Path(self.temp.name) / 'team',
                           report_root=Path(self.temp.name) / 'reports', start_growth=False)
        existing = getattr(self.app, 'interactions', None)
        if existing:
            existing.close()
        self.office = self.app.interactions = InteractionOffice(self.app)
        self.documents = self.app.documents = Documents()

    def tearDown(self):
        self.office.close()
        self.app.close()
        self.temp.cleanup()

    def create(self, goal='分析当前任务', ids=None, agent='linghang'):
        return self.office.create(dict(agent_id=agent, goal=goal, attachment_ids=ids or [], request_id=uid()))

    def action(self, item, action, **values):
        return self.office.action(item['id'], dict(action=action, revision=item['revision'], request_id=uid(), **values))

    def complete(self, identity):
        for unused in range(50):
            item = self.office.detail(identity)
            if item['status'] != 'queued':
                return item
            self.office.run_once(identity)
        self.fail('analysis did not terminate')

    def test_single_fragment_legacy_schema_is_real_bounded_work(self):
        item = self.create()
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(item['status'], 'queued')
        item = self.complete(item['id'])
        self.assertEqual(item['status'], 'completed')
        self.assertEqual(item['progress'], dict(completed=1, total=1))
        self.assertEqual(len(self.bridge.calls), 1)
        options = self.bridge.calls[0]['options']
        self.assertTrue(options['controlled'] and options['interaction'] and options['stream'])
        self.assertFalse(options['workbench'])
        self.assertEqual(options['output_tokens'], 8192)
        self.assertTrue(item['result']['requirement_items'][0]['sources'])
        self.assertNotIn('NOT_PUBLIC', json.dumps(item))

    def test_multifragment_analysis_preserves_sources_and_cross_checks(self):
        goal = '汉字材料' * 1000
        item = self.create(goal)
        self.assertEqual(len(item['steps']), 2)
        item = self.complete(item['id'])
        self.assertEqual(item['status'], 'completed')
        self.assertEqual([step['kind'] for step in item['steps']], ['analyze', 'analyze', 'review'])
        self.assertEqual(item['progress'], dict(completed=3, total=3))
        self.assertEqual(len(item['evidence']), 2)
        self.assertEqual(''.join(split_text(goal)), goal)
        self.assertTrue(all(len(chunk.encode()) <= FRAGMENT_BYTES for chunk in split_text(goal)))

    def test_restart_and_explicit_resume_keep_completed_steps(self):
        item = self.create('A' * 9000)
        self.office.run_once(item['id'])
        self.bridge.responses = [RuntimeError('连接中断')]
        self.office.run_once(item['id'])
        self.assertEqual(self.office.detail(item['id'])['status'], 'interrupted')
        self.assertEqual(len(self.bridge.calls), 2)
        self.office.close()
        self.office = self.app.interactions = InteractionOffice(self.app)
        self.assertFalse(self.office.run_once())
        current = self.office.detail(item['id'])
        self.action(current, 'resume')
        current = self.complete(item['id'])
        self.assertEqual(current['status'], 'completed')
        self.assertEqual([step['attempts'] for step in current['steps']], [1, 2, 1])
        self.assertEqual(len(self.bridge.calls), 4)

    def test_manual_retry_limit_prevents_endless_recharging(self):
        item = self.create()
        for count in range(3):
            self.bridge.responses = [RuntimeError('HTTP 504')]
            self.office.run_once(item['id'])
            current = self.office.detail(item['id'])
            if count < 2:
                self.action(current, 'resume')
        with self.assertRaisesRegex(TownError, '三次'):
            self.action(current, 'resume')
        self.assertEqual(len(self.bridge.calls), 3)

    def test_504_splits_only_failed_material_and_waits_for_explicit_resume(self):
        item = self.create('A' * 9000)
        self.office.run_once(item['id'])
        self.bridge.responses = [RuntimeError('网关 HTTP 504')]
        self.office.run_once(item['id'])
        item = self.office.detail(item['id'])
        self.assertEqual(item['status'], 'interrupted')
        self.assertEqual(item['steps'][0]['status'], 'completed')
        self.assertEqual(item['steps'][1]['status'], 'superseded')
        self.assertEqual(len(self.bridge.calls), 2)
        self.assertFalse(self.office.run_once())
        self.action(item, 'resume')
        item = self.complete(item['id'])
        self.assertEqual(item['status'], 'completed')
        self.assertEqual(item['steps'][0]['attempts'], 1)

    def test_pause_and_revision_change_discard_inflight_results(self):
        for action in ('pause', 'revise'):
            with self.subTest(action=action):
                item = self.create()
                self.bridge.callback = lambda: self.action(item, action, **(dict(goal='新的目标') if action == 'revise' else {}))
                self.office.run_once(item['id'])
                current = self.office.detail(item['id'])
                self.assertIsNone(current['result'])
                self.assertEqual(current['progress']['completed'], 0)
                self.assertEqual(current['status'], 'paused' if action == 'pause' else 'queued')

    def test_answer_reanalyzes_affected_material_and_resolves_question(self):
        self.bridge.responses = [answer(questions=[dict(text='保存多久？', blocking=True)])]
        item = self.complete(self.create()['id'])
        self.assertEqual(item['status'], 'waiting_input')
        answered = self.action(item, 'answer', question_id=item['questions'][0]['id'], answer='保存30天')
        self.assertEqual(answered['revision'], 2)
        self.assertIsNone(answered['result'])
        result = self.complete(item['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertGreater(len(self.bridge.calls), 1)
        self.assertTrue(any('保存30天' in call['prompt'] for call in self.bridge.calls))
        with self.assertRaises(TownError):
            self.action(item, 'pause')

    def test_revise_keeps_unchanged_document_extraction_but_rebuilds_review(self):
        self.documents.add('one', 'linghang')
        self.documents.add('two', 'linghang')
        item = self.complete(self.create(ids=['one'])['id'])
        old_step = next(step for step in item['steps'] if step['label'].startswith('one'))
        revised = self.action(item, 'revise', goal=item['goal'], attachment_ids=['one', 'two'])
        kept = next(step for step in revised['steps'] if step['id'] == old_step['id'])
        self.assertEqual(kept['status'], 'completed')
        self.assertIsNone(revised['result'])
        self.assertEqual(self.complete(item['id'])['status'], 'completed')

    def test_private_material_and_sessions_are_agent_scoped(self):
        self.documents.add('private', 'linghang')
        item = self.create(ids=['private'])
        with self.assertRaises(TownError):
            self.office.detail(item['id'], 'scout')
        with self.assertRaises(TownError):
            self.create(ids=['private'], agent='scout')
        self.assertEqual(self.office.list('scout'), [])
        self.assertEqual(len(self.office.list('linghang')), 1)

    def test_unknown_source_reference_and_hidden_reasoning_are_not_saved(self):
        self.bridge.responses = [answer(facts=[dict(text='虚假来源', sources=['not-supplied'])])]
        item = self.complete(self.create()['id'])
        self.assertEqual(item['status'], 'interrupted')
        self.assertIsNone(item['result'])
        self.bridge.responses = [answer(thinking='PRIVATE_REASONING', chain_of_thought='PRIVATE_REASONING')]
        item = self.complete(self.create()['id'])
        self.assertEqual(item['status'], 'completed')
        self.assertNotIn('PRIVATE_REASONING', json.dumps(item))
        self.assertNotIn(b'PRIVATE_REASONING', self.office.path.read_bytes())

    def test_image_is_attached_only_to_its_source_step_with_verified_origin(self):
        self.documents.add('picture', 'linghang', image=True)
        item = self.complete(self.create(ids=['picture'])['id'])
        self.assertEqual(item['status'], 'completed')
        image_calls = [call for call in self.bridge.calls if call['options']['images']]
        self.assertEqual(len(image_calls), 1)
        self.assertEqual(image_calls[0]['options']['images'], [dict(id='image-picture', mime_type='image/png', data='c3ludGhldGlj')])
        self.assertEqual(self.documents.resolutions[0]['fragment_ids'], {'picture': ['fragment-picture']})
        self.assertNotIn('c3ludGhldGlj', json.dumps(item))

    def test_unverified_vision_blocks_image_without_claiming_it_was_read(self):
        self.documents.add('picture', 'linghang', image=True)
        self.bridge.resolved_budget_capability = lambda config: dict(supports_vision=False)
        item = self.complete(self.create(ids=['picture'])['id'])
        self.assertEqual(item['status'], 'blocked')
        self.assertIn('图片尚未读取', item['error'])
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertIsNone(item['result'])

    def test_review_rechecks_private_source_access_before_sending_derived_material(self):
        self.documents.add('private', 'linghang')
        item = self.create(ids=['private'])
        self.office.run_once(item['id'])
        self.office.run_once(item['id'])
        self.documents.denied = True
        item = self.complete(item['id'])
        self.assertEqual(item['status'], 'blocked')
        self.assertEqual(len(self.bridge.calls), 2)
        self.assertIsNone(item['result'])

    def test_partial_document_cannot_claim_complete_understanding(self):
        self.documents.add('partial', 'linghang', partial=True)
        item = self.complete(self.create(ids=['partial'])['id'])
        self.assertEqual(item['status'], 'waiting_input')
        self.assertTrue(any('未读取' in question['text'] and question['blocking'] for question in item['questions']))

    def test_changed_or_revoked_documents_block_reanalysis_before_dispatch(self):
        self.documents.add('document', 'linghang')
        item = self.create(ids=['document'])
        self.office.run_once(item['id'])
        self.documents.docs['document']['fragments'][0]['text'] += ' altered'
        current = self.complete(item['id'])
        self.assertEqual(current['status'], 'interrupted')
        self.assertEqual(len(self.bridge.calls), 1)

    def test_revocation_after_response_does_not_publish_old_result(self):
        item = self.create()
        self.bridge.callback = lambda: self.app.budget.revoke('linghang')
        self.office.run_once(item['id'])
        item = self.office.detail(item['id'])
        self.assertEqual(item['status'], 'blocked')
        self.assertIsNone(item['result'])

    def test_idempotent_create_and_action_never_repeat_model_call(self):
        data = dict(agent_id='linghang', goal='说明范围', attachment_ids=[], request_id=uid())
        first = self.office.create(data)
        second = self.office.create(data)
        self.assertEqual(first['id'], second['id'])
        action = dict(action='pause', revision=1, request_id=uid())
        self.office.action(first['id'], action)
        self.office.action(first['id'], action)
        self.assertFalse(self.office.run_once())
        self.assertEqual(self.bridge.calls, [])


class InteractionDevelopmentTest(unittest.TestCase):
    tearDown = InteractionTest.tearDown
    action = InteractionTest.action

    def setUp(self):
        InteractionTest.setUp(self)
        self.members = dict(product='resident-yanghui', developer='resident-jiji', tester='resident-alige', commander='linghang')
        self.project = self.app.development.create_project(dict(title='合成项目', members=self.members, request_id=uid()))
        self.task = self.app.development.create_task(dict(project_id=self.project['id'], title='合成需求', requirements='明确需求', request_id=uid()))

    def dev_action(self, action, **values):
        return self.app.development.action(self.task['id'], dict(action=action, revision=1, request_id=uid(), **values))

    def test_development_clarification_requires_real_reanalysis_before_confirmation(self):
        self.bridge.responses = [answer(questions=['保存多久？'])]
        self.dev_action('analyze')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['interaction']['status'], 'waiting_input')
        self.dev_action('answer', question_id=task['questions'][0]['id'], answer='30天')
        with self.assertRaises(TownError):
            self.dev_action('confirm')
        self.app.development.run_once()
        task = self.dev_action('confirm')
        self.assertEqual(task['active_revision'], 1)
        self.assertTrue(all(call['options']['workbench'] for call in self.bridge.calls))
        self.assertTrue(any('30天' in call['prompt'] for call in self.bridge.calls))

    def test_managed_session_cannot_bypass_task_actions(self):
        self.dev_action('analyze')
        self.app.development.run_once()
        item = self.app.development.task(self.task['id'])['interaction']
        with self.assertRaisesRegex(TownError, '研发分析请通过'):
            self.action(item, 'revise', goal='绕过需求版本')

    def test_private_product_attachment_cannot_become_shared_requirement(self):
        self.documents.add('private', self.members['product'])
        self.documents.docs['private']['scope'] = dict(classification='private', project_id=self.project['id'])
        with self.assertRaisesRegex(TownError, '私人资料不能'):
            self.app.development.create_task(dict(project_id=self.project['id'], title='不能分享', requirements='分析私人资料',
                attachment_ids=['private'], request_id=uid()))

    def test_development_pause_and_resume_reuse_completed_step(self):
        self.task = self.app.development.action(self.task['id'], dict(action='revise', revision=1, request_id=uid(), requirements='A' * 9000))
        def action(name):
            return self.app.development.action(self.task['id'], dict(action=name, revision=2, request_id=uid()))
        action('analyze')
        self.bridge.responses = [answer(), RuntimeError('连接中断')]
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['run_status'], 'failed')
        self.assertEqual(task['interaction']['progress']['completed'], 1)
        action('resume')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['run_status'], 'completed')
        self.assertEqual(task['interaction']['steps'][0]['attempts'], 1)

    def test_pause_during_product_call_discards_reply_and_resume_stays_in_task(self):
        self.dev_action('analyze')
        self.bridge.callback = lambda: self.dev_action('pause')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['run_status'], 'paused')
        self.assertEqual(task['interaction']['status'], 'paused')
        self.assertIsNone(task['interaction']['result'])
        self.assertFalse(self.office.run_once())
        self.dev_action('resume')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['run_status'], 'completed')
        self.assertEqual(task['interaction']['steps'][0]['attempts'], 2)

    def test_new_requirement_revision_prevents_late_analysis_publication(self):
        self.dev_action('analyze')
        self.bridge.callback = lambda: self.dev_action('revise', requirements='另一个业务需求')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        self.assertEqual(task['revision'], 2)
        self.assertEqual(task['artifacts'], [])
        self.assertEqual(task['questions'], [])
