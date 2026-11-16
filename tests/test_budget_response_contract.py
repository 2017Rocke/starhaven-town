"""Response contract failures are charged, stop publication, and revoke queued use."""
import io
import json
import urllib.error
import threading
from unittest.mock import patch

from test_growth_v2 import GrowthFixture, ENDPOINT
from town_store import TownError, pack, verify_events


class BudgetResponseContractTest(GrowthFixture):
    def configure_resolved(self, **capability):
        profile = dict(profile="private-test", enforce_supported=True, max_output_tokens=4096,
                       max_request_bytes=65536, socket_timeout=20)
        profile.update(capability)
        resolver = patch.object(self.bridge, "resolved_budget_capability", return_value=profile, create=True)
        self.addCleanup(resolver.stop)
        resolver.start()
        self.configure()
        return profile

    def sent(self):
        context = self.call_context()
        self.app.budget.dispatch(context)
        context["reply"] = "a result that must not escape a failed contract"
        context["response_received"] = True
        return context

    def test_missing_usage_charges_reservation_and_blocks_pending_dispatch(self):
        self.configure()
        pending = self.call_context()
        context = self.sent()
        old_revision = self.app.budget.config()["revision"]
        self.app.budget.settle(context)
        self.assertNotIn("reply", context)
        self.assertEqual(context["budget_violation"], "missing_usage")
        self.assertIn("已暂停", context["error"])
        config = self.app.budget.config()
        self.assertTrue(config["paused"])
        self.assertEqual(config["revision"], old_revision + 1)
        with self.assertRaises(TownError):
            self.app.budget.dispatch(pending)
        self.app.budget.settle(pending)
        self.app.budget.settle(context)
        self.assertEqual(self.app.budget.config()["revision"], old_revision + 1)
        rows = self.calls()
        unknown = next(row for row in rows if row["id"] == context["call_id"])
        released = next(row for row in rows if row["id"] == pending["call_id"])
        self.assertEqual((unknown["status"], unknown["charged"]), ("unknown", unknown["reserved"]))
        self.assertEqual((released["sent"], released["charged"], released["status"]), (0, 0, "released"))
        with self.app.db() as db:
            events = db.execute("SELECT payload FROM v2_events WHERE kind='budget_contract_violation'").fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0][0])["reason"], "missing_usage")
            self.assertTrue(verify_events(db))

    def test_each_component_bound_is_checked_even_below_reserved_total(self):
        for name in ("input_overrun", "output_overrun", "invalid_usage"):
            with self.subTest(name=name):
                self.configure()
                context = self.sent()
                limits = context["usage_limits"]
                usage = (dict(prompt_tokens=limits["input_tokens"] + 1, completion_tokens=0)
                         if name == "input_overrun" else
                         dict(prompt_tokens=0, completion_tokens=limits["output_tokens"] + 1)
                         if name == "output_overrun" else dict(prompt_tokens=1, completion_tokens=1))
                usage["total_tokens"] = sum(usage.values()) + (1 if name == "invalid_usage" else 0)
                self.assertLess(usage["total_tokens"], limits["total_tokens"])
                context["usage"] = usage
                self.app.budget.settle(context)
                self.assertEqual(context["budget_violation"], name)
                self.assertNotIn("reply", context)
                self.assertTrue(self.app.budget.config()["paused"])
                self.assertEqual(self.calls()[-1]["charged"], usage["total_tokens"])

    def test_total_overrun_is_recorded_but_never_returned(self):
        self.configure()
        context = self.sent()
        context["usage"] = dict(prompt_tokens=20000, completion_tokens=1, total_tokens=20001)
        self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["charged"], 20001)
        self.assertNotIn("reply", context)
        self.assertTrue(self.app.budget.config()["paused"])

    def test_bridge_violation_is_fixed_audit_enum_and_survives_late_usage(self):
        self.configure()
        context = self.sent()
        context["budget_violation"] = "invalid_usage"
        self.app.budget.settle(context)
        revision = self.app.budget.config()["revision"]
        context["usage"] = dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        context["reply"] = "late response"
        self.app.budget.settle(context)
        self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["charged"], 5)
        self.assertEqual(self.calls()[0]["status"], "settled")
        self.assertEqual(self.app.budget.config()["revision"], revision)
        self.assertNotIn("reply", context)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='usage_correction'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='budget_contract_violation'").fetchone()[0], 1)

    def test_other_inflight_reply_cannot_publish_after_contract_pause(self):
        self.configure()
        first, second = self.sent(), self.sent()
        self.app.budget.settle(first)
        second["usage"] = dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        self.app.budget.settle(second)
        self.assertNotIn("reply", second)
        self.assertNotIn("budget_violation", second)
        self.assertIn("权限已暂停", second["error"])
        self.assertEqual(self.calls()[1]["charged"], 5)

    def test_compatible_chat_can_still_return_text_with_unknown_usage(self):
        self.configure(enabled=False, strict=False)
        self.opener.body = pack(dict(choices=[dict(message=dict(content="ordinary chat"))])).encode()
        result = self.bridge.generate_result("synthetic", threading.Event(), "linghang")
        self.assertEqual(result["text"], "ordinary chat")
        self.assertEqual(result["usage_quality"], "unknown")
        self.assertFalse(self.app.budget.config()["paused"])
        self.assertEqual(self.calls()[0]["status"], "unknown")

    def test_violation_prevents_training_artifact_creation(self):
        self.configure()
        self.opener.body = pack(dict(choices=[dict(message=dict(content="should not be saved"))])).encode()
        self.app.growth.plan()
        self.app.growth.tick()
        workspace = self.app.growth.workspace()
        self.assertEqual(workspace["tasks"][0]["status"], "blocked")
        self.assertEqual(workspace["artifacts"], [])
        self.assertTrue(workspace["policy"]["paused"])
        self.assertEqual(len(self.opener.requests), 1)

    def test_compatibility_total_overrun_still_pauses_shared_budget(self):
        self.configure(enabled=False, strict=False)
        self.opener.body = pack(dict(choices=[dict(message=dict(content="ordinary chat"))],
            usage=dict(prompt_tokens=20000, completion_tokens=1, total_tokens=20001))).encode()
        result = self.bridge.generate_result("synthetic", threading.Event(), "linghang")
        self.assertEqual(result["text"], "ordinary chat")
        self.assertTrue(self.app.budget.config()["paused"])
        self.assertEqual(self.calls()[0]["charged"], 20001)

    def test_late_compatibility_usage_overrun_pauses_without_double_charging(self):
        self.configure(enabled=False, strict=False)
        context = dict(agent_id="linghang", cancel=threading.Event(), options={})
        self.app.budget.admit(dict(base_url=ENDPOINT, model="gpt-4o-mini"), pack(dict(max_tokens=1024)).encode(), context)
        self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        self.assertFalse(self.app.budget.config()["paused"])
        context["usage"] = dict(prompt_tokens=20000, completion_tokens=1, total_tokens=20001)
        self.app.budget.settle(context)
        revision = self.app.budget.config()["revision"]
        self.app.budget.settle(context)
        self.assertTrue(self.app.budget.config()["paused"])
        self.assertEqual(self.app.budget.config()["revision"], revision)
        self.assertEqual(self.calls()[0]["charged"], 20001)

    def test_late_controlled_overrun_repauses_after_owner_resumed_policy(self):
        self.configure()
        context = self.sent()
        self.app.budget.settle(context)
        self.configure()
        self.assertFalse(self.app.budget.config()["paused"])
        context["usage"] = dict(prompt_tokens=20000, completion_tokens=1, total_tokens=20001)
        self.app.budget.settle(context)
        self.assertTrue(self.app.budget.config()["paused"])
        self.assertEqual(self.calls()[0]["charged"], 20001)

    def test_http_and_transport_failures_do_not_invalidate_usage_contract(self):
        for error in (urllib.error.HTTPError(ENDPOINT, 401, "unauthorized", {}, io.BytesIO()),
                      urllib.error.HTTPError(ENDPOINT, 429, "limited", {}, io.BytesIO()),
                      urllib.error.URLError("temporary timeout")):
            with self.subTest(error=type(error).__name__):
                self.configure()
                self.opener.error = error
                with self.assertRaises(RuntimeError):
                    self.bridge.generate_result("synthetic", threading.Event(), "linghang", dict(controlled=True))
                self.assertFalse(self.app.budget.config()["paused"])
                row = self.calls()[-1]
                self.assertEqual((row["status"], row["charged"]), ("unknown", row["reserved"]))
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='budget_contract_violation'").fetchone()[0], 0)

    def test_local_resolver_limits_are_enforced_before_network(self):
        profile = self.configure_resolved(max_output_tokens=512)
        with self.assertRaisesRegex(TownError, "输出上限超出已验证范围"):
            self.call_context()
        profile.update(max_output_tokens=4096, max_request_bytes=1)
        with self.assertRaisesRegex(TownError, "输入范围"):
            self.call_context()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.opener.requests, [])

    def test_dispatch_rechecks_profile_and_releases_expired_reservation(self):
        profile = self.configure_resolved()
        context = self.call_context()
        self.assertEqual(context["provider_identity"], dict(base_url=ENDPOINT, model="gpt-4o-mini"))
        profile["enforce_supported"] = False
        with self.assertRaisesRegex(TownError, "过期或撤销"):
            self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        row = self.calls()[0]
        self.assertEqual((row["sent"], row["charged"], row["status"]), (0, 0, "released"))
        self.assertFalse(self.app.budget.config()["paused"])

    def test_resolved_profile_never_grants_missing_native_permissions(self):
        self.configure_resolved()
        with self.assertRaisesRegex(RuntimeError, "未授权"):
            self.bridge.generate_result("synthetic", threading.Event(), "scout", dict(controlled=True, workbench=True))
        self.assertEqual(self.opener.requests, [])

    def test_dispatch_rechecks_verified_range_when_profile_is_replaced(self):
        profile = self.configure_resolved()
        context = self.call_context()
        profile["max_output_tokens"] = 512
        with self.assertRaisesRegex(TownError, "输出上限超出已验证范围"):
            self.app.budget.dispatch(context)
        self.app.budget.settle(context)
        self.assertEqual(self.calls()[0]["status"], "released")
