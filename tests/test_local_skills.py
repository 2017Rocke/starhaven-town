"""Synthetic private skill imports and per-message selection; no real home access."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from town_skills import LocalSkills
import town_skills
from town_store import TownError, digest, uid


DEVELOPER = "resident-dynamic-developer"
PROJECT_DEVELOPER = "resident-project-bound"
OTHER_DEVELOPER = "resident-other-developer"
PRODUCT = "resident-product"


class Projects:
    def __init__(self, root):
        self.path = root / "projects.sqlite3"
        with self.db() as db:
            db.execute("CREATE TABLE projects(members TEXT)")
            db.execute("INSERT INTO projects VALUES (?)", (json.dumps(dict(developer=PROJECT_DEVELOPER)),))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


class App:
    def __init__(self, root):
        self.data_dir = root / "data"
        self.data_dir.mkdir()
        self.lock = threading.RLock()
        self.development = Projects(root)
        with self.db() as db:
            db.execute("CREATE TABLE messages(id TEXT PRIMARY KEY,agent_id TEXT,role TEXT)")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.data_dir / "synthetic.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _agent(self, db, identity):
        roles = {DEVELOPER: "后端开发工程师", PROJECT_DEVELOPER: "小镇伙伴", OTHER_DEVELOPER: "developer", PRODUCT: "产品经理"}
        if identity not in roles:
            raise TownError(404, "没有找到这位居民")
        return dict(id=identity, role=roles[identity])


def source(root, name="java-coding-standard", content="SYNTHETIC_GUIDANCE_ONLY", extra=None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    files = {"SKILL.md": ("---\nname: " + name + "\ndescription: 合成开发指导\n---\n# 合成编码规范\n" + content).encode()}
    files.update(extra or {})
    for relative, raw in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    return folder


class LocalSkillsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = App(self.root)
        self.sources = self.root / "source-skills"
        self.sources.mkdir()
        self.skills = LocalSkills(self.app, self.sources)
        network = patch("socket.socket.connect", side_effect=AssertionError("No network in skill imports"))
        network.start()
        self.addCleanup(network.stop)

    def imported(self, name="java-coding-standard", agent=DEVELOPER):
        return self.skills.import_local(dict(names=[name], agent_id=agent))[0]

    def target(self, item):
        return self.app.data_dir / "workspaces" / item["agent_id"] / "local-skills" / item["name"] / item["hash"]

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(TownError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return str(caught.exception)

    def test_catalog_and_role_binding_use_development_capability_not_a_fixed_name(self):
        source(self.sources)
        source(self.sources, "unrelated-personal-skill")
        self.assertEqual([item["name"] for item in self.skills.catalog(DEVELOPER)], ["java-coding-standard"])
        self.assertEqual(self.skills.catalog(PROJECT_DEVELOPER)[0]["status"], "available")
        self.assertFalse(self.skills.workspace(PRODUCT)["allowed"])
        self.assert_error(403, self.skills.import_local, dict(names=["java-coding-standard"], agent_id=PRODUCT))
        self.assert_error(400, self.skills.import_local, dict(names=["unrelated-personal-skill"], agent_id=DEVELOPER))

    def test_generic_engineering_roles_cannot_import_or_select_development_skills(self):
        source(self.sources)
        item = self.imported()
        for role in ("测试工程师", "网络安全工程师", "工程师", "test engineer", "network security engineer", "engineer"):
            with self.subTest(role=role), patch.object(self.app, "_agent", return_value=dict(id=DEVELOPER, role=role)):
                self.assertFalse(self.skills.workspace(DEVELOPER)["allowed"])
                self.assert_error(403, self.skills.catalog, DEVELOPER)
                self.assert_error(403, self.skills.import_local, dict(names=["java-coding-standard"], agent_id=DEVELOPER))
                self.assert_error(403, self.skills.resolve, [item["id"]], DEVELOPER)
        for role in ("软件工程师", "后端工程师", "前端工程师", "研发工程师", "Software Engineer"):
            with self.subTest(role=role), patch.object(self.app, "_agent", return_value=dict(id=DEVELOPER, role=role)):
                self.assertTrue(self.skills.workspace(DEVELOPER)["allowed"])
        with patch.object(self.app, "_agent", return_value=dict(id=PROJECT_DEVELOPER, role="测试工程师")):
            self.assertTrue(self.skills.workspace(PROJECT_DEVELOPER)["allowed"])

    def test_import_preserves_resources_and_source_hash_without_executing_scripts(self):
        folder = source(self.sources, content="[规范](references/check.md)", extra={
            "references/check.md": "合成检查规则", "scripts/check.py": "raise RuntimeError('MUST_NOT_EXECUTE')",
            "assets/template.html": "<script>synthetic template</script>",
        })
        (folder / "scripts/check.py").chmod(0o755)
        item = self.imported()
        self.assertEqual(item["status"], "imported")
        self.assertEqual(item["source"]["path"], str(folder))
        self.assertEqual(item["source"]["hash"], item["hash"])
        for record in item["files"]:
            imported_path = self.target(item) / record["path"]
            self.assertEqual(imported_path.read_bytes(), (folder / record["path"]).read_bytes())
            self.assertEqual(digest(imported_path.read_bytes()), record["sha256"])
            self.assertEqual(imported_path.stat().st_mode & 0o111, 0)
        self.assertEqual(self.imported()["id"], item["id"])
        self.assertEqual(len(self.skills.list(DEVELOPER)), 1)
        self.assertEqual(self.skills.resolve([], DEVELOPER), [])
        resolved = self.skills.resolve([item["id"]], DEVELOPER)[0]
        self.assertEqual([value["path"] for value in resolved["resources"]], ["SKILL.md"])
        self.assertNotIn(str(folder), json.dumps(resolved))
        self.assertNotIn("MUST_NOT_EXECUTE", json.dumps(resolved))

    def test_private_environment_files_are_not_opened_copied_or_sent(self):
        folder = source(self.sources, "test-triage", content="使用 `assets/test-triage.env` 和 [指南](references/read.md)", extra={
            "assets/test-triage.env": "SYNTHETIC_PRIVATE_ENV_DO_NOT_READ",
            "assets/test-triage.env.example": "TEST_HOST=example.invalid\n",
            "references/read.md": "离线指导，脚本不自动执行。",
            "scripts/check.py": "print('synthetic-only')",
        })
        original = self.skills._read_file
        def guarded(path, *args, **kwargs):
            if Path(path) == folder / "assets/test-triage.env":
                raise AssertionError("Private environment file must never be opened")
            return original(path, *args, **kwargs)
        with patch.object(self.skills, "_read_file", side_effect=guarded):
            self.assertEqual(self.skills.catalog(DEVELOPER)[0]["status"], "available")
            item = self.imported("test-triage")
        self.assertEqual(item["status"], "partial_dependencies")
        self.assertIn("assets/test-triage.env", item["excluded"])
        self.assertFalse((self.target(item) / "assets/test-triage.env").exists())
        self.assertTrue((self.target(item) / "assets/test-triage.env.example").is_file())
        resolved = self.skills.resolve([item["id"]], DEVELOPER)[0]
        self.assertTrue(resolved["warnings"])
        self.assertNotIn("SYNTHETIC_PRIVATE_ENV_DO_NOT_READ", json.dumps(resolved))
        self.assert_error(404, self.skills.resolve, [item["id"]], DEVELOPER, files={item["id"]: ["assets/test-triage.env"]})

    def test_cross_skill_and_project_dependencies_are_explicit_not_fetched(self):
        source(self.sources, "fortress-selftest-playbook", content="[测试入口](../test-triage/SKILL.md) [发布指南](../../docs/RELEASING.md)")
        item = self.imported("fortress-selftest-playbook")
        self.assertEqual(item["status"], "partial_dependencies")
        self.assertEqual(len(item["missing_references"]), 2)
        result = self.skills.resolve([item["id"]], DEVELOPER)[0]
        self.assertEqual(result["missing_references"], item["missing_references"])
        self.assertFalse((self.target(item) / "test-triage").exists())

    def test_new_source_content_creates_new_version_without_changing_old_selection(self):
        folder = source(self.sources, content="VERSION_ONE")
        first = self.imported()
        source(self.sources, content="VERSION_TWO")
        second = self.imported()
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["hash"], second["hash"])
        self.assertIn("VERSION_ONE", self.skills.resolve([first["id"]], DEVELOPER)[0]["resources"][0]["text"])
        self.assertIn("VERSION_TWO", self.skills.resolve([second["id"]], DEVELOPER)[0]["resources"][0]["text"])
        self.assertIn("VERSION_TWO", (folder / "SKILL.md").read_text())

    def test_owner_scope_metadata_and_resource_tampering_fail_closed(self):
        source(self.sources)
        item = self.imported()
        self.assert_error(403, self.skills.read, item["id"], OTHER_DEVELOPER)
        (self.target(item) / "SKILL.md").write_text("changed")
        self.assert_error(409, self.skills.resolve, [item["id"]], DEVELOPER)
        manifest = self.target(item) / "local-manifest.json"
        value = json.loads(manifest.read_text())
        value["agent_id"] = OTHER_DEVELOPER
        manifest.write_text(json.dumps(value))
        self.assert_error(409, self.skills.read, item["id"], DEVELOPER)

    def test_source_and_destination_links_hardlinks_and_resource_escape_are_rejected(self):
        folder = source(self.sources)
        outside = self.root / "outside.md"
        outside.write_text("SYNTHETIC_OUTSIDE_DATA")
        link = folder / "linked.md"
        link.symlink_to(outside)
        self.assertEqual(self.skills.catalog(DEVELOPER)[0]["status"], "blocked")
        self.assert_error(503, self.imported)
        link.unlink()
        os.link(outside, link)
        self.assert_error(503, self.imported)
        link.unlink()
        item = self.imported()
        self.assert_error(400, self.skills.resolve, [item["id"]], DEVELOPER, files={item["id"]: ["../outside.md"]})
        (self.target(item) / "SKILL.md").unlink()
        (self.target(item) / "SKILL.md").symlink_to(outside)
        self.assert_error(503, self.skills.resolve, [item["id"]], DEVELOPER)
        self.assertEqual(outside.read_text(), "SYNTHETIC_OUTSIDE_DATA")

    def test_import_and_context_resource_limits_are_enforced(self):
        source(self.sources, extra={"references/large.md": "x" * 200})
        with patch.object(town_skills, "LOCAL_SKILL_MAX_BYTES", 100):
            self.assert_error(413, self.imported)
        with patch.object(town_skills, "LOCAL_SKILL_MAX_FILES", 1):
            self.assert_error(413, self.imported)
        item = self.imported()
        with patch.object(town_skills, "LOCAL_SKILL_CONTEXT_BYTES", 10):
            self.assert_error(413, self.skills.resolve, [item["id"]], DEVELOPER)
        self.assert_error(400, self.skills.resolve, [item["id"]] * 2, DEVELOPER)
        self.assert_error(400, self.skills.resolve, [uid() for _ in range(4)], DEVELOPER)
        selected = self.skills.resolve([item["id"]], DEVELOPER, files={item["id"]: ["references/large.md"]})
        self.assertEqual(selected[0]["resources"][0]["text"], "x" * 200)

    def test_message_binding_is_explicit_per_turn_and_cannot_change_after_creation(self):
        source(self.sources)
        item = self.imported()
        first, second, assistant = uid(), uid(), uid()
        with self.app.db() as db:
            for identity, role in ((first, "user"), (second, "user"), (assistant, "assistant")):
                db.execute("INSERT INTO messages VALUES (?,?,?)", (identity, DEVELOPER, role))
            self.skills.attach_message(first, DEVELOPER, [item["id"]], db)
            self.skills.attach_message(second, DEVELOPER, [], db)
        self.assertEqual(len(self.skills.for_message(first, DEVELOPER)), 1)
        self.assertEqual(self.skills.for_message(second, DEVELOPER), [])
        self.assert_error(409, self.skills.attach_message, first, DEVELOPER, [])
        self.assert_error(403, self.skills.attach_message, assistant, DEVELOPER, [item["id"]])
        self.assert_error(403, self.skills.for_message, first, OTHER_DEVELOPER)

    def test_selected_reference_paths_and_hashes_are_pinned_to_the_message(self):
        source(self.sources, extra={"references/check.md": "SYNTHETIC_SELECTED_REFERENCE"})
        item = self.imported()
        message_id = uid()
        with self.app.db() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?)", (message_id, DEVELOPER, "user"))
        files = {item["id"]: ["SKILL.md", "references/check.md"]}
        self.skills.attach_message(message_id, DEVELOPER, [item["id"]], files=files)
        saved = self.skills.message_selection(message_id, DEVELOPER)
        self.assertEqual(saved["files"], files)
        self.assertEqual(saved["hashes"][item["id"]]["resources"]["references/check.md"], digest(b"SYNTHETIC_SELECTED_REFERENCE"))
        self.assertIn("SYNTHETIC_SELECTED_REFERENCE", json.dumps(self.skills.for_message(message_id, DEVELOPER)))
        self.assert_error(409, self.skills.attach_message, message_id, DEVELOPER, [item["id"]])
        (self.target(item) / "references/check.md").write_text("changed after selection")
        self.assert_error(409, self.skills.for_message, message_id, DEVELOPER)


from test_growth_v2 import GrowthFixture
from server import APIError


class LocalSkillsChatTest(GrowthFixture):
    def setUp(self):
        super().setUp()
        self.source_root = self.root.resolve() / "synthetic-skills"
        source(self.source_root, content="SYNTHETIC_SELECTED_SKILL_GUIDANCE",
               extra={"references/detail.md": "SYNTHETIC_EXPLICIT_REFERENCE_DETAIL"})
        self.app.local_skills = LocalSkills(self.app, self.source_root)
        self.agent_id = self.app.add_agent(dict(name="合成吉吉", role="后端开发工程师"))["agent"]["id"]
        self.skill = self.app.local_skills.import_local(dict(names=["java-coding-standard"], agent_id=self.agent_id))[0]

    def chat(self, request):
        result = self.app.chat(request)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.app.job(result["job_id"])
            if job["status"] not in ("queued", "running"):
                self.assertEqual(job["status"], "completed", job)
                return result
            time.sleep(.01)
        self.fail("Synthetic chat did not finish")

    def test_selected_skill_enters_only_current_chat_and_keeps_manual_boundaries(self):
        first = dict(agent_id=self.agent_id, message="按所选规范帮我分析合成代码", request_id=uid(), local_skill_ids=[self.skill["id"]])
        original = self.chat(first)
        first_body = json.loads(self.opener.requests[-1].data)
        prompt = first_body["messages"][-1]["content"]
        self.assertIn("SYNTHETIC_SELECTED_SKILL_GUIDANCE", prompt)
        self.assertIn("不能授予工具", prompt)
        self.assertIn("用户手动检查", prompt)
        self.assertNotIn(str(self.source_root), prompt)
        self.assertEqual(self.app.chat(first), original)
        with self.assertRaises(APIError) as caught:
            self.app.chat(dict(first, local_skill_ids=[]))
        self.assertEqual(caught.exception.code, 409)
        self.chat(dict(agent_id=self.agent_id, message="这一轮只讨论合成需求", request_id=uid()))
        prompt = json.loads(self.opener.requests[-1].data)["messages"][-1]["content"]
        self.assertNotIn("SYNTHETIC_SELECTED_SKILL_GUIDANCE", prompt)
        self.assertNotIn("<local_skill_guides>", prompt)

    def test_unowned_or_tampered_skill_never_dispatches_chat(self):
        with self.assertRaises(TownError):
            self.app.chat(dict(agent_id="linghang", message="合成需求", request_id=uid(), local_skill_ids=[self.skill["id"]]))
        target = self.app.data_dir / "workspaces" / self.agent_id / "local-skills" / self.skill["name"] / self.skill["hash"] / "SKILL.md"
        target.write_text("tampered")
        with self.assertRaises(TownError):
            self.app.chat(dict(agent_id=self.agent_id, message="合成需求", request_id=uid(), local_skill_ids=[self.skill["id"]]))
        self.assertEqual(self.opener.requests, [])

    def test_reference_files_are_explicit_and_request_ids_cannot_change_them(self):
        selected = {self.skill["id"]: ["references/detail.md", "SKILL.md"]}
        request = dict(agent_id=self.agent_id, message="按明确选择的详细规范说明", request_id=uid(),
                       local_skill_ids=[self.skill["id"]], local_skill_files=selected)
        original = self.chat(request)
        prompt = json.loads(self.opener.requests[-1].data)["messages"][-1]["content"]
        self.assertIn("SYNTHETIC_EXPLICIT_REFERENCE_DETAIL", prompt)
        reordered = dict(request, local_skill_files={self.skill["id"]: list(reversed(selected[self.skill["id"]]))})
        self.assertEqual(self.app.chat(reordered), original)
        with self.assertRaises(APIError) as caught:
            self.app.chat(dict(request, local_skill_files={self.skill["id"]: ["SKILL.md"]}))
        self.assertEqual(caught.exception.code, 409)
        target = self.app.data_dir / "workspaces" / self.agent_id / "local-skills" / self.skill["name"] / self.skill["hash"] / "references/detail.md"
        target.write_text("changed")
        before = len(self.opener.requests)
        with self.assertRaises(TownError) as caught:
            self.app.chat(dict(request, request_id=uid()))
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(self.opener.requests), before)


if __name__ == "__main__":
    unittest.main()
