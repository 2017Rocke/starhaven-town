"""Personal directions and deterministic curriculum integration, all data temporary."""
import json
from unittest.mock import patch

from test_growth_v2 import GrowthFixture
from server import APIError
from town_curriculum import role_for
from town_growth import objective
from town_store import TownError, digest, now, pack, uid, verify_events


class CultivationTest(GrowthFixture):
    def save(self, **fields):
        revision = self.app.growth.cultivation()["revision"]
        return self.app.growth.save_cultivation(dict(expected_revision=revision, **fields))

    def resident(self, identity="linghang"):
        return next(row for row in self.app.growth.cultivation()["residents"] if row["agent_id"] == identity)

    def test_initial_recommendation_uses_existing_direction_without_writing(self):
        self.configure(direction="减少交接返工")
        with self.app.db() as db:
            before = db.execute("SELECT COUNT(*) FROM v2_events").fetchone()[0]
        state = self.app.growth.cultivation()
        self.assertEqual(state["revision"], 0)
        self.assertEqual(state["town_goal"], "减少交接返工")
        self.assertIn("减少交接返工", self.resident()["effective_direction"])
        self.assertEqual(self.resident()["mode"], "recommended")
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events").fetchone()[0], before)
            self.assertIsNone(db.execute("SELECT 1 FROM v2_meta WHERE key='growth_directions'").fetchone())

    def test_save_is_isolated_from_budget_permissions_and_other_residents(self):
        before = self.app.budget.config()
        self.save(agent_id="linghang", mode="custom", long_term="减少交接返工", focus="进度证据", success_criteria="说清实际部署版本")
        self.save(agent_id="scout", mode="custom", long_term="核实项目来源", focus="作者背景")
        self.save(town_goal="提高小镇实际成果质量")
        self.assertEqual(self.resident()["long_term"], "减少交接返工")
        self.assertEqual(self.resident("scout")["long_term"], "核实项目来源")
        self.assertEqual(self.app.budget.config(), before)
        self.assertEqual(self.opener.requests, [])
        with self.app.db() as db:
            self.assertTrue(verify_events(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_tasks").fetchone()[0], 0)

    def test_conflict_and_invalid_fields_leave_configuration_and_events_unchanged(self):
        state = self.save(town_goal="已有方向")
        with self.app.db() as db:
            count = db.execute("SELECT COUNT(*) FROM v2_events").fetchone()[0]
        with self.assertRaises(TownError) as conflict:
            self.app.growth.save_cultivation(dict(expected_revision=0, town_goal="过期覆盖"))
        self.assertEqual(conflict.exception.code, 409)
        invalid = [dict(town_goal=""), dict(town_goal="a" * 1001), dict(town_goal="新方向", enabled=True),
                   dict(agent_id="linghang", mode="custom", long_term=" "),
                   dict(agent_id="linghang", mode="custom", long_term="x", focus=[]),
                   dict(agent_id="not-found", mode="recommended")]
        for data in invalid:
            with self.subTest(data=str(data)[:60]), self.assertRaises((TownError, APIError)):
                self.app.growth.save_cultivation(dict(expected_revision=state["revision"], **data))
        self.assertEqual(self.app.growth.cultivation(), state)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events").fetchone()[0], count)

    def test_reset_recommendation_uses_latest_town_goal(self):
        self.save(agent_id="scout", mode="custom", long_term="临时方向", focus="密钥")
        self.save(town_goal="重视材料交接")
        self.save(agent_id="scout", mode="recommended")
        resident = self.resident("scout")
        self.assertEqual(resident["mode"], "recommended")
        self.assertIn("重视材料交接", resident["effective_direction"])
        self.assertEqual(resident["focus"], "")
        self.assertEqual(resident["long_term"], resident["recommendation"]["long_term"])

    def test_save_persists_after_reopening_application(self):
        from server import TownApp
        state = self.save(agent_id="linghang", mode="custom", long_term="长期方向", focus="交接")
        self.app.close()
        self.app = TownApp(self.root / "data", self.bridge, self.root / "team", report_root=self.root / "reports", owner_secret="b" * 64, start_growth=False)
        self.assertEqual(self.app.growth.cultivation(), state)

    def test_preview_neither_mutates_database_nor_calls_model_when_training_disabled(self):
        with self.app.db() as db:
            counts = [db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("v2_tasks", "v2_plans", "v2_events", "v2_calls")]
        preview = self.app.growth.preview_cultivation(dict(agent_id="linghang"))
        self.assertEqual(len(preview["days"]), 7)
        self.assertIn("未启用或未获授权", preview["notice"])
        with self.app.db() as db:
            self.assertEqual(counts, [db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("v2_tasks", "v2_plans", "v2_events", "v2_calls")])
        self.assertEqual(self.opener.requests, [])

    def test_focus_and_personal_direction_outrank_town_keywords(self):
        self.configure(residents=["resident-yanghui"], daily_tasks=1)
        self.save(town_goal="需求验收标准效果覆盖联调文档")
        self.save(agent_id="resident-yanghui", mode="custom", long_term="需求变更", focus="权限", success_criteria="不能擅自赋予源码权限")
        preview = self.app.growth.preview_cultivation(dict(agent_id="resident-yanghui"))
        first = preview["days"][0]["tasks"][0]
        self.assertEqual(first["skill_id"], "product.boundaries")
        self.assertIn("近期重点匹配", first["reason"])
        self.assertIn("不能擅自赋予源码权限", first["prompt"])
        self.assertTrue(any("不能擅自赋予源码权限" in check for check in first["checks"]))
        self.save(agent_id="resident-yanghui", mode="custom", long_term="需求变更", focus="")
        preview = self.app.growth.preview_cultivation(dict(agent_id="resident-yanghui"))
        self.assertEqual(preview["days"][0]["tasks"][0]["skill_id"], "product.change")

    def test_preview_and_actual_week_share_courses_with_no_filler_duplicates(self):
        self.configure(residents=["resident-yanghui"], daily_tasks=3)
        with patch("town_growth.day", return_value="2030-03-01"):
            preview = self.app.growth.preview_cultivation(dict(agent_id="resident-yanghui"))
        fingerprints = []
        for day_result in preview["days"]:
            target = day_result["day"]
            with patch("town_growth.day", return_value=target):
                self.app.growth.plan()
                self.assertEqual(self.app.growth.plan()["created"], 0)
            with self.app.db() as db:
                rows = db.execute("SELECT t.* FROM v2_tasks t JOIN v2_plans p ON p.id=t.plan_id WHERE p.day=? ORDER BY t.rowid", (target,)).fetchall()
            self.assertEqual([row["prompt"] for row in rows], [task["prompt"] for task in day_result["tasks"]])
            fingerprints.extend(json.loads(row["planning"])["fingerprint"] for row in rows)
        self.assertEqual(len(fingerprints), len(set(fingerprints)))
        self.assertEqual(len(fingerprints), 8)
        self.assertTrue(any(not item["tasks"] and "不为达到数量" in item["note"] for item in preview["days"]))
        with patch("town_growth.day", return_value="2030-03-08"):
            self.assertGreater(self.app.growth.plan()["created"], 0)

    def test_saved_direction_preserves_existing_tasks_but_applies_to_future_plans(self):
        self.configure(residents=["linghang"])
        with patch("town_growth.day", return_value="2030-03-01"):
            self.app.growth.plan()
            with self.app.db() as db:
                old_task = dict(db.execute("SELECT * FROM v2_tasks").fetchone())
            state = self.save(agent_id="linghang", mode="custom", long_term="减少版本交接返工", focus="交接", success_criteria="每项成果标明版本")
            preview = self.app.growth.preview_cultivation(dict(agent_id="linghang"))
            self.assertTrue(preview["days"][0]["existing"])
            self.assertEqual(preview["days"][0]["tasks"][0]["prompt"], old_task["prompt"])
            self.assertEqual(self.app.growth.plan()["created"], 0)
        with patch("town_growth.day", return_value="2030-03-02"):
            self.app.growth.plan()
        with self.app.db() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM v2_tasks WHERE id=?", (old_task["id"],)).fetchone()), old_task)
            newest = dict(db.execute("SELECT * FROM v2_tasks ORDER BY rowid DESC LIMIT 1").fetchone())
        self.assertEqual(newest["prompt"], preview["days"][1]["tasks"][0]["prompt"])
        self.assertEqual(json.loads(newest["planning"])["direction_revision"], state["revision"])
        self.assertIn("每项成果标明版本", newest["prompt"])

    def test_workflow_states_are_not_capability_gaps_and_unverified_failure_is_excluded(self):
        with self.app.db() as db:
            for status in ("review", "interrupted", "blocked", "failed"):
                task_id = self.app.growth.insert_task(db, "linghang", "practice", status, "任务", {}, [])
                db.execute("UPDATE v2_tasks SET status=? WHERE id=?", (status, task_id))
            profile = self.app.growth.profile(db, "linghang")
        self.assertEqual(profile["gaps"], [])
        self.assertEqual(profile["failed_skills"], [])

    def test_verified_failure_is_tracked_without_trusting_wrong_state_labels(self):
        artifact = self.artifact()
        rubric = objective("主管")
        expected = rubric["answers"]
        wrong = {key: not value for key, value in expected.items()}
        with self.app.db() as db:
            task = self.app.growth.insert_task(db, "linghang", "objective", "有证据错误", "任务", rubric, [], planning=dict(skill_id="coordination.status"))
            evidence = dict(rubric=rubric, baseline_text=pack(expected), candidate_text=pack(wrong),
                            reference_id=artifact["id"], reference_hash=artifact["hash"], calls=[], provenance=[])
            eid = uid()
            db.execute("INSERT INTO v2_trial_evidence VALUES (?,?,?,?,?,?)", (eid, task, 1, pack(evidence), digest(evidence), now()))
            db.execute("UPDATE v2_tasks SET status='failed',evaluation=? WHERE id=?", (pack(dict(evidence_id=eid, candidate=dict(passed=False))), task))
            profile = self.app.growth.profile(db, "linghang")
            self.assertEqual(profile["gaps"], ["有证据错误"])
            self.assertEqual(profile["failed_skills"], ["coordination.status"])
            db.execute("UPDATE v2_tasks SET status='interrupted' WHERE id=?", (task,))
            self.assertEqual(self.app.growth.profile(db, "linghang")["gaps"], [])

    def test_new_professional_course_remains_under_review_until_explicit_acceptance(self):
        self.configure()
        self.app.growth.plan()
        self.app.growth.tick()
        task = self.app.growth.workspace()["tasks"][0]
        self.assertEqual(task["kind"], "practice")
        self.assertEqual(task["status"], "review")
        self.assertFalse(json.loads(task["evaluation"])["improved"])
        self.assertEqual(len(self.opener.requests), 1)
        self.approve("accept", dict(artifact_id=task["artifact_id"], note="核对任务全部检查项"))
        with self.app.db() as db:
            self.assertEqual(self.app.growth.profile(db, "linghang")["accepted"], 1)
        self.app.artifacts.withdraw(task["artifact_id"])
        with self.app.db() as db:
            self.assertEqual(self.app.growth.profile(db, "linghang")["accepted"], 0)

    def test_damaged_accepted_artifact_is_not_counted_as_verified_progress(self):
        self.configure()
        self.app.growth.plan()
        self.app.growth.tick()
        task = self.app.growth.workspace()["tasks"][0]
        self.approve("accept", dict(artifact_id=task["artifact_id"], note="已核对"))
        with self.app.db() as db:
            artifact = self.app.artifacts.row(db, task["artifact_id"])
        self.app.artifacts.path(artifact).write_text("内容已发生变化")
        with self.app.db() as db:
            self.assertEqual(self.app.growth.profile(db, "linghang")["accepted"], 0)

    def test_full_length_town_goal_keeps_recommendation_editable_and_reaches_prompt(self):
        town_goal = "长期目标" * 250
        self.save(town_goal=town_goal)
        recommended = self.resident()["recommendation"]
        self.assertLessEqual(len(recommended["long_term"]), 1000)
        self.save(agent_id="linghang", mode="custom", **recommended)
        preview = self.app.growth.preview_cultivation(dict(agent_id="linghang"))
        self.assertIn(town_goal, preview["days"][0]["tasks"][0]["prompt"])

    def test_security_engineer_does_not_inherit_generic_engineering_course(self):
        self.assertEqual(role_for(dict(id="custom", role="网络安全工程师"))[0], "security")
