"""Real local attachment/session routes, with synthetic state and no model network."""
import base64
import http.client
import json
import unittest
from urllib.parse import urlencode

import test_dev_workbench_http as fixtures
import test_documents_store as document_fixtures
from test_interaction import answer
from town_store import uid


class InteractionHTTPTest(unittest.TestCase):
    setUp = fixtures.DevWorkbenchHTTPTest.setUp
    tearDown = fixtures.DevWorkbenchHTTPTest.tearDown
    request = fixtures.DevWorkbenchHTTPTest.request
    expect = fixtures.DevWorkbenchHTTPTest.expect
    create_project_task = fixtures.DevWorkbenchHTTPTest.create
    dev_action = fixtures.DevWorkbenchHTTPTest.action

    def scope(self, path, agent=None, project=None):
        values = dict(agent_id=agent or self.members['product'])
        if project is not None:
            values['project_id'] = project
        return path + '?' + urlencode(values)

    def upload_payload(self, text='合成需求：任务完成后保留记录。', **fields):
        payload = dict(name='需求.md', content_base64=base64.b64encode(text.encode()).decode(),
                       agent_id=self.members['product'])
        payload.update(fields)
        return payload

    def upload(self, text='合成需求：任务完成后保留记录。', **fields):
        return self.expect('POST', '/api/documents', self.upload_payload(text, **fields), 201)

    def create_session(self, **values):
        payload = dict(agent_id=self.members['product'], goal='分析合成任务',
                       attachment_ids=[], request_id=uid())
        payload.update(values)
        return self.expect('POST', '/api/interactions', payload, 201)

    def session_path(self, item, agent=None):
        return self.scope('/api/interactions/' + item['id'], agent)

    def session_action(self, item, action, status=200, **values):
        payload = dict(agent_id=item['agent_id'], action=action, revision=item['revision'], request_id=uid())
        payload.update(values)
        return self.expect('POST', '/api/interactions/' + item['id'] + '/action', payload, status)

    def binary(self, path, method='GET'):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.request(method, path)
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def finish(self, item):
        for unused in range(20):
            current = self.expect('GET', self.session_path(item))
            if current['status'] != 'queued':
                return current
            self.bridge.responses.append(answer())
            self.app.interactions.run_once(item['id'])
        self.fail('session did not finish')

    def test_private_upload_list_preview_and_cross_resident_denial(self):
        item = self.upload()
        self.assertEqual(item['scope']['classification'], 'private')
        self.assertEqual(item['status'], 'ready')
        self.assertEqual(len(item['fragments']), 1)
        path = '/api/documents/' + item['id']
        self.assertEqual(item, self.expect('GET', self.scope(path)))
        self.assertEqual([item['id']], [row['id'] for row in self.expect('GET', self.scope('/api/documents'))['documents']])
        self.assertEqual([], self.expect('GET', self.scope('/api/documents', self.members['tester']))['documents'])
        self.expect('GET', self.scope(path, self.members['tester']), status=403)
        self.expect('GET', path, status=400)
        self.assertNotIn(str(self.root), json.dumps(item))
        self.assertNotIn('content_base64', json.dumps(item))
        self.assertEqual(self.bridge.calls, [])

    def test_requirement_original_is_shared_only_with_project_working_roles(self):
        project, unused = self.create_project_task()
        second, unused = self.create_project_task()
        item = self.upload(classification='requirement', project_id=project['id'])
        path = '/api/documents/' + item['id']
        for role in ('product', 'developer', 'tester'):
            with self.subTest(role=role):
                scoped = self.scope(path, self.members[role], project['id'])
                self.assertEqual(item['id'], self.expect('GET', scoped)['id'])
                status, unused_headers, content = self.binary(self.scope(path + '/assets', self.members[role], project['id']))
                self.assertEqual(status, 200)
                self.assertIn('合成需求'.encode(), content)
        self.expect('GET', self.scope(path, self.members['commander'], project['id']), status=403)
        self.expect('GET', self.scope(path, project=second['id']), status=403)
        self.expect('GET', self.scope(path), status=403)
        self.assertEqual([], self.expect('GET', self.scope('/api/documents', self.members['commander'], project['id']))['documents'])

    def test_project_private_attachment_cannot_escape_project_or_become_requirement(self):
        project, unused = self.create_project_task()
        private = self.upload(project_id=project['id'])
        path = '/api/documents/' + private['id']
        self.expect('GET', self.scope(path), status=403)
        self.assertEqual(private['id'], self.expect('GET', self.scope(path, project=project['id']))['id'])
        self.expect('POST', '/api/documents', self.upload_payload(classification='requirement', project_id=project['id']), 403)
        self.expect('POST', '/api/dev/tasks', dict(project_id=project['id'], title='不可转发私有附件',
            requirements='合成需求', attachment_ids=[private['id']], request_id=uid()), 403)
        self.assertEqual(self.bridge.calls, [])

    def test_original_download_is_attachment_nosniff_no_store_and_head_has_no_body(self):
        text = '<script>syntheticActiveText()</script>\n# 仅是文档正文'
        item = self.upload(text)
        path = self.scope('/api/documents/' + item['id'] + '/assets')
        status, headers, content = self.binary(path)
        self.assertEqual(status, 200)
        self.assertEqual(content, text.encode())
        self.assertEqual(headers['Content-Disposition'], 'attachment')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertEqual(headers['Content-Type'], 'text/plain')
        status, headers, content = self.binary(path, 'HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(content, b'')
        self.assertEqual(int(headers['Content-Length']), len(text.encode()))

    @unittest.skipUnless(document_fixtures.HAS_IMAGES, 'Pillow runtime required')
    def test_image_preview_uses_sanitized_derivative_and_keeps_resident_scope(self):
        raw = document_fixtures.image_bytes(private=True)
        item = self.expect('POST', '/api/documents', dict(name='截图.png',
            content_base64=base64.b64encode(raw).decode(), agent_id=self.members['product']), 201)
        image = item['images'][0]
        path = '/api/documents/' + item['id'] + '/assets/' + image['id']
        status, headers, content = self.binary(self.scope(path))
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Content-Disposition', headers)
        self.assertNotIn(b'SYNTHETIC_PRIVATE_METADATA', content)
        self.assertNotEqual(raw, content)
        self.expect('GET', self.scope(path, self.members['tester']), status=403)
        self.expect('GET', self.scope(path.rsplit('/', 1)[0] + '/' + 'a' * 32), status=404)

    def test_documents_and_sessions_reject_csrf_bad_host_and_unscoped_reads(self):
        item = self.upload()
        session = self.create_session()
        operations = [('/api/documents', self.upload_payload('不应写入')),
                      ('/api/interactions', dict(agent_id=session['agent_id'], goal='不应创建', request_id=uid())),
                      ('/api/interactions/' + session['id'] + '/action', dict(agent_id=session['agent_id'],
                          action='pause', revision=session['revision'], request_id=uid()))]
        for headers, expected in (({'Origin': 'https://other.example'}, 403),
                                  ({'Sec-Fetch-Site': 'cross-site'}, 403),
                                  ({'X-Town-Client': None}, 403),
                                  ({'Content-Type': 'text/plain'}, 415),
                                  ({'Host': 'unrelated.example'}, 403)):
            for path, payload in operations:
                with self.subTest(path=path, headers=headers):
                    self.expect('POST', path, payload, expected, headers)
        for path in (self.scope('/api/documents'), self.scope('/api/documents/' + item['id']),
                     self.scope('/api/documents/' + item['id'] + '/assets'),
                     self.scope('/api/interactions'), self.session_path(session)):
            self.expect('GET', path, status=403, headers={'Origin': 'https://other.example'})
        self.expect('GET', '/api/interactions', status=400)
        self.expect('GET', '/api/interactions/' + session['id'], status=400)
        self.expect('POST', '/api/interactions/' + session['id'] + '/action',
            dict(action='pause', revision=session['revision'], request_id=uid()), 400)
        self.assertEqual('queued', self.expect('GET', self.session_path(session))['status'])
        self.assertEqual(self.bridge.calls, [])

    def test_invalid_uploads_and_query_scope_do_not_create_documents(self):
        for payload, status in ((self.upload_payload(name='../outside.md'), 400),
                                (dict(self.upload_payload(), content_base64='not-base64!'), 400),
                                (dict(self.upload_payload(), classification='source'), 400),
                                (dict(self.upload_payload(), path='/not-an-upload'), 400)):
            with self.subTest(payload=payload):
                self.expect('POST', '/api/documents', payload, status)
        self.expect('GET', '/api/documents?agent_id=a&agent_id=b', status=400)
        self.expect('GET', '/api/documents?agent_id=a&other=1', status=400)
        self.assertEqual([], self.expect('GET', self.scope('/api/documents'))['documents'])

    def test_ordinary_analysis_resolves_real_uploaded_text_before_model_work(self):
        attachment = self.upload('HTTP_SOURCE_SENTINEL：必须保留来源并处理完成。')
        session = self.create_session(attachment_ids=[attachment['id']])
        self.assertEqual(session['status'], 'queued')
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual([session['id']], [row['id'] for row in self.expect('GET', self.scope('/api/interactions'))['sessions']])
        result = self.finish(session)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['progress'], dict(completed=3, total=3))
        self.assertTrue(any('HTTP_SOURCE_SENTINEL' in prompt for unused_agent, prompt in self.bridge.calls))
        self.assertEqual({source.get('document_id') for source in result['evidence'] if source.get('document_id')}, {attachment['id']})
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_session_detail_actions_and_attachments_cannot_cross_residents(self):
        attachment = self.upload('PRODUCT_PRIVATE_ATTACHMENT')
        session = self.create_session(attachment_ids=[attachment['id']])
        other = self.members['tester']
        self.expect('GET', self.session_path(session, other), status=403)
        self.assertEqual([], self.expect('GET', self.scope('/api/interactions', other))['sessions'])
        self.session_action(session, 'pause', 403, agent_id=other)
        self.expect('POST', '/api/interactions', dict(agent_id=other, goal='跨居民读取',
            attachment_ids=[attachment['id']], request_id=uid()), 403)
        self.assertEqual('queued', self.expect('GET', self.session_path(session))['status'])
        self.assertEqual(self.bridge.calls, [])

    def test_session_idempotency_revision_pause_resume_and_revise(self):
        payload = dict(agent_id=self.members['product'], goal='初始目标', attachment_ids=[], request_id=uid())
        session = self.expect('POST', '/api/interactions', payload, 201)
        self.assertEqual(session, self.expect('POST', '/api/interactions', payload, 201))
        self.expect('POST', '/api/interactions', dict(payload, goal='覆盖同一请求'), 409)
        paused = self.session_action(session, 'pause')
        self.assertEqual(paused['status'], 'paused')
        self.assertFalse(self.app.interactions.run_once(session['id']))
        resumed = self.session_action(paused, 'resume')
        self.assertEqual(resumed['status'], 'queued')
        revised = self.session_action(resumed, 'revise', goal='修改后的目标')
        self.assertEqual(revised['revision'], 2)
        self.session_action(session, 'pause', 409)
        self.assertEqual(self.finish(revised)['goal'], '修改后的目标')

    def test_clarification_answers_reanalyze_and_reject_unrelated_question(self):
        session = self.create_session()
        self.bridge.responses = [answer(questions=[dict(text='保留记录多久？', blocking=True)])]
        self.app.interactions.run_once(session['id'])
        pending = self.expect('GET', self.session_path(session))
        self.assertEqual(pending['status'], 'waiting_input')
        self.session_action(pending, 'answer', 404, question_id='missing', answer='30天')
        queued = self.session_action(pending, 'answer', question_id=pending['questions'][0]['id'], answer='30天')
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['revision'], 2)
        completed = self.finish(queued)
        self.assertEqual(completed['status'], 'completed')
        self.assertTrue(any('30天' in prompt for unused_agent, prompt in self.bridge.calls))

    def test_managed_requirement_analysis_uses_same_engine_but_generic_actions_are_denied(self):
        project, unused = self.create_project_task()
        attachment = self.upload('SHARED_REQUIREMENT_SENTINEL', classification='requirement', project_id=project['id'])
        task = self.expect('POST', '/api/dev/tasks', dict(project_id=project['id'], title='带附件的需求',
            requirements='分析附件并给出验收范围', attachment_ids=[attachment['id']], request_id=uid()), 201)
        self.bridge.responses = [answer(), answer(), answer()]
        self.dev_action(task, 'analyze')
        self.app.development.run_once()
        task = self.expect('GET', '/api/dev/tasks/' + task['id'])
        session = task['interaction']
        self.assertEqual(session['mode'], 'development')
        self.assertEqual(session['status'], 'completed')
        self.assertTrue(any('SHARED_REQUIREMENT_SENTINEL' in prompt for unused_agent, prompt in self.bridge.calls))
        self.assertEqual([], self.expect('GET', self.scope('/api/interactions'))['sessions'])
        self.session_action(session, 'resume', 403)
        self.session_action(session, 'revise', 403, goal='绕过项目需求版本')
        self.assertEqual(task['revision'], 1)
        self.assertEqual(self.expect('GET', '/api/dev/tasks/' + task['id'])['interaction']['goal'], session['goal'])
        self.expect('POST', '/api/interactions', dict(agent_id=session['agent_id'], goal='伪造托管会话',
            mode='development', request_id=uid()), 400)


if __name__ == '__main__':
    unittest.main()
