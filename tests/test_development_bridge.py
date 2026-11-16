"""Real development budget + bridge integration, without a network connection."""
import io
import json
import threading
from unittest.mock import patch

from test_growth_v2 import GrowthFixture
from town_store import pack


MESSAGES = dict(mode="api", base_url="https://synthetic-development.example/api",
                model="synthetic-model", protocol="anthropic", auth_type="bearer")


class DevelopmentBridgeTest(GrowthFixture):
    def setUp(self):
        super().setUp()
        self.profile = dict(profile="synthetic-32k", enforce_supported=True,
                            max_output_tokens=32768, max_request_bytes=262144, socket_timeout=600)
        resolver = patch.object(self.bridge, "resolved_budget_capability", return_value=self.profile)
        self.addCleanup(resolver.stop)
        resolver.start()
        self.bridge.save_settings(MESSAGES)
        self.configure(endpoints=[MESSAGES_IDENTITY])
        self.development()
        self.response()

    def development(self, relaxed=True, cap=32768):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.app.budget.apply_development(db, dict(relaxed=relaxed, max_output_tokens=cap))

    def response(self, stop="end_turn", text="synthetic final", thinking="synthetic private reasoning", output=10):
        content = [dict(type="thinking", thinking=thinking), dict(type="text", text=text)]
        self.opener.body = pack(dict(content=content, stop_reason=stop,
            usage=dict(input_tokens=4, output_tokens=output))).encode()

    def generate(self, **options):
        return self.bridge.generate_result("synthetic task", threading.Event(), "linghang",
                                           dict(controlled=True, workbench=True, **options))

    def events(self, kind):
        with self.app.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM v2_events WHERE kind=?", (kind,))]

    def test_real_request_uses_32k_cap_600_second_socket_and_isolated_usage(self):
        result = self.generate()
        payload = json.loads(self.opener.requests[0].data)
        self.assertEqual(payload["max_tokens"], 32768)
        self.assertEqual(self.opener.timeouts, [600])
        self.assertGreaterEqual(self.bridge.request_timeout(MESSAGES, dict(controlled=True, workbench=True)), 630)
        self.assertEqual(result["text"], "synthetic final")
        row = self.calls()[0]
        self.assertEqual((row["purpose"], row["charged"], row["status"]), ("development", 14, "settled"))
        self.assertEqual(self.events("call_reserved")[0]["usage_limits"]["output_tokens"], 32768)
        self.assertEqual(self.events("model_response_observed"), [dict(protocol="anthropic", stop_reason="end_turn",
            requested_output_tokens=32768, text_chars=15, thinking_chars=27)])

    def test_chat_and_training_keep_original_cap_and_timeout(self):
        for options in ({}, dict(controlled=True), dict(workbench=True)):
            with self.subTest(options=options):
                self.bridge.generate_result("synthetic task", threading.Event(), "linghang", options)
                self.assertEqual(json.loads(self.opener.requests[-1].data)["max_tokens"], 1024)
                self.assertEqual(self.opener.timeouts[-1], 120)
                self.assertEqual(self.bridge.request_timeout(MESSAGES, options), self.bridge.timeout)
        self.assertEqual([row["purpose"] for row in self.calls()], ["chat", "training", "chat"])

    def test_disabled_relaxation_keeps_original_output_cap(self):
        self.development(relaxed=False)
        self.generate()
        self.assertEqual(json.loads(self.opener.requests[0].data)["max_tokens"], 1024)

    def test_limits_snapshot_cannot_be_reused_after_config_change(self):
        original = self.app.budget.request_limits
        def capture_then_change(options):
            snapshot = original(options)
            self.development(cap=8192)
            return snapshot
        with patch.object(self.app.budget, "request_limits", side_effect=capture_then_change):
            with self.assertRaisesRegex(RuntimeError, "研发额度设置已变化"):
                self.generate()
        self.assertEqual(self.opener.requests, [])
        self.assertEqual(self.calls(), [])

    def test_change_before_dispatch_prevents_network_and_refunds_reservation(self):
        with self.assertRaisesRegex(RuntimeError, "研发额度设置已变化"):
            self.generate(before_dispatch=lambda: self.development(cap=8192))
        self.assertEqual(self.opener.requests, [])
        self.assertEqual((self.calls()[0]["status"], self.calls()[0]["charged"]), ("released", 0))

    def test_change_after_dispatch_charges_usage_but_never_publishes_reply(self):
        original = self.opener.open
        def open_then_change(request, timeout):
            response = original(request, timeout)
            self.development(cap=8192)
            return response
        with patch.object(self.opener, "open", side_effect=open_then_change):
            with self.assertRaisesRegex(RuntimeError, "研发额度设置已变化"):
                self.generate()
        self.assertEqual((self.calls()[0]["status"], self.calls()[0]["charged"]), ("settled", 14))
        self.assertEqual(len(self.events("model_response_observed")), 1)
        self.assertFalse(self.app.budget.config()["paused"])

    def test_truncation_records_actual_limit_and_counts_without_private_content(self):
        self.response(stop="max_tokens", text="", thinking="PRIVATE_THINKING", output=32768)
        with self.assertRaisesRegex(RuntimeError, "32768 Token") as error:
            self.generate()
        self.assertNotIn("PRIVATE_THINKING", str(error.exception))
        self.assertFalse(self.app.budget.config()["paused"])
        self.assertEqual(self.calls()[0]["charged"], 32772)
        self.assertEqual(self.events("model_response_observed"), [dict(protocol="anthropic", stop_reason="max_tokens",
            requested_output_tokens=32768, text_chars=0, thinking_chars=16)])

    def test_context_exhaustion_is_distinct_from_output_cap(self):
        self.response(stop="model_context_window_exceeded")
        with self.assertRaisesRegex(RuntimeError, "提高输出额度无法扩大模型上下文"):
            self.generate()
        self.assertEqual(self.events("model_response_observed")[0]["stop_reason"], "model_context_window_exceeded")
        self.assertFalse(self.app.budget.config()["paused"])

    def test_internal_cancel_after_full_response_never_returns_reply(self):
        original = self.bridge.response_metadata
        def cancel_after_read(*args):
            metadata = original(*args)
            self.bridge.cancel_for_agent("linghang")
            return metadata
        with patch.object(self.bridge, "response_metadata", side_effect=cancel_after_read):
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                self.generate()
        self.assertEqual(self.calls()[0]["charged"], 14)
        self.assertFalse(self.app.budget.config()["paused"])

    def test_cancel_during_long_socket_wait_is_prompt_and_charges_unknown(self):
        release, cancel = threading.Event(), threading.Event()
        self.opener.wait = release
        results = []
        def run():
            try:
                results.append(self.bridge.generate_result("synthetic wait", cancel, "linghang", dict(controlled=True, workbench=True)))
            except RuntimeError as error:
                results.append(str(error))
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(self.opener.started.wait(1))
            cancel.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertIn("已取消", results[0])
            self.assertEqual(self.opener.timeouts, [600])
        finally:
            release.set()
            worker.join(1)
            for pending in list(self.bridge._pending):
                pending.join(1)
        self.assertEqual((self.calls()[0]["status"], self.calls()[0]["charged"]), ("unknown", self.calls()[0]["reserved"]))
        self.assertEqual(self.events("model_response_observed"), [])
        self.assertFalse(self.app.budget.config()["paused"])

    def test_cancelled_read_returning_eof_is_not_a_missing_usage_violation(self):
        bridge = self.bridge
        class CancelAtEOF(io.BytesIO):
            def read(self, *args):
                bridge.cancel_for_agent("linghang")
                return b""
        with patch.object(self.opener, "open", return_value=CancelAtEOF()):
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                self.generate()
        row = self.calls()[0]
        self.assertEqual((row["status"], row["charged"]), ("unknown", row["reserved"]))
        self.assertFalse(self.app.budget.config()["paused"])
        self.assertEqual(self.events("budget_contract_violation"), [])
        self.assertEqual(self.events("model_response_observed"), [])


MESSAGES_IDENTITY = {key: value for key, value in MESSAGES.items() if key != "mode"}
