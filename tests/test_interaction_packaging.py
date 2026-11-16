"""Export real temporary private work; verify it cannot reach portable/public packages."""
import base64
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from server import TownApp
from test_interaction import AnalysisBridge
from test_windows_package import build_module
from town_skills import LocalSkills
from town_store import now, uid


ROOT = Path(__file__).resolve().parents[1]


class PrivateWorkPackagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.bridge = AnalysisBridge()
        self.app = TownApp(self.data, self.bridge, self.root / 'team', report_root=self.root / 'reports',
                           start_growth=False, owner_secret='c' * 64)
        self.bundles = build_module('state_bundle')

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def populate(self):
        document = self.app.documents.upload(dict(agent_id='resident-yanghui', name='private-requirement.md',
            content_base64=base64.b64encode(b'PRIVATE_UPLOADED_DOCUMENT_SENTINEL').decode()))
        self.app.interactions.create(dict(agent_id='resident-yanghui', goal='PRIVATE_ANALYSIS_GOAL_SENTINEL',
            attachment_ids=[document['id']], request_id=uid()))
        skill_root = self.root / 'private-guides'
        skill = skill_root / 'java-coding-standard'
        (skill / 'references').mkdir(parents=True)
        (skill / 'SKILL.md').write_text('# Private synthetic skill\nPRIVATE_SKILL_BODY_SENTINEL\n[reference](references/rules.md)', encoding='utf-8')
        (skill / 'references' / 'rules.md').write_text('PRIVATE_SKILL_REFERENCE_SENTINEL', encoding='utf-8')
        self.app.local_skills = LocalSkills(self.app, skill_root)
        imported = self.app.local_skills.import_local(dict(agent_id='resident-jiji', names=['java-coding-standard']))
        with self.app.db() as db:
            db.execute("INSERT INTO messages(id,agent_id,role,content,status,created_at) VALUES (?,?,'user',?,'complete',?)",
                       ('synthetic-message-id', 'resident-jiji', 'Use the selected synthetic skill', now()))
        self.app.local_skills.attach_message('synthetic-message-id', 'resident-jiji', [imported[0]['id']])
        project = self.app.development.create_project(dict(title='Private synthetic project', request_id=uid(),
            members=dict(product='resident-yanghui', developer='resident-jiji', tester='resident-alige', commander='linghang')))
        self.app.development.create_task(dict(project_id=project['id'], title='Private task',
            requirements='PRIVATE_DEVELOPMENT_REQUIREMENT_SENTINEL', request_id=uid()))
        repository = self.root / 'PRIVATE_SOURCE_DIRECTORY_SENTINEL'
        repository.mkdir()
        (repository / 'example.py').write_text('PRIVATE_SOURCE_BODY_SENTINEL', encoding='utf-8')
        intent = self.app.owner.issue(dict(action='dev-connect', payload=dict(project_id=project['id'],
            repositories=[dict(name='repo', path=str(repository))], allow_source=True)))
        preview = self.app.owner.preview(intent['id'])
        self.app.owner.confirm(dict(id=intent['id'], digest=preview['digest'], approved=True))
        with self.app.db() as db:
            db.execute('INSERT INTO v2_intents VALUES (?,?,?,?,?,?,?)',
                (uid(), 'dev-apply', json.dumps(dict(run_id='PRIVATE_APPLY_INTENT_SENTINEL')), '0' * 64, '1' * 64, 0, 'approved'))
        self.app.close()
        return project, repository

    def export(self, name='state.zip', strip=True):
        output = self.root / name
        args = ['export', '--data', str(self.data), '--out', str(output)]
        if strip:
            args.append('--strip-chat')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.bundles.main(args), 0)
        return output

    def payloads(self, archive):
        with zipfile.ZipFile(archive) as source:
            return {name: source.read(name) for name in source.namelist()}

    def assert_sanitized_database(self, payload):
        path = self.root / ('inspection-' + uid() + '.sqlite3')
        path.write_bytes(payload)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            for table in ('v2_documents', 'v2_local_skills', 'v2_message_local_skills'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_meta WHERE key GLOB 'dev-connection-*'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_intents WHERE action IN ('dev-connect','dev-apply')").fetchone()[0], 0)
            self.assertGreater(db.execute('SELECT COUNT(*) FROM agents').fetchone()[0], 0)

    def test_state_export_removes_actual_documents_sessions_skills_and_source_grants(self):
        project, repository = self.populate()
        original_database = (self.data / 'town.sqlite3').read_bytes()
        for strip in (False, True):
            with self.subTest(strip_chat=strip):
                payloads = self.payloads(self.export('state-%s.zip' % strip, strip))
                all_bytes = b''.join(payloads.values())
                for marker in (b'PRIVATE_UPLOADED_DOCUMENT_SENTINEL', b'PRIVATE_ANALYSIS_GOAL_SENTINEL',
                               b'PRIVATE_SKILL_BODY_SENTINEL', b'PRIVATE_SKILL_REFERENCE_SENTINEL',
                               b'PRIVATE_DEVELOPMENT_REQUIREMENT_SENTINEL', b'PRIVATE_SOURCE_DIRECTORY_SENTINEL',
                               b'PRIVATE_SOURCE_BODY_SENTINEL', b'PRIVATE_APPLY_INTENT_SENTINEL'):
                    self.assertNotIn(marker, all_bytes)
                self.assertFalse(any(any(part in {'documents', 'interactions', 'dev-workbench', 'local-skills'}
                                              for part in name.split('/')) for name in payloads))
                self.assert_sanitized_database(payloads['data/town.sqlite3'])
        self.assertEqual(original_database, (self.data / 'town.sqlite3').read_bytes())
        with closing(sqlite3.connect(self.data / 'town.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v2_documents').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v2_local_skills').fetchone()[0], 1)
            self.assertIn(str(repository), db.execute('SELECT value FROM v2_meta WHERE key=?', ('dev-connection-' + project['id'],)).fetchone()[0])

    def test_external_bundle_private_paths_and_indexes_cannot_reintroduce_local_grants(self):
        self.populate()
        public = self.payloads(self.export())
        # Case-insensitive Windows aliases must use the same database scrubber.
        public.pop('data/town.sqlite3')
        public['data/TOWN.SQLITE3'] = (self.data / 'town.sqlite3').read_bytes()
        injected = ('data/documents/original.md', 'data/./INTERACTIONS/session.json',
                    'data/workspaces/resident-jiji/LOCAL-SKILLS. /guide/SKILL.md',
                    'data\\dev-workbench\\workbench.sqlite3', 'data/documents:private/original.md')
        for name in injected:
            public[name] = b'PRIVATE_EXTERNAL_BUNDLE_SENTINEL'
        manifest = json.loads(public['manifest.json'])
        manifest['files'] = {name: dict(sha256=hashlib.sha256(value).hexdigest(), bytes=len(value))
                             for name, value in public.items() if name != 'manifest.json'}
        public['manifest.json'] = json.dumps(manifest).encode()
        external = self.root / 'external.zip'
        with zipfile.ZipFile(external, 'w') as archive:
            for name, value in public.items():
                archive.writestr(name, value)
        destination = self.root / 'restored'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.bundles.main(['restore', str(external), '--data', str(destination)]), 0)
        self.assert_sanitized_database((destination / 'town.sqlite3').read_bytes())
        for path in destination.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE_EXTERNAL_BUNDLE_SENTINEL', path.read_bytes())
                self.assertNotIn(b'PRIVATE_SOURCE_DIRECTORY_SENTINEL', path.read_bytes())
        self.assertFalse((destination / 'documents').exists())
        self.assertFalse((destination / 'dev-workbench').exists())

    def test_symbolic_alias_cannot_export_private_originals(self):
        self.populate()
        alias = self.data / 'portable-notes'
        try:
            alias.symlink_to(self.data / 'documents', target_is_directory=True)
        except OSError:
            self.skipTest('host does not permit symbolic links')
        payloads = self.payloads(self.export())
        self.assertFalse(any(name.startswith('data/portable-notes/') for name in payloads))
        self.assertNotIn(b'PRIVATE_UPLOADED_DOCUMENT_SENTINEL', b''.join(payloads.values()))

    def test_external_bundle_paths_cannot_write_outside_the_chosen_directory(self):
        self.populate()
        public = self.payloads(self.export())
        base_manifest = json.loads(public['manifest.json'])
        for index, name in enumerate(('data/../outside.txt', 'data/nested/../../outside.txt',
                                      'data\\..\\outside.txt', 'data/C:/outside.txt', '/absolute.txt')):
            with self.subTest(name=name):
                marker = b'UNAUTHORIZED_EXTERNAL_WRITE_SENTINEL'
                manifest = dict(base_manifest, files={name: dict(sha256=hashlib.sha256(marker).hexdigest(), bytes=len(marker))})
                external = self.root / ('invalid-%d.zip' % index)
                with zipfile.ZipFile(external, 'w') as archive:
                    archive.writestr('manifest.json', json.dumps(manifest))
                    archive.writestr(name, marker)
                target = self.root / ('invalid-restored-%d' % index)
                with self.assertRaisesRegex(SystemExit, '文件路径无效'), redirect_stdout(io.StringIO()):
                    self.bundles.main(['restore', str(external), '--data', str(target)])
                self.assertEqual(list(target.rglob('*')), [])
                self.assertFalse((self.root / 'outside.txt').exists())


class PublicSourcePackagingTest(unittest.TestCase):
    def test_source_archive_includes_working_feature_modules_and_excludes_private_work(self):
        exporter = build_module('export_source')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in exporter.public_files(ROOT):
                target = root / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            private = ('data/documents/original.md', 'data/interactions/sessions.sqlite3',
                       'data/dev-workbench/workbench.sqlite3', 'data/workspaces/resident-jiji/local-skills/guide/SKILL.md',
                       'state/town-state.zip', 'build/temporary.md', '.codex/personal.md',
                       '.agents/skills/private-guide/SKILL.md')
            for name in private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'PRIVATE_PUBLIC_EXPORT_SENTINEL')
            with redirect_stdout(io.StringIO()):
                archive_path = exporter.export_source(root)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                required = ('town_documents.py', 'town_interaction.py', 'town_dev_workbench.py', 'town_dev_runtime.py',
                            'provider_features.py', 'provider_streaming.py', 'provider_verification.py',
                            'requirements-runtime.txt', 'scripts/bundle_documents.py', 'web/resident-workspace.js',
                            'web/local-skills.js', '.agents/skills/starhaven-release/SKILL.md')
                self.assertTrue({'starhaven-town/' + name for name in required} <= names)
                for name in private:
                    self.assertNotIn('starhaven-town/' + name, names)
                self.assertEqual(archive.read('starhaven-town/requirements-runtime.txt'),
                                 (ROOT / 'requirements-runtime.txt').read_bytes().replace(b'\r\n', b'\n'))
                self.assertNotIn(b'PRIVATE_PUBLIC_EXPORT_SENTINEL', b''.join(archive.read(name) for name in names if name.endswith('.md')))


if __name__ == '__main__':
    unittest.main()
