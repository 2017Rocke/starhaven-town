"""Local Messages receipts plus real bridge/workbench; synthetic data and mock HTTP only."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error

from provider_bridge import ProviderBridge, endpoint_identity
from provider_verification import EXTENDED_INPUT_LIMIT, EXTENDED_OUTPUT_LIMIT, EXTENDED_PROFILE, TTL, run_verification
from server import TownApp
from test_provider_bridge import MemoryCredentials
from town_store import uid


CONFIG = dict(mode="api", base_url="https://gateway.example.test/api", model="synthetic-messages-model",
              protocol="anthropic", auth_type="bearer")
ANALYSIS = dict(summary="明确的合成需求", scope=["修改示例值"], acceptance_criteria=["值变为2"], questions=[])
EXTENDED = dict(output_limit=EXTENDED_OUTPUT_LIMIT, input_limit=EXTENDED_INPUT_LIMIT)


class BrokenRead(io.BytesIO):
    def read(self, *args):
        raise OSError("synthetic read timeout after headers")


class SyntheticMessagesOpener:
    def __init__(self):
        self.verifying = False
        self.responses = []
        self.requests = []
        self.timeouts = []
        self.failure = None
        self.violation = None

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.failure == "http429":
            raise urllib.error.HTTPError(request.full_url, 429, "synthetic limit", {}, io.BytesIO())
        if self.failure == "read":
            return BrokenRead()
        body = json.loads(request.data)
        cap = body["max_tokens"]
        if self.verifying:
            is_cap = cap in (16, 128) and len(request.data) < 1000
            value = ANALYSIS if cap in (4096, 32768) else "OK"
            completion = cap if is_cap else 8
            stop = "max_tokens" if is_cap else "end_turn"
        else:
            value = self.responses.pop(0) if self.responses else "ordinary synthetic reply"
            completion = cap + 1 if self.violation == "output" else 8
            stop = "end_turn"
        content = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        result = dict(content=[dict(type="text", text=content)], stop_reason=stop,
                      usage=dict(input_tokens=max(1, len(request.data) // 4), output_tokens=completion))
        if self.violation == "missing":
            result.pop("usage")
        return io.BytesIO(json.dumps(result, ensure_ascii=False).encode())


class LocalMessagesWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "synthetic-repository"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / ".git" / "HEAD").write_text("user-owned-git")
        (self.repo / "example.py").write_text("# user work\nvalue = 1\n")
        self.opener = SyntheticMessagesOpener()
        self.bridge = ProviderBridge(self.root / "runtime", self.root / "town", MemoryCredentials(), lambda: self.opener)
        self.bridge.save_settings(dict(CONFIG, api_key="synthetic-test-credential"))
        self.app = TownApp(self.root / "town", self.bridge, self.root / "team", report_root=self.root / "reports",
                           owner_secret="f" * 64, start_growth=False)
        self.members = dict(product="resident-yanghui", developer="resident-jiji", tester="resident-alige", commander="linghang")
        self.project = self.app.development.create_project(dict(title="合成研发项目", members=self.members, request_id=uid()))
        self.task = self.app.development.create_task(dict(project_id=self.project["id"], title="修改合成示例",
            requirements="将example中的值从1改成2，保留注释；不运行命令，不提交Git。", request_id=uid()))

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def owner(self, action, payload):
        intent = self.app.owner.issue(dict(action=action, payload=payload))
        preview = self.app.owner.preview(intent["id"])
        return self.app.owner.confirm(dict(id=intent["id"], digest=preview["digest"], approved=True))

    def verify(self, config=None, now=None, **limits):
        config = config or CONFIG
        self.opener.verifying = True
        try:
            records = run_verification(config, "synthetic-test-credential", opener=self.opener, **limits)
            receipt = self.bridge._local_budget_profiles.record(config, records, now=now, **limits)
        finally:
            self.opener.verifying = False
        self.assertEqual(len(records), 4)
        self.opener.requests.clear()
        self.opener.timeouts.clear()
        return receipt

    def policy(self, residents=None, endpoint=None):
        return self.owner("policy", dict(enabled=False, strict=True,
            residents=residents if residents is not None else [self.members[key] for key in ("product", "developer", "tester")],
            endpoints=[endpoint or endpoint_identity(CONFIG)], daily_tokens=100000, daily_calls=12))

    def connect(self):
        self.owner("dev-connect", dict(project_id=self.project["id"], repositories=[dict(name="app", path=str(self.repo))], allow_source=True))

    def action(self, name, **fields):
        return self.app.development.action(self.task["id"], dict(action=name, revision=1, request_id=uid(), **fields))

    def ready(self):
        self.opener.responses = [ANALYSIS]
        self.action("analyze")
        self.app.development.run_once()
        self.action("confirm")

    def develop(self, replies=None):
        self.opener.responses = replies or [dict(action="read", path="app/example.py"),
            dict(action="replace", path="app/example.py", old="value = 1", new="value = 2"),
            dict(action="finish", summary="已生成私有候选，尚未执行功能自测")]
        self.action("develop")
        self.app.development.run_once()
        task = self.app.development.task(self.task["id"])
        return [a for a in task["artifacts"] if a["kind"] == "development_candidate"]

    def controlled(self):
        return self.bridge.generate_result("synthetic request", threading.Event(), self.members["developer"], dict(controlled=True, workbench=True))

    def calls(self):
        with self.app.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM v2_calls ORDER BY rowid")]

    def receipt_status(self):
        return json.loads(self.bridge._local_budget_profiles.path.read_text())["entries"][-1]["status"]

    def test_receipt_with_native_grants_runs_real_workbench_without_touching_source(self):
        receipt = self.verify()
        self.assertNotIn("synthetic-test-credential", json.dumps(receipt))
        self.assertTrue(self.bridge.settings()["budget_capability"]["local_verification"])
        self.policy()
        self.connect()
        self.ready()
        candidates = self.develop()
        self.assertEqual(len(candidates), 1)
        content = candidates[0]["content"]
        self.assertEqual(content["status"], "review")
        self.assertIn("+value = 2", self.app.dev_runtime.run_details(content["run_id"])["diff"])
        self.assertEqual((self.repo / "example.py").read_text(), "# user work\nvalue = 1\n")
        self.assertEqual((self.repo / ".git" / "HEAD").read_text(), "user-owned-git")
        self.assertEqual(len(self.opener.requests), 4)
        self.assertTrue(all(row["purpose"] == "development" and row["status"] == "settled" for row in self.calls()))
        self.assertFalse(self.app.budget.config()["enabled"])
        self.assertFalse(self.app.budget.config()["paused"])
        for role in ("product", "tester", "commander"):
            shared = json.dumps(self.app.development.role_context(self.task["id"], role, self.members[role]))
            self.assertNotIn(str(self.repo), shared)
            self.assertNotIn("value = 1", shared)

    def test_extended_receipt_runs_relaxed_workbench_with_larger_cap_and_keeps_chat_limits(self):
        receipt = self.verify(**EXTENDED)
        self.assertEqual(receipt["profile"], EXTENDED_PROFILE)
        self.policy()
        self.owner("development-budget", dict(relaxed=True, max_output_tokens=32768))
        self.connect()
        self.ready()
        candidates = self.develop()
        self.assertEqual(len(candidates), 1)
        self.assertEqual([json.loads(request.data)["max_tokens"] for request in self.opener.requests], [8192, 32768, 32768, 32768])
        self.assertEqual(self.opener.timeouts, [600] * 4)
        self.assertGreaterEqual(self.bridge.request_timeout(CONFIG, dict(controlled=True, workbench=True)), 630)
        self.assertEqual(self.bridge.request_timeout(CONFIG, {}), self.bridge.timeout)
        self.bridge.generate_result("synthetic ordinary chat", threading.Event(), self.members["developer"])
        self.assertEqual(json.loads(self.opener.requests[-1].data)["max_tokens"], 1024)
        self.assertEqual(self.opener.timeouts[-1], 120)
        self.assertEqual([row["purpose"] for row in self.calls()], ["development"] * 4 + ["chat"])
        self.assertEqual((self.repo / "example.py").read_text(), "# user work\nvalue = 1\n")
        self.assertEqual((self.repo / ".git" / "HEAD").read_text(), "user-owned-git")

    def test_relaxed_development_cannot_expand_a_legacy_receipt(self):
        self.verify()
        self.policy()
        self.owner("development-budget", dict(relaxed=True, max_output_tokens=32768))
        with self.assertRaisesRegex(RuntimeError, "输出上限超出已验证范围"):
            self.controlled()
        self.assertEqual(self.opener.requests, [])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.receipt_status(), "verified")

    def test_extended_receipt_requires_grants_and_enforces_expanded_input_boundary(self):
        self.verify(**EXTENDED)
        self.owner("development-budget", dict(relaxed=True, max_output_tokens=32768))
        with self.assertRaisesRegex(RuntimeError, "未授权|已暂停"):
            self.controlled()
        self.assertEqual(self.opener.requests, [])
        self.policy()
        options = dict(controlled=True, workbench=True)
        result = self.bridge.generate_result("x" * 70000, threading.Event(), self.members["developer"], options)
        self.assertEqual(result["text"], "ordinary synthetic reply")
        self.assertGreater(len(self.opener.requests[0].data), 65536)
        self.assertEqual(self.opener.timeouts, [600])
        with self.assertRaisesRegex(RuntimeError, "超出已验证的输入范围"):
            self.bridge.generate_result("x" * 262144, threading.Event(), self.members["developer"], options)
        self.assertEqual(len(self.opener.requests), 1)
        self.assertEqual(self.receipt_status(), "verified")
        self.assertFalse(self.app.budget.config()["paused"])

    def test_receipt_never_supplies_missing_resident_endpoint_or_source_grants(self):
        self.verify()
        with self.assertRaisesRegex(RuntimeError, "未授权|已暂停"):
            self.controlled()
        self.policy(residents=[self.members["product"]])
        with self.assertRaisesRegex(RuntimeError, "未授权|已暂停"):
            self.controlled()
        wrong = dict(endpoint_identity(CONFIG), auth_type="api_key")
        self.policy(endpoint=wrong)
        with self.assertRaisesRegex(RuntimeError, "端点未经授权"):
            self.controlled()
        self.assertEqual(self.opener.requests, [])
        self.policy()
        self.ready()
        before = len(self.opener.requests)
        self.assertEqual(self.develop(), [])
        self.assertEqual(len(self.opener.requests), before)
        self.assertEqual((self.repo / "example.py").read_text(), "# user work\nvalue = 1\n")

    def test_missing_expired_and_revoked_receipts_never_dispatch(self):
        self.policy()
        with self.assertRaisesRegex(RuntimeError, "适配档案"):
            self.controlled()
        self.verify(now=time.time() - TTL - 60)
        self.assertFalse(self.bridge.settings()["budget_capability"]["enforce_supported"])
        with self.assertRaisesRegex(RuntimeError, "适配档案"):
            self.controlled()
        self.verify()
        self.bridge._local_budget_profiles.revoke(CONFIG, "synthetic_revocation")
        with self.assertRaisesRegex(RuntimeError, "适配档案"):
            self.controlled()
        self.assertEqual(self.opener.requests, [])
        self.assertEqual(self.calls(), [])

    def test_new_protocol_identity_requires_new_source_grant_even_with_receipt(self):
        self.verify()
        self.policy()
        self.connect()
        self.ready()
        changed = dict(CONFIG, auth_type="api_key")
        self.bridge.save_settings(changed)
        self.verify(config=changed)
        self.policy(endpoint=endpoint_identity(changed))
        before = len(self.opener.requests)
        self.assertEqual(self.develop(), [])
        self.assertEqual(len(self.opener.requests), before)
        self.assertEqual((self.repo / "example.py").read_text(), "# user work\nvalue = 1\n")

    def test_output_overrun_revokes_receipt_and_prevents_development_candidate(self):
        self.verify()
        self.policy()
        self.connect()
        self.ready()
        self.opener.violation = "output"
        candidates = self.develop([dict(action="finish", summary="不得发布")])
        self.assertEqual(candidates, [])
        self.assertEqual(self.receipt_status(), "revoked")
        self.assertTrue(self.app.budget.config()["paused"])
        self.assertFalse(self.bridge.settings()["budget_capability"]["enforce_supported"])
        with self.app.db() as db:
            reason = json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='budget_contract_violation'").fetchone()[0])["reason"]
        self.assertEqual(reason, "output_overrun")
        self.assertEqual(self.calls()[-1]["status"], "settled")
        before = len(self.opener.requests)
        with self.assertRaises(RuntimeError):
            self.controlled()
        self.assertEqual(len(self.opener.requests), before)

    def test_missing_usage_revokes_receipt_and_prevents_development_candidate(self):
        self.verify()
        self.policy()
        self.connect()
        self.ready()
        self.opener.violation = "missing"
        self.assertEqual(self.develop([dict(action="finish", summary="不得发布")]), [])
        self.assertEqual(self.receipt_status(), "revoked")
        self.assertTrue(self.app.budget.config()["paused"])
        row = self.calls()[-1]
        self.assertEqual((row["status"], row["charged"]), ("unknown", row["reserved"]))

    def test_receipt_revoked_between_reservation_and_dispatch_releases_budget(self):
        self.verify()
        self.policy()
        with self.assertRaisesRegex(RuntimeError, "过期或撤销"):
            self.bridge.generate_result("synthetic request", threading.Event(), self.members["developer"],
                dict(controlled=True, workbench=True,
                     before_dispatch=lambda: self.bridge._local_budget_profiles.revoke(CONFIG, "synthetic_revoke_before_send")))
        self.assertEqual(self.opener.requests, [])
        row = self.calls()[0]
        self.assertEqual((row["status"], row["charged"], row["sent"]), ("released", 0, 0))
        self.assertFalse(self.app.budget.config()["paused"])

    def test_violation_after_private_changes_never_publishes_candidate_or_applies_source(self):
        self.verify()
        self.policy()
        self.connect()
        self.ready()
        original_open = self.opener.open
        def fail_after_private_change(request, timeout):
            body = json.loads(request.data)
            if "private_candidate" in body["messages"][0]["content"]:
                self.opener.violation = "missing"
            return original_open(request, timeout)
        self.opener.open = fail_after_private_change
        self.assertEqual(self.develop(), [])
        self.assertEqual(self.receipt_status(), "revoked")
        self.assertTrue(self.app.budget.config()["paused"])
        self.assertEqual((self.repo / "example.py").read_text(), "# user work\nvalue = 1\n")
        self.assertEqual((self.repo / ".git" / "HEAD").read_text(), "user-owned-git")

    def test_http429_and_read_failure_keep_receipt_without_publishing_reply(self):
        self.verify()
        self.policy()
        for failure in ("http429", "read"):
            with self.subTest(failure=failure):
                self.opener.failure = failure
                with self.assertRaises(RuntimeError):
                    self.controlled()
                self.assertEqual(self.receipt_status(), "verified")
                self.assertTrue(self.bridge.settings()["budget_capability"]["enforce_supported"])
                self.assertFalse(self.app.budget.config()["paused"])
                row = self.calls()[-1]
                self.assertEqual((row["status"], row["charged"]), ("unknown", row["reserved"]))
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='budget_contract_violation'").fetchone()[0], 0)
