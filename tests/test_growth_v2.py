"""V2 integration and failure-path tests. All model/network responses are local fakes."""
import concurrent.futures
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from provider_bridge import ProviderBridge
from server import TownApp, create_server
from town_store import TownError, digest, event, get_setting, pack, setting, uid, verify_events
from town_growth import grade, objective
from test_provider_bridge import MemoryCredentials, MockOpener

ENDPOINT = "https://api.openai.com/v1"


class GrowthFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.opener = MockOpener()
        self.bridge = ProviderBridge(self.root / "runtime", self.root / "data", MemoryCredentials(), lambda: self.opener)
        self.bridge.save_settings(dict(mode="api", base_url=ENDPOINT, model="gpt-4o-mini"))
        self.app = TownApp(self.root / "data", self.bridge, self.root / "team", report_root=self.root / "reports", owner_secret="a" * 64, start_growth=False)

    def tearDown(self):
        if self.opener.wait:
            self.opener.wait.set()
        self.app.close()
        self.temp.cleanup()

    def approve(self, action, payload):
        intent = self.app.owner.issue(dict(action=action, payload=payload))
        preview = self.app.owner.preview(intent["id"])
        return self.app.owner.confirm(dict(id=intent["id"], digest=preview["digest"], approved=True))

    def configure(self, **updates):
        policy = dict(enabled=True, strict=True, residents=["linghang"], endpoints=[dict(base_url=ENDPOINT, model="gpt-4o-mini")], daily_tokens=100000, daily_calls=12)
        policy.update(updates)
        self.approve("policy", policy)
        return self.app.budget.config()

    def artifact(self, content="可核验的工作结果。", **updates):
        args = dict(owner="linghang", category="results", kind="note", title="工作结果", content=content, source_type="training", source_id=uid())
        args.update(updates)
        return self.app.artifacts.create(**args)

    def allow(self, artifact, readers=None, destinations=None, deliverable=True):
        self.approve("artifact-policy", dict(artifact_id=artifact["id"], readers=readers or ["linghang"], destinations=destinations or ["local", ENDPOINT], deliverable=deliverable))

    def call_context(self, **options):
        context = dict(agent_id="linghang", cancel=threading.Event(), options=dict(controlled=True, **options))
        payload = pack(dict(messages=[dict(role="user", content="hello")], max_tokens=1024)).encode()
        self.app.budget.admit(dict(base_url=ENDPOINT, model="gpt-4o-mini"), payload, context)
        return context

    def calls(self):
        with self.app.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM v2_calls ORDER BY rowid")]


class ArtifactTest(GrowthFixture):
    def test_alias_request_ids_cannot_be_reused_after_source_dedup(self):
        first = self.artifact(request_id="first", source_id="same")
        second = self.artifact(request_id="alias", source_id="same")
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(TownError):
            self.artifact(content="different", request_id="alias", source_id="same")

    def test_contract_starts_tomorrow_and_late_acceptance_uses_acceptance_day(self):
        from town_store import day
        self.approve("contract", dict(agent_id="linghang", requirements=[dict(category="results", kind="note", count=1, min_bytes=1, days=1, accepted=True)]))
        with self.app.db() as db:
            self.assertFalse(self.app.skills.contract_status(db, "linghang")["configured"])
            effective = db.execute("SELECT effective_day FROM v2_contracts").fetchone()[0]
        a = self.artifact()
        self.approve("accept", dict(artifact_id=a["id"], note="迟到的验收"))
        with self.app.db() as db:
            db.execute("UPDATE v2_acceptances SET created_at=? WHERE artifact_id=?", (effective + "T10:00:00+08:00", a["id"]))
            self.assertTrue(self.app.skills.contract_status(db, "linghang", effective)["satisfied"])
        self.app.artifacts.withdraw(a["id"])
        with self.app.db() as db:
            self.assertFalse(self.app.skills.contract_status(db, "linghang", effective)["satisfied"])
            self.assertTrue(verify_events(db))

    def test_staging_write_failure_never_creates_available_evidence(self):
        with patch("town_artifacts.os.fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.artifact()
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_artifacts").fetchone()[0], 0)

    def test_immutable_revision_idempotency_and_provenance(self):
        first = self.artifact(request_id="once", source_id="source")
        repeated = self.artifact(request_id="once", source_id="source")
        self.assertEqual(first["id"], repeated["id"])
        with self.assertRaises(TownError):
            self.artifact(content="changed", request_id="once", source_id="source")
        revised = self.app.artifacts.revise(dict(artifact_id=first["id"], content="人工整理后的新版本"))
        self.assertEqual(revised["source_type"], "manual-derived")
        self.assertEqual(json.loads(revised["parents"]), [first["id"]])
        self.assertEqual(self.app.artifacts.content(first), "可核验的工作结果。")
        with self.app.db() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE v2_artifacts SET hash='fake' WHERE id=?", (first["id"],))

    def test_delivery_and_derived_content_keep_source_restrictions(self):
        self.configure(residents=["linghang", "scout"])
        root = self.artifact(demo=True)
        self.allow(root, readers=["linghang", "scout"])
        copy = self.app.artifacts.deliver(dict(artifact_id=root["id"], recipient="scout"))
        self.assertEqual(copy["root"], root["id"])
        self.assertEqual(copy["demo"], 1)
        derived = self.artifact(owner="scout", parents=[copy["id"]], policy=dict(readers=["scout"], destinations=[ENDPOINT], deliverable=True))
        with self.app.db() as db:
            self.app.artifacts.require(db, derived["id"], "scout", ENDPOINT)
        self.allow(root, readers=["linghang"])
        with self.app.db() as db, self.assertRaises(TownError):
            self.app.artifacts.require(db, derived["id"], "scout", ENDPOINT)

    def test_partial_publication_recovers_without_duplicate(self):
        with patch.object(self.app.artifacts, "publish", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                self.artifact(request_id="pending", source_id="source")
        with self.app.db() as db:
            row = dict(db.execute("SELECT * FROM v2_artifacts WHERE request_id='pending'").fetchone())
        self.assertEqual(row["status"], "pending")
        self.app.artifacts.recover()
        retry = self.artifact(request_id="pending", source_id="source")
        self.assertEqual(retry["id"], row["id"])
        self.assertEqual(retry["status"], "available")
        self.assertTrue(self.app.artifacts.path(retry).is_file())

    def test_missing_or_tampered_files_do_not_count_as_accepted(self):
        a = self.artifact()
        self.approve("accept", dict(artifact_id=a["id"], note="人工检查全部条件"))
        self.approve("contract", dict(agent_id="linghang", requirements=[dict(category="results", kind="note", count=1, min_bytes=1, days=7, accepted=True)]))
        with self.app.db() as db:
            self.assertTrue(self.app.skills.contract_status(db, "linghang", on_day=db.execute("SELECT MAX(effective_day) FROM v2_contracts").fetchone()[0])["satisfied"])
        self.app.artifacts.path(a).write_text("篡改", encoding="utf-8")
        with self.app.db() as db:
            self.assertFalse(self.app.skills.contract_status(db, "linghang", on_day=db.execute("SELECT MAX(effective_day) FROM v2_contracts").fetchone()[0])["satisfied"])
            self.assertTrue(verify_events(db))

    def test_contract_checks_each_file_before_count_and_content_dedup(self):
        for value in ("a" * 100, "b", "c", "a" * 100):
            self.artifact(content=value)
        self.approve("contract", dict(agent_id="linghang", requirements=[dict(category="results", kind="note", count=3, min_bytes=80, days=7, accepted=False)]))
        with self.app.db() as db:
            result = self.app.skills.contract_status(db, "linghang", on_day=db.execute("SELECT MAX(effective_day) FROM v2_contracts").fetchone()[0])
        self.assertFalse(result["satisfied"])
        self.assertEqual(result["requirements"][0]["actual"], 1)
        with self.assertRaises(TownError):
            self.approve("contract", dict(agent_id="linghang", requirements=[]))

    def test_hardlink_substitution_is_rejected(self):
        a = self.artifact()
        original = self.app.artifacts.path(a)
        os.link(original, self.root / "extra-link")
        with self.assertRaises(TownError):
            self.app.artifacts.content(a)

    def test_only_completed_assistant_reply_can_be_saved(self):
        with self.assertRaises(TownError):
            self.app.artifacts.save_reply(dict(message_id="missing"))
        with self.assertRaises(TownError):
            self.app.artifacts.save_reply(dict(message_id="missing", owner="scout"))
        job = self.app.chat(dict(agent_id="linghang", message="hello", request_id="chat"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.app.job(job["job_id"])
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(.01)
        self.assertEqual(result["status"], "completed", result)
        saved = self.app.artifacts.save_reply(dict(message_id=result["message"]["id"]))
        self.assertEqual(saved["owner"], "linghang")
        self.assertEqual(saved["source_type"], "assistant")
        edited = self.app.artifacts.save_reply(dict(message_id=result["message"]["id"], content="手工版本"))
        self.assertEqual(edited["source_type"], "manual-derived")


class OwnerSkillTest(GrowthFixture):
    def test_one_use_state_binding_and_native_secret(self):
        a = self.artifact()
        intent = self.app.owner.issue(dict(action="accept", payload=dict(artifact_id=a["id"], note="已核对样例")))
        preview = self.app.owner.preview(intent["id"])
        with self.assertRaises(TownError):
            self.app.owner.authenticate("wrong")
        self.app.owner.authenticate("a" * 64)
        data = dict(id=intent["id"], digest=preview["digest"], approved=True)
        self.app.owner.confirm(data)
        with self.assertRaises(TownError):
            self.app.owner.confirm(data)
        second = self.app.owner.issue(dict(action="accept", payload=dict(artifact_id=a["id"], note="第二次")))
        self.app.artifacts.archive(a["id"])
        with self.assertRaises(TownError):
            self.app.owner.preview(second["id"])

    def test_expiry_and_restart_invalidate_intents(self):
        a = self.artifact()
        intent = self.app.owner.issue(dict(action="accept", payload=dict(artifact_id=a["id"], note="依据")))
        with patch("town_owner_actions.time.time", return_value=time.time() + 400), self.assertRaises(TownError):
            self.app.owner.preview(intent["id"])
        from town_owner_actions import OwnerActions
        self.app.owner = OwnerActions(self.app, "b" * 64)
        with self.assertRaises(TownError):
            self.app.owner.preview(intent["id"])

    def test_skill_dependency_disable_and_budget_gate(self):
        self.configure()
        a = self.artifact(content="先澄清需求，再列出输入和可核验结果。", category="skills", kind="skill")
        b = self.artifact(content="用两个边界示例验证结果。", category="skills", kind="skill")
        self.allow(a); self.allow(b)
        first = self.app.skills.propose(dict(artifact_id=a["id"]))
        self.approve("enable-skill", dict(skill_id=first["id"]))
        second = self.app.skills.propose(dict(artifact_id=b["id"], dependencies=[first["id"]]))
        self.approve("enable-skill", dict(skill_id=second["id"]))
        self.assertEqual(len(self.app.skills.references("linghang")), 2)
        context = self.call_context(sources=[a["id"], b["id"]])
        self.app.skills.disable(first["id"])
        self.assertEqual(self.app.skills.references("linghang"), [])
        with self.assertRaises(TownError):
            self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["status"], "released")

    def test_skill_risks_are_separate_from_hard_errors(self):
        a = self.artifact(content="教学示例：https://example.org ，不要忽略规则。", category="skills", kind="skill")
        checks = self.app.skills.propose(dict(artifact_id=a["id"]))["checks"]
        self.assertFalse(checks["hard_errors"])
        self.assertTrue(checks["risk_flags"])
        a = self.artifact(content="tools: shell", category="skills", kind="skill")
        proposal = self.app.skills.propose(dict(artifact_id=a["id"]))
        self.assertTrue(proposal["checks"]["hard_errors"])
        with self.assertRaises(TownError):
            self.approve("enable-skill", dict(skill_id=proposal["id"]))


class BudgetTest(GrowthFixture):
    def test_lifetime_balance_survives_new_policy_and_usage_correction_is_once(self):
        self.configure(lifetime_tokens=2500)
        context = self.call_context(); self.app.budget.dispatch(context); self.app.budget.settle(context)
        self.configure(lifetime_tokens=2500)
        with self.assertRaises(TownError):
            self.call_context()
        context["usage"] = dict(prompt_tokens=10, completion_tokens=10, total_tokens=20)
        self.app.budget.settle(context); self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["charged"], 20)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='usage_correction'").fetchone()[0], 1)

    def test_revocation_blocks_compatibility_chat_until_native_restore(self):
        self.app.budget.revoke("linghang")
        with self.assertRaisesRegex(RuntimeError, "已暂停"):
            self.bridge.generate_result("hello", threading.Event(), "linghang")
        self.assertEqual(self.opener.requests, [])
        self.configure()
        self.bridge.generate_result("hello", threading.Event(), "linghang")
        self.assertEqual(len(self.opener.requests), 1)

    def test_reference_permissions_cover_history_and_legacy_text_is_omitted_in_strict_mode(self):
        self.configure()
        a = self.artifact(content="restricted knowledge")
        with self.app.db() as db:
            db.execute("INSERT INTO messages VALUES ('history','linghang','assistant','derived summary','complete','2026-11-14')")
            db.execute("INSERT INTO v2_message_sources VALUES ('history',?)", (pack([a["id"]]),))
        prompt, sources, unused = self.app.model_input("linghang")
        self.assertIn(a["id"], sources)
        self.assertIn("strict_data_scope", prompt)
        with self.assertRaisesRegex(RuntimeError, "不允许发往"):
            self.bridge.generate_result(prompt, threading.Event(), "linghang", dict(sources=sources))
        self.assertEqual(self.opener.requests, [])

    def test_supported_profile_is_explicit_and_does_not_trust_an_arbitrary_server(self):
        self.assertTrue(ProviderBridge.budget_capability(dict(base_url=ENDPOINT, model="gpt-4o-mini"))["enforce_supported"])
        self.assertFalse(ProviderBridge.budget_capability(dict(base_url="https://unknown.example/v1", model="gpt-4o-mini"))["enforce_supported"])
        self.assertFalse(ProviderBridge.budget_capability(dict(base_url=ENDPOINT, model="unknown"))["enforce_supported"])
        for sample in ("普通中文输入", "ASCII", "🙂" * 20, "line\nnext\tend"):
            context = dict(agent_id="linghang", cancel=threading.Event(), options={})
            payload = pack(dict(messages=[dict(role="user", content=sample)], max_tokens=1024)).encode()
            self.app.budget.admit(dict(base_url=ENDPOINT, model="gpt-4o-mini"), payload, context)
            self.assertGreaterEqual(self.calls()[-1]["reserved"], len(sample.encode()) + 1024)

    def test_parallel_reservations_share_the_same_last_balance(self):
        self.configure(daily_tokens=2500)
        def call(unused):
            try:
                return self.call_context()
            except TownError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_full_input_reservation_rejects_oversized_request(self):
        self.configure(daily_tokens=2500)
        with self.assertRaisesRegex(RuntimeError, "预算"):
            self.bridge.generate_result("x" * 8000, threading.Event(), "linghang", dict(controlled=True))
        self.assertEqual(self.opener.requests, [])

    def test_unknown_usage_charged_once_on_original_day(self):
        self.configure()
        with patch("town_budget.day", return_value="2026-11-13"):
            context = self.call_context()
        self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        self.app.budget.settle(context)
        row = self.calls()[0]
        self.assertEqual(row["day"], "2026-11-13")
        self.assertEqual(row["charged"], row["reserved"])
        self.assertEqual(row["status"], "unknown")
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='call_settled'").fetchone()[0], 1)

    def test_usage_is_settled_even_if_reply_is_invalid(self):
        self.configure()
        self.opener.body = pack(dict(choices=[], usage=dict(prompt_tokens=30, completion_tokens=20, total_tokens=50))).encode()
        with self.assertRaises(RuntimeError):
            self.bridge.generate_result("hello", threading.Event(), "linghang", dict(controlled=True))
        self.assertEqual(self.calls()[0]["charged"], 50)
        self.assertEqual(self.calls()[0]["status"], "settled")

    def test_pause_before_dispatch_releases_but_sent_call_is_not_free(self):
        self.configure()
        before = self.call_context()
        sent = self.call_context()
        self.app.budget.dispatch(sent)
        self.app.budget.pause()
        with self.assertRaises(TownError):
            self.app.budget.dispatch(before)
        self.app.budget.settle(before); self.app.budget.settle(sent)
        rows = self.calls()
        self.assertEqual([r["status"] for r in rows], ["released", "unknown"])
        self.assertEqual(rows[0]["charged"], 0)
        self.assertGreater(rows[1]["charged"], 0)

    def test_overrun_records_real_usage_and_pauses(self):
        self.configure()
        context = self.call_context(); self.app.budget.dispatch(context)
        context["usage"] = dict(prompt_tokens=20000, completion_tokens=1000, total_tokens=21000)
        self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["charged"], 21000)
        self.assertTrue(self.app.budget.config()["paused"])

    def test_private_material_never_reaches_opener(self):
        self.configure()
        a = self.artifact(content="private-text")
        with self.assertRaisesRegex(RuntimeError, "不允许发往"):
            self.bridge.generate_result("private-text", threading.Event(), "linghang", dict(controlled=True, sources=[a["id"]]))
        self.assertEqual(self.opener.requests, [])
        self.allow(a)
        self.bridge.generate_result("private-text", threading.Event(), "linghang", dict(controlled=True, sources=[a["id"]]))
        self.assertEqual(len(self.opener.requests), 1)

    def test_strict_mode_rejects_codex_and_unapproved_model(self):
        self.configure()
        self.bridge.save_settings(dict(mode="codex", base_url="", model=""))
        with self.assertRaisesRegex(RuntimeError, "Codex"):
            self.bridge.generate_result("hello", threading.Event(), "linghang")
        self.bridge.save_settings(dict(mode="api", base_url=ENDPOINT, model="other-model"))
        with self.assertRaisesRegex(RuntimeError, "档案|未经授权"):
            self.bridge.generate_result("hello", threading.Event(), "linghang")
        self.assertEqual(self.opener.requests, [])


class TrainingTest(GrowthFixture):
    def test_default_off_and_idempotent_role_plans(self):
        self.assertEqual(self.app.growth.plan()["created"], 0)
        self.configure(residents=["resident-yanghui", "resident-alige"])
        self.assertEqual(self.app.growth.plan()["created"], 2)
        self.assertEqual(self.app.growth.plan()["created"], 0)
        workspace = self.app.growth.workspace()
        self.assertEqual(len(workspace["tasks"]), 2)
        self.assertNotEqual(workspace["tasks"][0]["title"], workspace["tasks"][1]["title"])
        self.assertEqual(self.opener.requests, [])

    def test_three_stage_training_with_heldout_evidence_and_real_artifact(self):
        self.configure()
        self.app.growth.plan()
        with self.app.db() as db:
            task = dict(db.execute("SELECT * FROM v2_tasks").fetchone())
            rubric = json.loads(task["rubric"])
        expected = rubric["answers"]
        wrong = {key: not value for key, value in expected.items()}
        replies = [pack(wrong), "方法：先核实来源，再检查授权与版本。三个条件同时满足才能交付。", pack(expected)]
        payloads = []
        def open_response(request, timeout):
            payloads.append(json.loads(request.data))
            return io.BytesIO(pack(dict(choices=[dict(message=dict(content=replies.pop(0)))], usage=dict(prompt_tokens=100, completion_tokens=100, total_tokens=200))).encode())
        self.opener.open = open_response
        self.app.growth.tick()
        workspace = self.app.growth.workspace()
        finished = workspace["tasks"][0]
        self.assertEqual(finished["status"], "accepted", finished)
        self.assertEqual(len(payloads), 3)
        self.assertNotIn("case-1", payloads[1]["messages"][1]["content"])
        self.assertEqual(len(workspace["artifacts"]), 1)
        self.assertTrue(json.loads(finished["evaluation"])["improved"])
        self.assertEqual(sum(c["charged"] for c in workspace["calls"]), 600)
        self.app.growth.tick()
        self.assertEqual(len(payloads), 3)

    def test_subjective_project_requires_owner_acceptance(self):
        self.configure()
        project = self.app.growth.project(dict(title="整理需求", goal="输出需求规格与验收条件"))
        self.assertEqual(len(project["assignments"]), 1)
        self.app.growth.tick()
        task = self.app.growth.workspace()["tasks"][0]
        self.assertEqual(task["status"], "review", task)
        self.assertFalse(json.loads(task["evaluation"])["improved"])
        self.approve("accept", dict(artifact_id=task["artifact_id"], note="逐条核对用户需求与验收条件"))
        self.assertEqual(self.app.growth.workspace()["tasks"][0]["status"], "accepted")

    def test_grader_rejects_self_claims_and_wrong_types(self):
        rubric = objective("测试工程师")
        for reply in ('我已经通过所有检查', '{}', pack({key: int(value) for key, value in rubric["answers"].items()})):
            self.assertFalse(grade(reply, rubric)["passed"])
        self.assertTrue(grade(pack(rubric["answers"]), rubric)["passed"])

    def test_restart_retains_unknown_charge_and_requires_explicit_retry(self):
        self.configure()
        self.app.growth.plan()
        context = self.call_context(); self.app.budget.dispatch(context)
        with self.app.db() as db:
            db.execute("UPDATE v2_tasks SET status='running',attempts=1")
        self.app.close()
        self.app = TownApp(self.root / "data", self.bridge, self.root / "team", report_root=self.root / "reports", owner_secret="b" * 64, start_growth=False)
        self.assertEqual(self.calls()[0]["status"], "unknown")
        self.assertEqual(self.app.growth.workspace()["tasks"][0]["status"], "interrupted")
        self.app.growth.tick()
        self.assertEqual(self.opener.requests, [])


class OwnerHTTPTest(unittest.TestCase):
    def test_browser_cannot_confirm_or_read_private_native_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ProviderBridge(root / "runtime", root / "data", MemoryCredentials(), lambda: MockOpener())
            server = create_server(0, root / "data", bridge, root / "team", report_root=root / "reports", owner_secret="c" * 64, start_growth=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/internal/owner/intents/" + "0" * 32)
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
                connection.request("POST", "/internal/owner/confirm", "{}", {"Content-Type": "application/json", "X-Town-Client": "pixel-town"})
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
                connection.request("GET", "/api/growth", headers={"Origin": "https://evil.example"})
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
                connection.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(3)
