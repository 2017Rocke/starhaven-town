"""Development quotas are independent, without relaxing any access control."""
import json
import threading
from unittest.mock import patch

from test_growth_v2 import ENDPOINT, GrowthFixture
from town_budget import DEVELOPMENT_OUTPUT_LIMITS
from town_store import TownError, day, now, pack, uid, verify_events


class DevelopmentBudgetTest(GrowthFixture):
    def setUp(self):
        super().setUp()
        self.profile = dict(profile="synthetic-development", enforce_supported=True,
                            max_output_tokens=32768, max_request_bytes=262144)
        resolver = patch.object(self.bridge, "resolved_budget_capability", return_value=self.profile)
        self.addCleanup(resolver.stop)
        resolver.start()
        self.configure()

    def development(self, relaxed=True, max_output_tokens=32768):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.app.budget.apply_development(db, dict(relaxed=relaxed, max_output_tokens=max_output_tokens))

    def context(self, options=None, agent="linghang", output=None, config=None, prepared=None, body="synthetic"):
        options = dict(controlled=True, workbench=True) if options is None else options
        limits = self.app.budget.request_limits(options) if prepared is None else prepared
        result = dict(agent_id=agent, cancel=threading.Event(), options=options, request_limits=limits)
        payload = pack(dict(messages=[dict(role="user", content=body)],
                            max_tokens=limits["max_output_tokens"] if output is None else output)).encode()
        self.app.budget.admit(config or dict(base_url=ENDPOINT, model="gpt-4o-mini"), payload, result)
        return result

    def finish(self, context, prompt=3, completion=2):
        self.app.budget.dispatch(context)
        context.update(response_received=True, reply="synthetic result",
                       usage=dict(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion))
        self.app.budget.settle(context)
        return context

    def history(self, purpose, charged, status="settled"):
        identity = uid()
        with self.app.db() as db:
            db.execute("INSERT INTO v2_calls(id,agent_id,day,purpose,endpoint,model,reserved,charged,status,sent,revision,created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                       (identity, "linghang", day(), purpose, ENDPOINT, "gpt-4o-mini", charged, charged, status,
                        self.app.budget.config(db)["revision"], now()))
        return identity

    def test_defaults_and_validation_cannot_change_authorization(self):
        original = self.app.budget.config()
        self.assertEqual(self.app.budget.development_config(), dict(revision=0, relaxed=False, max_output_tokens=32768))
        self.assertEqual(self.app.budget.validate_development(dict(relaxed=False, max_output_tokens=32768)),
                         dict(relaxed=False, max_output_tokens=32768))
        for value in DEVELOPMENT_OUTPUT_LIMITS:
            with self.subTest(value=value):
                approved = self.development(max_output_tokens=value)
                self.assertEqual(approved["max_output_tokens"], value)
        for data in (None, [], {}, dict(relaxed=True), dict(max_output_tokens=32768),
                     dict(relaxed=1, max_output_tokens=32768), dict(relaxed=True, max_output_tokens=True),
                     dict(relaxed=True, max_output_tokens=8193),
                     dict(revision=4), dict(paused=False), dict(residents=["scout"]), dict(endpoints=[])):
            with self.subTest(data=data), self.assertRaises(TownError):
                self.app.budget.validate_development(data)
        self.assertEqual(self.app.budget.config(), original)
        with self.app.db() as db:
            self.assertTrue(verify_events(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='development_budget_approved'").fetchone()[0], 4)

    def test_relaxation_only_applies_to_controlled_workbench(self):
        self.development()
        for options in ({}, dict(controlled=True), dict(workbench=True), dict(controlled=False, workbench=True)):
            with self.subTest(options=options):
                limits = self.app.budget.request_limits(options)
                self.assertEqual(limits["max_output_tokens"], 1024)
                self.assertFalse(limits["development"])
                self.assertFalse(limits["relaxed"])
                self.assertIsNone(limits["development_revision"])
        self.assertEqual(self.app.budget.request_limits(dict(controlled=True, workbench=True))["max_output_tokens"], 32768)
        self.development(relaxed=False, max_output_tokens=16384)
        self.assertEqual(self.app.budget.request_limits(dict(controlled=True, workbench=True))["max_output_tokens"], 1024)

    def test_relaxed_workbench_bypasses_all_four_growth_quantity_limits(self):
        self.configure(daily_tokens=1000, lifetime_tokens=1000, daily_calls=1, request_total_cap=1000)
        self.history("development", 200000)
        self.development()
        first = self.context()
        self.finish(first)
        second = self.context()
        self.finish(second)
        self.assertGreater(first["usage_limits"]["total_tokens"], 32768)
        self.assertEqual(first["usage_limits"]["output_tokens"], 32768)
        self.assertTrue(first["request_limits"]["relaxed"])
        self.assertEqual(first["development_revision"], 1)
        self.assertEqual([row["purpose"] for row in self.calls()], ["development"] * 3)
        with self.app.db() as db:
            payload = json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='call_reserved' AND subject=?", (first["call_id"],)).fetchone()[0])
        self.assertEqual(payload["usage_limits"], first["usage_limits"])
        self.assertEqual(payload["request_limits"], first["request_limits"])

    def test_disabled_relaxation_keeps_each_growth_quantity_limit(self):
        scenarios = (
            (dict(request_total_cap=1000), "单次上限", None),
            (dict(lifetime_tokens=1000), "累计额度不足", None),
            (dict(daily_tokens=1000), "今日额度不足", None),
            (dict(daily_calls=1), "今日调用次数", "development"),
        )
        for changes, error, history in scenarios:
            with self.subTest(error=error):
                self.configure(**changes)
                if history:
                    self.history(history, 1)
                with self.assertRaisesRegex(TownError, error):
                    self.context()
        self.assertEqual(len(self.calls()), 1)

    def test_development_history_never_drains_chat_training_balances(self):
        self.history("development", 5000000)
        self.history("development", 250000, status="unknown")
        self.history("development", 33000, status="reserved")
        self.history("chat", 7)
        self.history("training", 11)
        for relaxed in (False, True, False):
            with self.subTest(relaxed=relaxed):
                self.development(relaxed=relaxed)
                with self.app.db() as db:
                    growth = next(row for row in self.app.budget.balances(db) if row["agent_id"] == "linghang")
                development = next(row for row in self.app.budget.development_balances() if row["agent_id"] == "linghang")
                self.assertEqual((growth["charged_today"], growth["reserved_today"], growth["calls_today"], growth["lifetime"]), (18, 0, 2, 18))
                self.assertEqual((growth["unknown_calls"], growth["budget_scope"]), (0, "chat-training"))
                self.assertEqual((development["charged_today"], development["reserved_today"], development["calls_today"], development["lifetime"]), (5250000, 33000, 3, 5283000))
                self.assertEqual((development["unknown_calls"], development["budget_scope"]), (1, "development"))
                self.assertEqual(development["daily_remaining"], None if relaxed else 0)
                pending = self.context(options=dict(controlled=True))
                self.app.budget.settle(pending)

    def test_chat_history_does_not_exhaust_separate_development_pool(self):
        self.history("chat", 5000000)
        self.history("training", 100000)
        self.finish(self.context())
        with self.assertRaisesRegex(TownError, "累计额度不足"):
            self.context(options=dict(controlled=True))

    def test_uncontrolled_workbench_label_cannot_escape_chat_quota(self):
        self.development()
        self.history("chat", 5000000)
        with self.assertRaisesRegex(TownError, "累计额度不足"):
            self.context(options=dict(workbench=True))

    def test_prepare_admit_revision_race_is_blocked_before_reservation(self):
        prepared = self.app.budget.request_limits(dict(controlled=True, workbench=True))
        self.development()
        with self.assertRaisesRegex(TownError, "研发额度设置已变化"):
            self.context(prepared=prepared)
        self.assertEqual(self.calls(), [])

    def test_admit_dispatch_revision_race_releases_unsent_reservation(self):
        self.development()
        context = self.context()
        self.development(max_output_tokens=8192)
        with self.assertRaisesRegex(TownError, "研发额度设置已变化"):
            self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        row = self.calls()[0]
        self.assertEqual((row["status"], row["sent"], row["charged"]), ("released", 0, 0))

    def test_changed_configuration_blocks_inflight_and_late_publication(self):
        self.development()
        context = self.context()
        self.app.budget.dispatch(context)
        self.development(relaxed=False)
        context.update(reply="must not publish", response_received=False)
        self.app.budget.settle(context)
        self.assertNotIn("reply", context)
        self.assertIn("研发额度设置已变化", context["error"])
        context.update(reply="late result", response_received=True,
                       usage=dict(prompt_tokens=3, completion_tokens=2, total_tokens=5))
        self.app.budget.settle(context)
        self.assertNotIn("reply", context)
        self.assertEqual(self.calls()[0]["charged"], 5)
        self.assertFalse(self.app.budget.config()["paused"])

    def test_relaxed_output_still_requires_verified_profile_range(self):
        self.development()
        self.profile["max_output_tokens"] = 4096
        with self.assertRaisesRegex(TownError, "输出上限超出已验证范围"):
            self.context()
        self.profile["max_output_tokens"] = 32768
        self.profile["max_request_bytes"] = 10
        with self.assertRaisesRegex(TownError, "输入范围"):
            self.context()
        self.profile["max_request_bytes"] = 262144
        self.profile["enforce_supported"] = False
        with self.assertRaisesRegex(TownError, "尚无经过验证"):
            self.context()
        self.assertEqual(self.calls(), [])

    def test_relaxed_configuration_does_not_grant_resident_endpoint_or_source_access(self):
        self.development()
        with self.assertRaisesRegex(TownError, "未授权"):
            self.context(agent="scout")
        with self.assertRaisesRegex(TownError, "端点未经授权"):
            self.context(config=dict(base_url="https://different.example.test/v1", model="gpt-4o-mini"))
        artifact = self.artifact()
        with self.assertRaises(TownError):
            self.context(options=dict(controlled=True, workbench=True, sources=[artifact["id"]]))
        with self.assertRaises(TownError):
            self.context(options=dict(controlled=True, workbench=True, skill_versions=["unapproved-skill"]))
        self.app.budget.pause()
        original = self.app.budget.config()
        self.development(max_output_tokens=4096)
        self.assertEqual(self.app.budget.config(), original)
        with self.assertRaisesRegex(TownError, "已暂停"):
            self.context()
        self.assertEqual(self.calls(), [])

    def test_relaxed_configuration_does_not_override_resident_revocation(self):
        self.app.budget.revoke("linghang")
        self.development()
        with self.assertRaisesRegex(TownError, "该居民已暂停"):
            self.context()

    def test_usage_contract_remains_enforced_in_relaxed_development(self):
        for kind in ("missing_usage", "output_overrun"):
            with self.subTest(kind=kind):
                self.configure()
                self.development()
                context = self.context()
                self.app.budget.dispatch(context)
                context.update(response_received=True, reply="invalid result")
                if kind == "output_overrun":
                    context["usage"] = dict(prompt_tokens=0, completion_tokens=32769, total_tokens=32769)
                self.app.budget.settle(context)
                self.assertEqual(context["budget_violation"], kind)
                self.assertNotIn("reply", context)
                self.assertTrue(self.app.budget.config()["paused"])
                self.assertGreater(self.calls()[-1]["charged"], 32768)

    def test_response_metadata_is_whitelisted_and_written_once(self):
        self.development()
        context = self.context()
        context["response_metadata"] = dict(protocol="anthropic", stop_reason="max_tokens", requested_output_tokens=32768,
            text_chars=0, thinking_chars=12000, response="private text", thinking="private reasoning", credential="private credential")
        self.finish(context)
        self.app.budget.settle(context)
        with self.app.db() as db:
            events = db.execute("SELECT payload FROM v2_events WHERE kind='model_response_observed'").fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0][0]), dict(protocol="anthropic", stop_reason="max_tokens",
                requested_output_tokens=32768, text_chars=0, thinking_chars=12000))
            self.assertTrue(verify_events(db))

    def test_response_metadata_redacts_unknown_stop_and_invalid_counts(self):
        raw = dict(protocol="openai", stop_reason="private provider string", requested_output_tokens=True,
                   text_chars=-5, thinking_chars=1000000001, content="private")
        self.assertEqual(self.app.budget.response_metadata(raw), dict(protocol="openai", stop_reason="other"))
        for invalid in (None, "private", dict(protocol="private", stop_reason="stop")):
            self.assertIsNone(self.app.budget.response_metadata(invalid))

    def test_released_request_has_no_response_metadata_event(self):
        context = self.context()
        context["response_metadata"] = dict(protocol="openai", stop_reason="stop", text_chars=4)
        self.app.budget.settle(context)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='model_response_observed'").fetchone()[0], 0)
