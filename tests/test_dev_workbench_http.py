"""Real loopback routes with temporary state and scripted, offline model replies."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import create_server
import test_dev_runtime as runtime_fixtures
from town_store import uid


class DevWorkbenchHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.bridge = runtime_fixtures.ScriptedBridge()
        self.secret = 'd' * 64
        self.server = create_server(0, self.root / 'data', self.bridge, self.root / 'team',
                                    report_root=self.root / 'reports', owner_secret=self.secret, start_growth=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.app = self.server.app
        with self.app.db() as db:
            identities = [r[0] for r in db.execute('SELECT id FROM agents ORDER BY id LIMIT 4')]
        self.members = dict(zip(('product', 'developer', 'tester', 'commander'), identities))
        self.repo = self.root / 'source'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        (self.repo / '.git' / 'HEAD').write_text('user-owned-git')
        self.private = 'OWNER_PRIVATE_SOURCE_SENTINEL'
        (self.repo / 'example.py').write_text('# ' + self.private + '\nvalue = 1\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        all_headers = {'Content-Type': 'application/json', 'X-Town-Client': 'pixel-town'}
        for key, value in (headers or {}).items():
            if value is None:
                all_headers.pop(key, None)
            else:
                all_headers[key] = value
        try:
            client.request(method, path, None if body is None else json.dumps(body), all_headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def expect(self, method, path, body=None, status=200, headers=None):
        code, result = self.request(method, path, body, headers)
        self.assertEqual(status, code, result)
        return result

    def create(self):
        project = self.expect('POST', '/api/dev/projects', {'title': 'HTTP项目', 'members': self.members, 'request_id': uid()}, 201)
        task = self.expect('POST', '/api/dev/tasks', {'title': '修改值', 'project_id': project['id'], 'requirements': '值改为2并保留用户内容', 'request_id': uid()}, 201)
        return project, task

    def action(self, task, action, status=200, **values):
        return self.expect('POST', '/api/dev/tasks/' + task['id'] + '/action',
                           {'action': action, 'revision': task['revision'], 'request_id': uid(), **values}, status)

    def native(self, action, payload):
        intent = self.expect('POST', '/api/owner/intents', {'action': action, 'payload': payload})
        preview = self.expect('GET', '/internal/owner/intents/' + intent['id'], headers={'X-Town-Owner': self.secret})
        self.expect('POST', '/internal/owner/confirm', {'id': intent['id'], 'digest': preview['digest'], 'approved': True}, headers={'X-Town-Owner': self.secret})
        return intent, preview

    def connection_payload(self, project):
        return {'project_id': project['id'], 'repositories': [{'name': 'repo', 'path': str(self.repo)}], 'allow_source': True}

    def ready(self, task):
        self.bridge.responses = [{'summary': '明确范围', 'scope': ['修改值'], 'acceptance_criteria': ['值为2'], 'questions': []}]
        self.action(task, 'analyze')
        self.app.development.run_once()
        return self.action(task, 'confirm')

    def candidate(self, project, task):
        self.native('dev-connect', self.connection_payload(project))
        task = self.ready(task)
        self.bridge.responses = [{'action': 'read', 'path': 'repo/example.py'},
            {'action': 'replace', 'path': 'repo/example.py', 'old': 'value = 1', 'new': 'value = 2'},
            {'action': 'finish', 'summary': self.private + ' — 私有开发摘要'}]
        self.action(task, 'develop')
        self.app.development.run_once()
        detail = self.expect('GET', '/api/dev/tasks/' + task['id'])
        artifact = next(a for a in detail['artifacts'] if a['kind'] == 'development_candidate')
        return detail, artifact

    def test_project_task_routes_idempotency_and_error_statuses(self):
        initial = self.expect('GET', '/api/dev/workspace')
        self.assertFalse(initial['enabled'])
        self.assertTrue(initial['native_available'])
        self.assertFalse(initial['capabilities']['deployment'])
        self.assertFalse(initial['capabilities']['acceptance'])
        project, task = self.create()
        payload = {'project_id': project['id'], 'title': '幂等任务', 'requirements': '正文', 'request_id': uid()}
        first = self.expect('POST', '/api/dev/tasks', payload, 201)
        self.assertEqual(first, self.expect('POST', '/api/dev/tasks', payload, 201))
        self.expect('POST', '/api/dev/tasks', {**payload, 'requirements': '覆盖'}, 409)
        self.expect('GET', '/api/dev/tasks/missing', status=404)
        self.expect('GET', '/api/dev/connection/missing', status=404)
        self.expect('GET', '/api/dev/runs/missing', status=404)
        self.expect('POST', '/api/dev/projects', {'title': '错误绑定', 'members': {**self.members, 'tester': self.members['developer']}, 'request_id': uid()}, 400)
        self.expect('POST', '/api/dev/tasks', {'project_id': 'missing', 'title': '任务', 'requirements': '正文', 'request_id': uid()}, 404)
        self.action(task, 'confirm', 409)
        self.action(task, 'accepted', 400)
        revised = self.action(task, 'revise', requirements='新草稿')
        self.assertEqual(2, revised['revision'])
        self.action(task, 'analyze', 409)
        self.assertEqual('新草稿', self.expect('GET', '/api/dev/tasks/' + task['id'])['requirements'])
        self.assertEqual([], self.bridge.calls)

    def test_workbench_rejects_csrf_and_wrong_host(self):
        project, task = self.create()
        for headers, expected in (({'Origin': 'https://other.example'}, 403),
                                  ({'Sec-Fetch-Site': 'cross-site'}, 403),
                                  ({'X-Town-Client': None}, 403),
                                  ({'X-Town-Client': 'other'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415),
                                  ({'Host': 'unrelated.example'}, 403)):
            with self.subTest(headers=headers):
                self.expect('POST', '/api/dev/tasks/' + task['id'] + '/action',
                            {'action': 'revise', 'revision': 1, 'requirements': '未经授权', 'request_id': uid()}, expected, headers)
        for path in ('/api/dev/workspace', '/api/dev/tasks/' + task['id'], '/api/dev/connection/' + project['id']):
            self.expect('GET', path, status=403, headers={'Origin': 'https://other.example'})
        self.expect('OPTIONS', '/api/dev/projects', status=405)
        self.assertEqual(1, self.expect('GET', '/api/dev/tasks/' + task['id'])['revision'])

    def test_http_cannot_confirm_native_source_grant_or_replay_it(self):
        project, unused = self.create()
        intent = self.expect('POST', '/api/owner/intents', {'action': 'dev-connect', 'payload': self.connection_payload(project)})
        path = '/internal/owner/intents/' + intent['id']
        self.expect('GET', path, status=403)
        self.expect('GET', path, status=403, headers={'X-Town-Owner': 'wrong'})
        preview = self.expect('GET', path, headers={'X-Town-Owner': self.secret})
        confirm = {'id': intent['id'], 'digest': preview['digest'], 'approved': True}
        self.expect('POST', '/internal/owner/confirm', confirm, 403)
        self.expect('POST', '/internal/owner/confirm', confirm, 403, {'X-Town-Owner': 'wrong'})
        self.expect('POST', '/internal/owner/confirm', confirm, 403, {'X-Town-Owner': self.secret, 'Origin': 'https://other.example'})
        self.assertFalse(self.expect('GET', '/api/dev/connection/' + project['id'])['connected'])
        self.expect('POST', '/api/dev/connect', self.connection_payload(project), 404)
        self.expect('POST', '/internal/owner/confirm', confirm, headers={'X-Town-Owner': self.secret})
        self.assertTrue(self.expect('GET', '/api/dev/connection/' + project['id'])['connected'])
        self.expect('POST', '/internal/owner/confirm', confirm, 409, {'X-Town-Owner': self.secret})
        self.assertNotIn(self.secret, json.dumps(self.expect('GET', '/api/dev/workspace')))

    def test_question_ids_are_scoped_to_http_task_and_revision(self):
        unused, task = self.create()
        second = self.expect('POST', '/api/dev/tasks', {'project_id': task['project_id'], 'title': '另一个需求', 'requirements': '独立正文', 'request_id': uid()}, 201)
        self.bridge.responses = [{'summary': '需要澄清', 'scope': [], 'acceptance_criteria': [], 'questions': [{'text': '业务范围？', 'blocking': True}]}]
        self.action(task, 'analyze')
        self.app.development.run_once()
        question = self.expect('GET', '/api/dev/tasks/' + task['id'])['questions'][0]
        self.action(second, 'answer', 404, question_id=question['id'], answer='跨任务回答')
        revised = self.action(task, 'revise', requirements='需求变化')
        self.action(revised, 'answer', 404, question_id=question['id'], answer='跨版本回答')
        self.assertIsNone(self.expect('GET', '/api/dev/tasks/' + task['id'])['questions'][0]['answer'])

    def test_private_candidate_is_owner_visible_but_not_shared_or_chat_context(self):
        project, task = self.create()
        detail, artifact = self.candidate(project, task)
        run_id = artifact['content']['run_id']
        self.assertEqual('private', artifact['visibility'])
        run = self.expect('GET', '/api/dev/runs/' + run_id)
        self.assertIn(self.private, run['diff'])
        self.assertIn(self.private, run['summary'])
        self.expect('GET', '/api/dev/runs/' + run_id, status=403, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertNotIn(self.private, json.dumps(self.expect('GET', '/api/dev/workspace')))
        self.assertNotIn(str(self.repo), json.dumps(self.expect('GET', '/api/dev/workspace')))
        for role in ('product', 'tester', 'commander'):
            context = self.app.development.role_context(task['id'], role, self.members[role])
            self.assertNotIn(self.private, json.dumps(context))
            self.assertNotIn('example.py', json.dumps(context))
            self.assertNotIn(self.private, self.app.prompt(self.members[role]))
            self.assertNotIn(self.private, json.dumps(self.expect('GET', '/api/agents/' + self.members[role] + '/messages')))
        self.assertIn('value = 1', (self.repo / 'example.py').read_text())
        payload = {'task_id': task['id'], 'revision': 1, 'run_id': run_id}
        intent = self.expect('POST', '/api/owner/intents', {'action': 'dev-apply', 'payload': payload})
        preview = self.expect('GET', '/internal/owner/intents/' + intent['id'], headers={'X-Town-Owner': self.secret})
        confirm = {'id': intent['id'], 'digest': preview['digest'], 'approved': True}
        self.expect('POST', '/internal/owner/confirm', confirm, 403)
        self.assertIn('value = 1', (self.repo / 'example.py').read_text())
        self.expect('POST', '/internal/owner/confirm', confirm, headers={'X-Town-Owner': self.secret})
        self.assertIn('value = 2', (self.repo / 'example.py').read_text())
        self.assertEqual('user-owned-git', (self.repo / '.git' / 'HEAD').read_text())
        self.assertEqual('awaiting_commit', self.expect('GET', '/api/dev/tasks/' + task['id'])['status'])

    def test_disconnect_requires_same_origin_and_revokes_old_candidates(self):
        project, task = self.create()
        unused, artifact = self.candidate(project, task)
        path = '/api/dev/projects/' + project['id'] + '/disconnect'
        self.expect('POST', path, {}, 403, {'Origin': 'https://other.example'})
        self.expect('POST', path, {}, 403, {'X-Town-Client': None})
        self.expect('POST', path, {'allow_source': True}, 400)
        self.assertTrue(self.expect('GET', '/api/dev/connection/' + project['id'])['connected'])
        disconnected = self.expect('POST', path, {})
        self.assertFalse(disconnected['connected'])
        self.assertFalse(self.expect('GET', '/api/dev/connection/' + project['id'])['connected'])
        payload = {'task_id': task['id'], 'revision': 1, 'run_id': artifact['content']['run_id']}
        self.expect('POST', '/api/owner/intents', {'action': 'dev-apply', 'payload': payload}, 409)
        after = self.expect('GET', '/api/dev/tasks/' + task['id'])
        self.assertTrue(any(a['kind'] == 'requirement_analysis' for a in after['artifacts']))
        self.assertTrue(all(not a['effective'] for a in after['artifacts'] if a['kind'] == 'development_candidate'))
        self.assertIn('value = 1', (self.repo / 'example.py').read_text())
        self.expect('POST', '/api/dev/projects/missing/disconnect', {}, 404)

    def test_feedback_invalidates_pending_native_apply_through_http(self):
        project, task = self.create()
        unused, artifact = self.candidate(project, task)
        payload = {'task_id': task['id'], 'revision': 1, 'run_id': artifact['content']['run_id']}
        intent = self.expect('POST', '/api/owner/intents', {'action': 'dev-apply', 'payload': payload})
        preview = self.expect('GET', '/internal/owner/intents/' + intent['id'], headers={'X-Town-Owner': self.secret})
        self.action(task, 'feedback', content='先修正边界条件')
        self.expect('POST', '/internal/owner/confirm', {'id': intent['id'], 'digest': preview['digest'], 'approved': True}, 409, {'X-Town-Owner': self.secret})
        self.assertIn('value = 1', (self.repo / 'example.py').read_text())
        self.assertEqual('rework_required', self.expect('GET', '/api/dev/tasks/' + task['id'])['status'])


if __name__ == '__main__':
    unittest.main()
