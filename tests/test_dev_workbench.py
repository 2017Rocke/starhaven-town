import contextlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from town_dev_workbench import DevWorkbench
from town_store import TownError, uid


class FakeApp:
    def __init__(self, root):
        self.data_dir = Path(root)
        self.db_path = self.data_dir / 'town.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE agents(id TEXT PRIMARY KEY,name TEXT NOT NULL)')
            db.executemany('INSERT INTO agents VALUES(?,?)', [('p', '同名'), ('d', '同名'), ('t', '测试'), ('c', '领航')])

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()


def analysis(questions=None):
    return {'summary': '需求已整理', 'scope': ['本次范围'], 'acceptance_criteria': ['保存成功'], 'questions': questions or []}


class DevWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = FakeApp(self.temp.name)
        self.calls = []
        self.result = analysis()
        self.workbench = DevWorkbench(self.app, self.runner, start_scheduler=False)
        self.addCleanup(self.workbench.close)
        self.project = self.workbench.create_project({'title': '演示项目', 'members': {'product': 'p', 'developer': 'd', 'tester': 't', 'commander': 'c'}, 'request_id': uid()})

    def runner(self, kind, context, cancel):
        self.calls.append((kind, context, cancel))
        return self.result

    def new_task(self):
        return self.workbench.create_task({'project_id': self.project['id'], 'title': '保存需求', 'requirements': '保存后可以读取', 'request_id': uid()})

    def action(self, task, action, **kwargs):
        return self.workbench.action(task['id'], {'action': action, 'revision': task['revision'], 'request_id': uid(), **kwargs})

    def active_task(self):
        task = self.new_task()
        self.action(task, 'analyze')
        self.workbench.run_once()
        return self.action(task, 'confirm')

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(TownError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)

    def test_independent_db_and_explicit_roles(self):
        self.assertEqual(self.app.data_dir / 'dev-workbench' / 'workbench.sqlite3', self.workbench.db_path)
        with self.app.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone())
        self.assertEqual('p', self.workbench.project(self.project['id'])['members']['product'])
        for members in ({'product': '同名', 'developer': 'd', 'tester': 't', 'commander': 'c'},
                        {'product': 'd', 'developer': 'd', 'tester': 't', 'commander': 'c'}, {'product': 'p'}):
            self.assert_code(400, self.workbench.create_project, {'title': '项目', 'members': members, 'request_id': uid()})
        self.assertFalse(self.workbench.workspace()['capabilities']['deployment'])
        self.assertFalse(self.workbench.workspace()['capabilities']['acceptance'])

    def test_state_machine_questions_confirmation_and_test_plan(self):
        task = self.new_task()
        self.assert_code(409, self.action, task, 'confirm')
        self.result = analysis([{'text': '保存多久？', 'blocking': True}])
        queued = self.action(task, 'analyze')
        self.assertEqual('queued', queued['run_status'])
        self.assertTrue(self.workbench.run_once())
        task = self.workbench.task(task['id'])
        self.assertEqual('awaiting_requirement_confirmation', task['status'])
        self.assert_code(409, self.action, task, 'confirm')
        question = task['questions'][0]
        task = self.action(task, 'answer', question_id=question['id'], answer='30天')
        task = self.action(task, 'confirm')
        self.assertEqual(1, task['active_revision'])
        self.assertEqual('developing', task['status'])
        self.assertEqual(1, len(task['jobs']))  # Confirmation does not start development.
        self.result = {'summary': '验证保存', 'cases': [{'title': '保存成功', 'steps': ['保存', '读取'], 'expected': '相同内容'}], 'execution_status': 'passed'}
        self.action(task, 'plan_tests')
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        plan = next(a for a in task['artifacts'] if a['kind'] == 'test_plan')
        self.assertEqual('not_run', plan['content']['execution_status'])
        self.assertEqual('developing', task['status'])
        self.assertIn('尚未执行', task['blocked_reason'])
        self.assertEqual('30天', self.calls[-1][1]['answers'][0]['answer'])

    def test_request_idempotency_and_payload_conflict(self):
        payload = {'project_id': self.project['id'], 'title': '需求', 'requirements': '正文', 'request_id': uid()}
        first = self.workbench.create_task(payload)
        self.assertEqual(first, self.workbench.create_task(payload))
        self.assert_code(409, self.workbench.create_task, {**payload, 'title': '被更改'})
        action = {'action': 'analyze', 'revision': 1, 'request_id': uid()}
        result = self.workbench.action(first['id'], action)
        self.workbench.run_once()
        self.assertEqual(result, self.workbench.action(first['id'], action))
        self.assertEqual(1, len(self.workbench.task(first['id'])['jobs']))
        self.assert_code(409, self.workbench.action, first['id'], {**action, 'action': 'confirm'})

    def test_cross_task_question_and_old_revision_answer_rejected(self):
        first, second = self.new_task(), self.new_task()
        self.result = analysis(['问题？'])
        self.action(first, 'analyze')
        self.workbench.run_once()
        first = self.workbench.task(first['id'])
        question = first['questions'][0]['id']
        self.assert_code(404, self.action, second, 'answer', question_id=question, answer='恶意跨任务答复')
        updated = self.action(first, 'revise', requirements='新的业务范围')
        self.assert_code(409, self.action, first, 'answer', question_id=question, answer='旧版本')
        self.assert_code(404, self.action, updated, 'answer', question_id=question, answer='新版本不能引用旧问题')
        self.assertIsNone(self.workbench.task(first['id'])['questions'][0]['answer'])

    def test_draft_does_not_replace_effective_revision(self):
        task = self.active_task()
        revised = self.action(task, 'revise', requirements='新的范围')
        self.assertEqual(2, revised['revision'])
        self.assertEqual(1, revised['active_revision'])
        self.assertEqual('developing', revised['status'])
        self.assertTrue(self.workbench.is_current(task['id'], 1))
        self.assert_code(409, self.action, revised, 'develop')
        self.action(revised, 'analyze')
        self.workbench.run_once()
        active = self.action(revised, 'confirm')
        self.assertEqual(2, active['active_revision'])
        self.assertFalse(self.workbench.is_current(task['id'], 1))
        self.assertTrue(all(not a['effective'] for a in active['artifacts'] if a['revision'] == 1))

    def test_late_product_result_cannot_change_new_draft(self):
        started, release = threading.Event(), threading.Event()
        def runner(kind, context, cancel):
            started.set()
            release.wait(5)
            return analysis(['旧问题'])
        self.workbench.runner = runner
        task = self.new_task()
        self.action(task, 'analyze')
        worker = threading.Thread(target=self.workbench.run_once)
        worker.start()
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        revised = self.action(task, 'revise', requirements='新版本')
        release.set()
        worker.join(3)
        current = self.workbench.task(task['id'])
        self.assertEqual(2, revised['revision'])
        self.assertEqual('draft', current['status'])
        self.assertEqual('superseded', current['jobs'][0]['status'])
        self.assertEqual([], current['questions'])
        self.assertEqual([], current['artifacts'])

    def test_new_activation_revokes_developer_lease_and_late_result(self):
        task = self.active_task()
        started, release = threading.Event(), threading.Event()
        def runner(kind, context, cancel):
            if kind == 'develop':
                self.assertTrue(self.workbench.is_current(task['id'], 1, context['job_id']))
                started.set()
                release.wait(5)
                return {'summary': '旧源码答复', 'status': 'accepted'}
            return analysis()
        self.workbench.runner = runner
        self.action(task, 'develop')
        worker = threading.Thread(target=self.workbench.run_once)
        worker.start()
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        revised = self.action(task, 'revise', requirements='新要求')
        self.assertTrue(self.workbench.is_current(task['id'], 1))
        self.action(revised, 'analyze')
        self.workbench.run_once()
        self.action(revised, 'confirm')
        release.set()
        worker.join(3)
        current = self.workbench.task(task['id'])
        self.assertEqual(2, current['active_revision'])
        self.assertFalse(any(a['kind'] == 'development_candidate' for a in current['artifacts']))
        self.assertEqual('superseded', next(j for j in current['jobs'] if j['kind'] == 'develop')['status'])

    def test_private_source_and_questions_never_reach_other_roles(self):
        task = self.active_task()
        secret = 'PRIVATE_SOURCE_/repositories/private.py/token-123'
        self.result = {'summary': secret, 'code': secret, 'question': secret, 'status': 'accepted', 'checks': [{'status': 'passed'}]}
        self.action(task, 'develop')
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        self.assertNotEqual('accepted', task['status'])
        question = next(q for q in task['questions'] if q['role'] == 'developer')
        self.assert_code(409, self.action, task, 'develop')
        task = self.action(task, 'answer', question_id=question['id'], answer=secret)
        for role, agent_id in [('product', 'p'), ('tester', 't'), ('commander', 'c')]:
            self.assertNotIn(secret, json.dumps(self.workbench.role_context(task['id'], role, agent_id)))
        self.assertIn(secret, json.dumps(self.workbench.role_context(task['id'], 'developer', 'd')))
        self.assertNotIn(secret, json.dumps(self.workbench.workspace()))
        self.assert_code(403, self.workbench.role_context, task['id'], 'developer', 'p')
        self.assert_code(403, self.workbench.role_context, task['id'], 'product', '同名')
        self.assert_code(400, self.action, task, 'accepted')
        self.assertEqual('private', next(a for a in task['artifacts'] if a['kind'] == 'development_candidate')['visibility'])

    def test_feedback_invalidates_running_work_and_is_private(self):
        task = self.active_task()
        self.action(task, 'develop')
        task = self.action(task, 'feedback', content='不要泄漏 /private/source')
        self.assertEqual('rework_required', task['status'])
        self.assertEqual('superseded', task['jobs'][-1]['status'])
        self.assertFalse(self.workbench.run_once())
        self.assertNotIn('/private/source', json.dumps(self.workbench.role_context(task['id'], 'tester', 't')))
        self.assertIn('/private/source', json.dumps(self.workbench.role_context(task['id'], 'developer', 'd')))

    def test_deferred_start_binds_app_before_restored_job_and_is_idempotent(self):
        task = self.new_task()
        self.action(task, 'analyze')
        self.workbench.close()
        observed = []
        called = threading.Event()
        def runner(kind, context, cancel):
            observed.append(self.app.development)
            called.set()
            return analysis()
        restored = DevWorkbench(self.app, runner, start_scheduler=False)
        self.addCleanup(restored.close)
        self.assertIsNone(restored.scheduler)
        self.assertEqual([], observed)
        self.app.development = restored
        self.assertTrue(restored.start())
        scheduler = restored.scheduler
        self.assertFalse(restored.start())
        self.assertIs(scheduler, restored.scheduler)
        self.assertTrue(called.wait(2))
        self.assertEqual([restored], observed)
        restored.close()
        self.assertFalse(restored.start())
        self.assertIs(scheduler, restored.scheduler)

    def test_restart_marks_running_interrupted_without_reexecuting(self):
        task = self.new_task()
        self.action(task, 'analyze')
        self.workbench.close()
        with self.workbench.db() as db:
            db.execute("UPDATE jobs SET status='running'")
            db.execute("UPDATE tasks SET run_status='running'")
        restarted = DevWorkbench(self.app, self.runner, start_scheduler=False)
        self.addCleanup(restarted.close)
        current = restarted.task(task['id'])
        self.assertEqual('interrupted', current['run_status'])
        self.assertEqual('interrupted', current['jobs'][0]['status'])
        self.assertFalse(restarted.run_once())
        self.assertEqual([], self.calls)
        restarted.action(task['id'], {'action': 'retry', 'revision': 1, 'request_id': uid(), 'job_id': current['jobs'][0]['id']})
        restarted.run_once()
        self.assertEqual(1, len(self.calls))

    def test_cancel_and_close_ignore_late_output(self):
        for operation in ('cancel', 'close'):
            with self.subTest(operation=operation):
                started, release = threading.Event(), threading.Event()
                def runner(kind, context, cancel):
                    started.set()
                    release.wait(5)
                    self.assertTrue(cancel.is_set())
                    return analysis()
                self.workbench.runner = runner
                task = self.new_task()
                self.action(task, 'analyze')
                worker = threading.Thread(target=self.workbench.run_once)
                worker.start()
                self.assertTrue(started.wait(2))
                if operation == 'cancel':
                    self.action(task, 'cancel')
                else:
                    self.workbench.close()
                release.set()
                worker.join(3)
                current = self.workbench.task(task['id'])
                self.assertEqual([], current['artifacts'])
                self.assertEqual('cancelled' if operation == 'cancel' else 'interrupted', current['jobs'][0]['status'])

    def test_missing_runner_blocked_and_failure_retry(self):
        task = self.new_task()
        self.workbench.runner = None
        self.action(task, 'analyze')
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        self.assertEqual('blocked', task['jobs'][0]['status'])
        self.assertFalse(self.workbench.run_once())
        self.workbench.runner = lambda *args: (_ for _ in ()).throw(RuntimeError('/private/connection failed'))
        self.action(task, 'retry', job_id=task['jobs'][0]['id'])
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        self.assertEqual('failed', task['jobs'][-1]['status'])
        self.assertNotIn('/private/connection', json.dumps(self.workbench.workspace()))
        self.workbench.runner = self.runner
        self.action(task, 'retry', job_id=task['jobs'][-1]['id'])
        self.workbench.run_once()
        self.assertEqual('awaiting_requirement_confirmation', self.workbench.task(task['id'])['status'])

    def test_trusted_apply_and_invalidations(self):
        for invalidation in ('feedback', 'new_job', 'draft', 'activate', 'cancel'):
            with self.subTest(invalidation=invalidation):
                self.result = analysis()
                task = self.active_task()
                self.result = {'summary': '本地候选', 'status': 'review', 'run_id': uid()}
                run_id = self.result['run_id']
                self.action(task, 'develop')
                self.workbench.run_once()
                task = self.workbench.task(task['id'])
                job_id = next(j['id'] for j in task['jobs'] if j['kind'] == 'develop')
                self.assertTrue(self.workbench.can_apply(task['id'], 1, job_id, run_id))
                self.assertFalse(self.workbench.can_apply(task['id'], 1, job_id, 'other-run'))
                with self.workbench.lock:
                    applied = self.workbench.mark_applied(task['id'], 1, job_id, run_id)
                self.assertEqual('awaiting_commit', applied['status'])
                self.workbench.mark_applied(task['id'], 1, job_id, run_id)
                self.assertEqual(1, len([e for e in self.workbench.task(task['id'])['events'] if e['kind'] == 'development.applied']))
                if invalidation == 'feedback':
                    self.action(task, 'feedback', content='修复边界条件')
                elif invalidation == 'new_job':
                    self.action(task, 'develop')
                elif invalidation in ('draft', 'activate'):
                    task = self.action(task, 'revise', requirements='新要求')
                    if invalidation == 'activate':
                        self.result = analysis()
                        self.action(task, 'analyze')
                        # Drain any earlier queued task from a prior subtest.
                        while self.workbench.run_once():
                            pass
                        self.action(task, 'confirm')
                else:
                    self.action(task, 'cancel')
                self.assertFalse(self.workbench.can_apply(task['id'], 1, job_id, run_id))
                self.assert_code(409, self.workbench.mark_applied, task['id'], 1, job_id, run_id)
                # Keep the next subtest isolated from this queued developer batch.
                if invalidation == 'new_job':
                    self.action(task, 'cancel')

    def test_clarification_candidate_cannot_be_applied(self):
        task = self.active_task()
        self.result = {'summary': '需要澄清', 'status': 'clarifying', 'run_id': 'private-run', 'questions': [{'text': '私有问题'}]}
        self.action(task, 'develop')
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        job = next(j for j in task['jobs'] if j['kind'] == 'develop')
        self.assertFalse(self.workbench.can_apply(task['id'], 1, job['id'], 'private-run'))
        question = next(q for q in task['questions'] if q['role'] == 'developer')
        self.action(task, 'answer', question_id=question['id'], answer='答案')
        self.assertFalse(self.workbench.can_apply(task['id'], 1, job['id'], 'private-run'))

    def test_malformed_result_fails_without_partial_artifacts(self):
        task = self.new_task()
        self.result = {'summary': '摘要', 'questions': [{'text': '问题', 'blocking': 'false'}]}
        self.action(task, 'analyze')
        self.workbench.run_once()
        task = self.workbench.task(task['id'])
        self.assertEqual('failed', task['run_status'])
        self.assertEqual([], task['artifacts'])
        self.assertEqual([], task['questions'])

    def test_duplicate_analyze_and_invalid_revision_rejected(self):
        task = self.new_task()
        self.action(task, 'analyze')
        self.action(task, 'analyze')
        self.assertEqual(1, len(self.workbench.task(task['id'])['jobs']))
        self.workbench.run_once()
        self.assert_code(409, self.action, task, 'analyze')
        self.assert_code(409, self.workbench.action, task['id'], {'action': 'confirm', 'revision': True, 'request_id': uid()})

    def test_symlinks_and_hardlinks_rejected(self):
        for target in ('directory', 'database', 'wal', 'hardlink'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as root:
                app = FakeApp(root)
                external = Path(root) / 'external'
                external.mkdir()
                directory = Path(root) / 'dev-workbench'
                if target == 'directory':
                    directory.symlink_to(external, target_is_directory=True)
                else:
                    directory.mkdir()
                    private = external / 'private.sqlite3'
                    private.touch()
                    db_path = directory / ('workbench.sqlite3-wal' if target == 'wal' else 'workbench.sqlite3')
                    if target == 'hardlink':
                        os.link(private, db_path)
                    else:
                        db_path.symlink_to(private)
                self.assert_code(503, DevWorkbench, app, start_scheduler=False)

    def test_database_links_rechecked_after_startup(self):
        private = self.app.data_dir / 'private'
        private.touch()
        Path(str(self.workbench.db_path) + '-wal').symlink_to(private)
        self.assert_code(503, self.workbench.workspace)
        Path(str(self.workbench.db_path) + '-wal').unlink()


if __name__ == '__main__':
    unittest.main()
