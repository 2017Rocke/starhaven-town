"""Real local file changes with scripted model replies; no external requests."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from server import TownApp
from town_dev_runtime import read_source, source_path
from town_store import TownError, uid


class ScriptedBridge:
    def __init__(self):
        self.responses = []
        self.calls = []
        self.before_return = None
        self.model = 'test-model'

    def settings(self):
        return dict(mode='api', base_url='https://model.example/v1', model=self.model)

    def generate_result(self, prompt, cancel, agent, options):
        if options.get('before_dispatch'):
            options['before_dispatch']()
        self.calls.append((agent, prompt))
        result = self.responses.pop(0)
        if self.before_return:
            callback, self.before_return = self.before_return, None
            callback()
        return dict(text=json.dumps(result), call_id=uid())

    def close(self):
        pass


class DevRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repository'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        (self.repo / '.git' / 'HEAD').write_text('owned-by-user')
        (self.repo / 'example.py').write_text('# user work\nvalue = 1\n')
        self.bridge = ScriptedBridge()
        self.app = TownApp(self.root / 'town', self.bridge, self.root / 'team',
                           report_root=self.root / 'reports', owner_secret='a' * 64, start_growth=False)
        with self.app.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM agents ORDER BY id LIMIT 4')]
        self.members = dict(zip(('product', 'developer', 'tester', 'commander'), ids))
        self.project = self.app.development.create_project(dict(title='示例项目', members=self.members, request_id=uid()))
        self.task = self.app.development.create_task(dict(project_id=self.project['id'], title='修改值', requirements='将值改成 2，保留其他内容', request_id=uid()))

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def owner(self, action, payload):
        intent = self.app.owner.issue(dict(action=action, payload=payload))
        preview = self.app.owner.preview(intent['id'])
        return self.app.owner.confirm(dict(id=intent['id'], digest=preview['digest'], approved=True))

    def connect(self):
        self.owner('dev-connect', dict(project_id=self.project['id'], repositories=[dict(name='app', path=str(self.repo))], allow_source=True))

    def action(self, name, **fields):
        return self.app.development.action(self.task['id'], dict(action=name, revision=1, request_id=uid(), **fields))

    def ready(self):
        self.bridge.responses = [dict(summary='范围明确', scope=['修改值'], acceptance_criteria=['值为2'], questions=[])]
        self.action('analyze')
        self.app.development.run_once()
        self.action('confirm')

    def develop(self, responses=None):
        self.bridge.responses = responses or [dict(action='read', path='app/example.py'),
            dict(action='replace', path='app/example.py', old='value = 1', new='value = 2'),
            dict(action='finish', summary='修改候选已完成，功能自测未执行')]
        self.action('develop')
        self.app.development.run_once()
        task = self.app.development.task(self.task['id'])
        artifacts = [a for a in task['artifacts'] if a['kind'] == 'development_candidate']
        return artifacts[-1]['content'] if artifacts else None

    def apply_payload(self, result):
        return dict(task_id=self.task['id'], revision=1, run_id=result['run_id'])

    def test_disconnect_revokes_queued_work_and_candidates_without_editing_source(self):
        self.connect()
        self.ready()
        candidate = self.develop()
        self.action('develop')
        disconnected = self.app.dev_runtime.disconnect(self.project['id'])
        self.assertFalse(disconnected['connected'])
        task = self.app.development.task(self.task['id'])
        self.assertFalse(any(job['status'] in ('queued', 'running') for job in task['jobs']))
        self.assertFalse(any(a['effective'] for a in task['artifacts'] if a['kind'] == 'development_candidate'))
        self.assertEqual((self.repo / 'example.py').read_text(), '# user work\nvalue = 1\n')
        self.connect()
        with self.assertRaises(TownError):
            self.app.dev_runtime.normalize_apply(self.apply_payload(candidate))

    def test_disconnect_before_model_reply_cannot_publish_candidate(self):
        self.connect()
        self.ready()
        self.bridge.before_return = lambda: self.app.dev_runtime.disconnect(self.project['id'])
        result = self.develop([dict(action='finish', summary='不应生效')])
        self.assertIsNone(result)
        self.assertFalse(self.app.dev_runtime.connection(self.project['id'])['connected'])
        self.assertEqual((self.repo / 'example.py').read_text(), '# user work\nvalue = 1\n')

    def test_candidates_then_native_application_preserve_user_work_and_git(self):
        self.connect()
        self.ready()
        result = self.develop()
        self.assertEqual(result['status'], 'review')
        self.assertIn('value = 1', (self.repo / 'example.py').read_text())
        details = self.app.dev_runtime.run_details(result['run_id'])
        self.assertIn('+value = 2', details['diff'])
        self.assertEqual(details['checks'][0]['status'], 'not_run')
        self.owner('dev-apply', self.apply_payload(result))
        self.assertEqual((self.repo / 'example.py').read_text(), '# user work\nvalue = 2\n')
        self.assertEqual((self.repo / '.git' / 'HEAD').read_text(), 'owned-by-user')
        self.assertEqual(self.app.development.task(self.task['id'])['status'], 'awaiting_commit')
        with self.assertRaises(TownError):
            self.owner('dev-apply', self.apply_payload(result))

    def test_user_edit_after_candidate_blocks_application(self):
        self.connect(); self.ready()
        result = self.develop()
        (self.repo / 'example.py').write_text('# more user work\nvalue = 3\n')
        with self.assertRaises(TownError):
            self.owner('dev-apply', self.apply_payload(result))
        self.assertEqual((self.repo / 'example.py').read_text(), '# more user work\nvalue = 3\n')

    def test_feedback_invalidates_old_candidate_and_continues_private_edits(self):
        self.connect(); self.ready()
        old = self.develop()
        self.action('feedback', content='请将 2 改为 4')
        with self.assertRaises(TownError):
            self.owner('dev-apply', self.apply_payload(old))
        revised = self.develop([dict(action='read', path='app/example.py'),
            dict(action='replace', path='app/example.py', old='value = 2', new='value = 4'),
            dict(action='finish', summary='已按反馈修订候选')])
        self.assertNotEqual(old['run_id'], revised['run_id'])
        self.assertIn('请将 2 改为 4', self.bridge.calls[-1][1])
        self.owner('dev-apply', self.apply_payload(revised))
        self.assertIn('value = 4', (self.repo / 'example.py').read_text())

    def test_product_and_tester_contexts_do_not_receive_private_source(self):
        self.connect(); self.ready()
        self.develop()
        for role in ('product', 'tester', 'commander'):
            context = self.app.development.role_context(self.task['id'], role, self.members[role])
            serialized = json.dumps(context)
            self.assertNotIn('example.py', serialized)
            self.assertNotIn('value = 1', serialized)
            self.assertNotIn(str(self.repo), serialized)

    def test_no_grant_or_changed_provider_prevents_source_model_calls(self):
        self.ready()
        before = len(self.bridge.calls)
        self.assertIsNone(self.develop())
        self.assertEqual(len(self.bridge.calls), before)
        self.connect()
        self.bridge.model = 'different-model'
        self.assertIsNone(self.develop())
        self.assertEqual(len(self.bridge.calls), before)

    def test_cancel_during_model_call_discards_write_action(self):
        self.connect(); self.ready()
        self.bridge.before_return = lambda: self.action('cancel')
        self.assertIsNone(self.develop([dict(action='replace', path='app/example.py', old='value = 1', new='value = 99')]))
        self.assertEqual((self.repo / 'example.py').read_text(), '# user work\nvalue = 1\n')

    def test_reconnecting_repository_cancels_old_source_lease(self):
        self.connect(); self.ready()
        second = self.root / 'second-repository'
        second.mkdir()
        (second / 'example.py').write_text('value = 100\n')
        self.bridge.before_return = lambda: self.owner('dev-connect', dict(project_id=self.project['id'],
            repositories=[dict(name='app', path=str(second))], allow_source=True))
        before = len(self.bridge.calls)
        self.assertIsNone(self.develop([dict(action='read', path='app/example.py')]))
        self.assertEqual(len(self.bridge.calls), before + 1)
        self.assertNotIn('# user work', self.bridge.calls[-1][1])
        self.assertEqual((self.repo / 'example.py').read_text(), '# user work\nvalue = 1\n')

    def test_new_candidate_file_can_be_read_before_application(self):
        self.connect(); self.ready()
        result = self.develop([dict(action='create', path='app/new.py', content='x = 1'),
            dict(action='read', path='app/new.py'), dict(action='finish', summary='新增文件候选')])
        self.assertEqual(result['status'], 'review')
        self.assertFalse((self.repo / 'new.py').exists())
        self.owner('dev-apply', self.apply_payload(result))
        self.assertEqual((self.repo / 'new.py').read_text(), 'x = 1\n')

    def test_paths_links_credentials_and_git_are_denied(self):
        for path in ('../outside.py', '/etc/passwd', '.git/HEAD', '.codex/config.md', 'secrets.py', 'src/../../example.py', 'app\\example.py'):
            with self.subTest(path=path), self.assertRaises(TownError):
                source_path(self.repo, path, create=True)
        (self.repo / 'linked.py').symlink_to(self.repo / 'example.py')
        with self.assertRaises(TownError):
            read_source(self.repo, 'linked.py')
        os.link(self.repo / 'example.py', self.repo / 'hardlink.py')
        with self.assertRaises(TownError):
            read_source(self.repo, 'hardlink.py')

    def test_unapproved_model_actions_cannot_execute_commands(self):
        self.connect(); self.ready()
        self.assertIsNone(self.develop([dict(action='shell', command='git commit -am unapproved')]))
        self.assertEqual((self.repo / '.git' / 'HEAD').read_text(), 'owned-by-user')


if __name__ == '__main__':
    unittest.main()
