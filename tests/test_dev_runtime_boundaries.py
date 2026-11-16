"""Authorization and filesystem regressions using isolated synthetic repositories."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_dev_runtime as runtime_fixtures
import town_dev_runtime
from town_store import TownError


class DevRuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Compose the shared setup without importing/inheriting its TestCase into
        # discovery, which would execute the entire runtime suite twice.
        self.fixture = runtime_fixtures.DevRuntimeTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_private_run_storage_rejects_replaced_ancestor_directories(self):
        runtime = self.fixture.app.dev_runtime
        for ancestor in (runtime.root, runtime.root.parent):
            with self.subTest(ancestor=ancestor.name):
                held = ancestor.with_name(ancestor.name + '-held')
                outside = self.fixture.root / ('outside-' + ancestor.name)
                outside.mkdir()
                redirected_runs = outside if ancestor == runtime.root else outside / 'runs'
                redirected_runs.mkdir(exist_ok=True)
                (redirected_runs / 'existing.json').write_text(json.dumps({'id': 'existing', 'private_source': 'outside sentinel'}))
                ancestor.rename(held)
                ancestor.symlink_to(outside, target_is_directory=True)
                try:
                    with self.assertRaises(TownError):
                        runtime._save_run({'id': 'new-private-run', 'private_source': 'must stay private'})
                    with self.assertRaises(TownError):
                        runtime._load_run('existing')
                    self.assertFalse((redirected_runs / 'new-private-run.json').exists())
                    self.assertEqual(['existing.json'], sorted(p.name for p in redirected_runs.iterdir()))
                finally:
                    ancestor.unlink()
                    held.rename(ancestor)

    def test_user_save_during_application_survives_and_stops_workflow(self):
        f = self.fixture
        f.connect()
        f.ready()
        candidate = f.develop()
        payload = f.apply_payload(candidate)
        intent = f.app.owner.issue({'action': 'dev-apply', 'payload': payload})
        preview = f.app.owner.preview(intent['id'])
        target = f.repo / 'example.py'
        user_content = '# user saved while applying\nvalue = 77\n'
        real_chmod = os.chmod
        raced = []

        def concurrent_editor(path, mode, *args, **kwargs):
            if Path(path).name.startswith('.town-edit-') and not raced:
                target.write_text(user_content)
                raced.append(True)
            return real_chmod(path, mode, *args, **kwargs)

        with patch('town_dev_runtime.os.chmod', side_effect=concurrent_editor):
            with self.assertRaises(TownError):
                f.app.owner.confirm({'id': intent['id'], 'digest': preview['digest'], 'approved': True})
        self.assertEqual([True], raced, 'Test must inject the user save after application starts.')
        self.assertEqual(user_content, target.read_text())
        record = f.app.dev_runtime._load_run(candidate['run_id'])
        self.assertEqual('apply_interrupted', record['status'])
        self.assertFalse(record.get('applied', False))
        self.assertNotEqual('awaiting_commit', f.app.development.task(f.task['id'])['status'])
        self.assertEqual('owned-by-user', (f.repo / '.git' / 'HEAD').read_text())

    def test_user_save_at_atomic_replacement_is_recovered(self):
        f = self.fixture
        f.connect()
        f.ready()
        candidate = f.develop()
        target = f.repo / 'example.py'
        user_content = '# saved after the last preflight check\nvalue = 88\n'
        native_replace = town_dev_runtime.replace_preserving_previous
        replacements = []

        def concurrent_editor(source, destination, backup):
            if not replacements:
                target.write_text(user_content)
            replacements.append(str(destination))
            return native_replace(source, destination, backup)

        with patch('town_dev_runtime.replace_preserving_previous', side_effect=concurrent_editor):
            with self.assertRaises(TownError):
                f.owner('dev-apply', f.apply_payload(candidate))
        self.assertEqual(2, len(replacements), 'The displaced user save must be restored after the initial atomic swap.')
        self.assertEqual(user_content, target.read_text())
        record = f.app.dev_runtime._load_run(candidate['run_id'])
        self.assertEqual('apply_interrupted', record['status'])
        self.assertFalse(record.get('applied', False))
        self.assertNotEqual('awaiting_commit', f.app.development.task(f.task['id'])['status'])
        self.assertTrue(any(Path(path).is_file() for path in record.get('recovery_files', [])))

    def test_reconnection_prevents_queued_source_from_being_dispatched(self):
        f = self.fixture
        f.connect()
        f.ready()
        replacement = f.root / 'new-repository'
        replacement.mkdir()
        (replacement / 'example.py').write_text('new repository content\n')
        generate = f.bridge.generate_result
        developer_calls = []

        def reconnect_before_dispatch(prompt, cancel, agent, options):
            if agent == f.members['developer']:
                developer_calls.append(prompt)
                if len(developer_calls) == 2:
                    f.owner('dev-connect', {'project_id': f.project['id'],
                            'repositories': [{'name': 'app', 'path': str(replacement)}], 'allow_source': True})
            # ScriptedBridge invokes the same before_dispatch gate as the real
            # provider; no network transport or model is involved.
            return generate(prompt, cancel, agent, options)

        f.bridge.generate_result = reconnect_before_dispatch
        before = len(f.bridge.calls)
        self.assertIsNone(f.develop([{'action': 'read', 'path': 'app/example.py'}, {'action': 'finish', 'summary': 'will not be sent'}]))
        self.assertEqual(2, len(developer_calls))
        self.assertIn('# user work', developer_calls[-1])
        self.assertEqual(before + 1, len(f.bridge.calls))
        self.assertNotIn('# user work', f.bridge.calls[-1][1])
        task = f.app.development.task(f.task['id'])
        self.assertEqual('failed', next(j for j in task['jobs'] if j['kind'] == 'develop')['status'])
        self.assertFalse(any(a['kind'] == 'development_candidate' for a in task['artifacts']))
        self.assertEqual('# user work\nvalue = 1\n', (f.repo / 'example.py').read_text())
        self.assertEqual('new repository content\n', (replacement / 'example.py').read_text())


if __name__ == '__main__':
    unittest.main()
