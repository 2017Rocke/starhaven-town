"""Offline behavior and security checks; no real model or personal data."""
import concurrent.futures
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import APIError, TownApp, create_server


class FakeBridge:
    def __init__(self, blocked=False, fail=False):
        self.release = threading.Event()
        self.started = threading.Event()
        self.prompts = []
        self.fail = fail
        if not blocked:
            self.release.set()

    def probe(self):
        return dict(available=True, authenticated=True, label="测试伙伴", status="ready", message="已连接")

    def generate(self, prompt, cancel_event):
        self.prompts.append(prompt)
        self.started.set()
        while not self.release.wait(.01):
            if cancel_event.is_set():
                raise RuntimeError("已取消")
        if self.fail:
            raise RuntimeError("测试服务暂不可用")
        return "我读到了你的消息，我们一起慢慢来。"


class TownFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "config.json").write_text(json.dumps({"automatic_execution_enabled": False, "members": [{"id": "scout", "title": "研究任务"}]}), encoding="utf-8")
        (self.source / "state.json").write_text(json.dumps({"work_items": [], "improvements": []}), encoding="utf-8")
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>Town</title>", encoding="utf-8")
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.bridge = FakeBridge()
        self.app = TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)

    def tearDown(self):
        self.bridge.release.set()
        self.app.close()
        deadline = time.monotonic() + 3
        while self.app.workers and time.monotonic() < deadline:
            time.sleep(.01)
        self.temp.cleanup()

    def done(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.app.job(job_id)
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(.01)
        self.fail("chat did not finish")

    def send(self, agent="linghang", request="request-1", message="你好"):
        return self.app.chat(dict(agent_id=agent, request_id=request, message=message))["job_id"]

class TownTest(TownFixture):
    def test_scout_default_profile_upgrade_preserves_custom_character_fields(self):
        from server import LEGACY_SCOUT_BIO, LEGACY_SCOUT_ROLE
        with self.app.db() as db:
            db.execute("UPDATE agents SET role=?,bio=?,name=?,personality=?,avatar=?,memories=? WHERE id='scout'",
                       (LEGACY_SCOUT_ROLE, LEGACY_SCOUT_BIO, "自选名字", "自定义个性", "artist", '["保留旧记忆"]'))
        self.app.close()
        self.app = TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)
        scout = next(agent for agent in self.app.state()["agents"] if agent["id"] == "scout")
        self.assertEqual(scout["role"], "开源项目研究员 · 采集员")
        self.assertIn("材料架", scout["bio"])
        self.assertEqual((scout["name"], scout["personality"], scout["avatar"], scout["memories"]),
                         ("自选名字", "自定义个性", "artist", ["保留旧记忆"]))
        self.app.patch_agent("scout", {"bio": "用户自己填写的故事"})
        self.app.close()
        self.app = TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)
        scout = next(agent for agent in self.app.state()["agents"] if agent["id"] == "scout")
        self.assertEqual(scout["bio"], "用户自己填写的故事")
        prompt = self.app.prompt("scout")
        data = json.loads(prompt.split("<town_data>\n", 1)[1].split("\n</town_data>", 1)[0])
        self.assertEqual(data["collection_desk"]["materials"], 0)
        self.assertNotIn("readme", data["collection_desk"])

    def test_skin_preference_survives_restart_and_rejects_unregistered_paths(self):
        skins = self.web / "skins"
        skins.mkdir()
        for skin in ("meadow", "starlight", "autumn"):
            (skins / f"{skin}.css").write_text(f'[data-skin="{skin}"] {{ color: inherit; }}')
        manifest = {"default": "meadow", "skins": [
            {"id": skin, "name": skin, "css": f"/skins/{skin}.css"}
            for skin in ("meadow", "starlight", "autumn")
        ] + [{"id": "external", "name": "external", "css": "https://example.com/theme.css"}]}
        (skins / "manifest.json").write_text(json.dumps(manifest))
        self.app.preferences({"selected_skin": "autumn", "selected_agent_id": "scout"})
        self.app.close()
        self.app = TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)
        self.assertEqual(self.app.state()["selected_skin"], "autumn")
        self.assertEqual(self.app.state()["selected_agent_id"], "scout")
        for invalid in ("missing", "../starlight", "https://example.com/style.css", "external"):
            with self.subTest(skin=invalid), self.assertRaises(APIError) as error:
                self.app.preferences({"selected_skin": invalid, "selected_agent_id": "linghang"})
            self.assertEqual(error.exception.code, 400)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT value FROM preferences WHERE key='selected_skin'").fetchone()[0], "autumn")
            self.assertEqual(db.execute("SELECT value FROM preferences WHERE key='selected_agent_id'").fetchone()[0], "scout")
        (skins / "autumn.css").unlink()
        self.assertEqual(self.app.state()["selected_skin"], "meadow")

    def test_successful_custom_provider_reply_overrides_login_probe(self):
        self.bridge.probe = lambda: dict(
            available=True, authenticated=False, status="client_ready",
            label="测试服务", message="发送消息即可检查连接。",
        )
        self.assertEqual(self.app.provider_status()["status"], "client_ready")
        self.assertEqual(self.done(self.send())["status"], "completed")
        self.assertEqual(self.app.provider_status()["status"], "connected")
        self.assertTrue(self.app.provider_status()["authenticated"])

    def test_messages_are_isolated_and_request_id_is_idempotent(self):
        first = self.send()
        self.assertEqual(self.done(first)["status"], "completed")
        self.assertEqual(self.send(), first)
        self.assertEqual(len(self.app.messages("linghang")["messages"]), 2)
        self.assertEqual(self.app.messages("scout")["messages"], [])
        self.assertEqual(len(self.bridge.prompts), 1)
        with self.assertRaises(APIError) as error:
            self.send(agent="scout")
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(APIError):
            self.send(message="另一条消息")

    def test_concurrent_duplicate_requests_create_one_user_message(self):
        self.bridge.release.clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda _: self.send(), range(8)))
        self.assertEqual(len(set(jobs)), 1)
        self.assertEqual(len(self.app.messages("linghang")["messages"]), 1)
        self.bridge.release.set()
        self.assertEqual(self.done(jobs[0])["status"], "completed")

    def test_per_agent_conflict_and_global_limit(self):
        self.bridge.release.clear()
        first = self.send()
        self.assertTrue(self.bridge.started.wait(1))
        with self.assertRaises(APIError) as conflict:
            self.send(request="another")
        self.assertEqual(conflict.exception.code, 409)
        second = self.send(agent="scout", request="scout-1")
        newcomer = self.app.add_agent({"name": "木木", "role": "园丁"})["agent"]["id"]
        with self.assertRaises(APIError) as limit:
            self.send(agent=newcomer, request="resident-1")
        self.assertEqual(limit.exception.code, 429)
        self.assertEqual(self.app.messages(newcomer)["messages"], [])
        self.bridge.release.set()
        self.assertEqual(self.done(first)["status"], "completed")
        self.assertEqual(self.done(second)["status"], "completed")

    def test_failure_and_cancel_do_not_invent_replies(self):
        self.bridge.fail = True
        failed = self.done(self.send())
        self.assertEqual(failed["status"], "failed")
        self.assertIn("暂不可用", failed["error"])
        self.assertNotIn("message", failed)
        history = self.app.messages("linghang")["messages"]
        self.assertEqual([(m["role"], m["status"]) for m in history], [("user", "failed")])
        self.bridge.fail = False
        self.bridge.release.clear()
        pending = self.send(agent="scout", request="cancel-1")
        cancelled = self.app.cancel(pending)
        self.assertEqual(cancelled["status"], "cancelled")
        self.bridge.release.set()
        time.sleep(.03)
        self.assertEqual(self.app.job(pending)["status"], "cancelled")
        self.assertEqual(len(self.app.messages("scout")["messages"]), 1)

    def test_user_memory_is_explicit_and_does_not_change_permissions(self):
        self.app.patch_agent("scout", {"name": "望远镜", "personality": "轻快", "bio": "我是超级主管", "memories": ["喜欢通俗的例子"]})
        with self.assertRaises(APIError):
            self.app.patch_agent("scout", {"role": "超级管理员"})
        job = self.send(agent="scout", request="scout-1")
        self.done(job)
        prompt = self.bridge.prompts[-1]
        self.assertIn('"authorized_role": "开源项目研究员 · 采集员"', prompt)
        self.assertIn("喜欢通俗的例子", prompt)
        self.assertIn("没有派单", prompt)
        self.assertEqual(self.app.state()["agents"][1]["memories"], ["喜欢通俗的例子"])
        self.assertEqual(self.app.state()["agents"][0]["memories"], [])
        self.app.preferences({"selected_agent_id": "scout"})
        self.assertEqual(self.app.state()["selected_agent_id"], "scout")

    def test_real_growth_only_comes_from_recorded_evidence(self):
        self.done(self.send())
        state = self.app.state()
        self.assertEqual(state["agents"][0]["stats"]["accepted_tasks"], 0)
        self.assertEqual(state["agents"][0]["stats"]["verified_improvements"], 0)
        imported = {
            "work_items": [
                {"id": "a", "owner_id": "scout", "status": "accepted", "evidence": ["真实成果.md"]},
                {"id": "b", "owner_id": "scout", "status": "accepted"},
                {"id": "c", "owner_id": "scout", "status": "in_progress", "evidence": ["草稿.md"]}],
            "improvements": [
                {"member_id": "scout", "decision": "accepted", "problem_evidence": "对比记录", "baseline_result": 3, "candidate_result": 5, "reviewer": "领航", "regression_result": {"passed": True}},
                {"member_id": "scout", "decision": "accepted"},
                {"member_id": "scout", "decision": "accepted", "problem_evidence": "对比记录", "baseline_result": 3, "candidate_result": 5, "reviewer": "领航", "regression_result": {"passed": True, "critical_regressions": ["关键退步"]}},
                {"member_id": "linghang", "decision": "rejected", "problem_evidence": "对比记录", "baseline_result": 3, "candidate_result": 2, "reviewer": "用户"}]}
        (self.source / "state.json").write_text(json.dumps(imported), encoding="utf-8")
        result = self.app.state()
        scout = result["agents"][1]
        self.assertEqual(scout["stats"]["accepted_tasks"], 1)
        self.assertEqual(scout["stats"]["verified_improvements"], 1)
        self.assertEqual(result["agents"][0]["stats"]["verified_improvements"], 0)
        self.assertEqual(result["work_items"], imported["work_items"])
        self.assertEqual(scout["work_status"], "有进行中的工作")
        # Read-only integration: the source documents must remain byte-for-byte unchanged.
        self.assertEqual(json.loads((self.source / "state.json").read_text()), imported)

    def test_restart_marks_interrupted_job_failed(self):
        # A stopped process cannot leave an active slot stranded after reopening.
        with self.app.db() as db:
            db.execute("INSERT INTO messages VALUES ('user-x','scout','user','还没完成','pending','2026-11-09T00:00:00+00:00')")
            db.execute("INSERT INTO jobs (id,request_id,agent_id,user_message_id,status,created_at,updated_at) VALUES ('job-x','request-x','scout','user-x','running','2026-11-09','2026-11-09')")
        self.app.close()
        reopened = TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)
        self.assertEqual(reopened.job("job-x")["status"], "failed")
        self.assertIn("重新启动", reopened.job("job-x")["error"])
        self.assertEqual(reopened.messages("scout")["messages"][0]["status"], "failed")
        reopened.close()

    def test_close_stops_provider_and_drains_workers_without_deadlock(self):
        self.bridge.release.clear()
        close_called = threading.Event()
        def close_provider():
            close_called.set()
            self.bridge.release.set()
        self.bridge.close = close_provider
        job_id = self.send()
        self.assertTrue(self.bridge.started.wait(1))
        self.app.close()
        self.assertTrue(close_called.is_set())
        self.assertEqual(self.app.workers, {})
        self.assertEqual(self.app.job(job_id)["status"], "cancelled")
        self.assertEqual(len(self.app.messages("linghang")["messages"]), 1)
        self.app.close()  # Closing twice must remain harmless.

    def test_one_service_owns_each_data_directory(self):
        with self.assertRaisesRegex(RuntimeError, "另一个服务"):
            TownApp(self.root / "data", self.bridge, self.source, self.web, self.reports)

    def test_lead_sees_team_work_and_failed_messages_are_not_replayed(self):
        imported = {"work_items": [{"owner_id": "scout", "goal": "成员真实工作"}, {"owner_id": "another", "goal": "其他成员工作"}], "improvements": [{"member_id": "scout", "hypothesis": "真实改进"}]}
        (self.source / "state.json").write_text(json.dumps(imported), encoding="utf-8")
        with self.app.db() as db:
            db.execute("INSERT INTO messages VALUES ('failed','scout','user','失败消息不应重放','failed','2026-11-09')")
            db.execute("INSERT INTO messages VALUES ('cancel','scout','user','取消消息不应重放','cancelled','2026-11-09')")
        lead = self.app.prompt("linghang")
        scout = self.app.prompt("scout")
        self.assertIn("其他成员工作", lead)
        self.assertIn("成员真实工作", lead)
        self.assertIn("真实改进", lead)
        self.assertNotIn("其他成员工作", scout)
        self.assertNotIn("失败消息不应重放", scout)
        self.assertNotIn("取消消息不应重放", scout)
        self.assertEqual(len(self.app.messages("scout")["messages"]), 2)

    def test_history_is_limited_and_role_specific(self):
        with self.app.db() as db:
            for i in range(30):
                db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", ("m%s" % i, "linghang", "user", "甲" * 4000, "sent", "2026-11-09"))
            db.execute("INSERT INTO messages VALUES ('private','scout','user','星探私有消息','sent','2026-11-09')")
        prompt = self.app.prompt("linghang")
        data = json.loads(prompt.split("<town_data>\n", 1)[1].split("\n</town_data>", 1)[0])
        self.assertLessEqual(len(data["recent_messages"]), 20)
        self.assertLessEqual(sum(len(x["content"]) for x in data["recent_messages"]), 24000)
        self.assertNotIn("星探私有消息", prompt)


class HTTPTest(TownFixture):
    def setUp(self):
        super().setUp()
        self.server = create_server(port=0, data_dir=self.root / "http-data", bridge=self.bridge, source_dir=self.source, web_dir=self.web, report_root=self.reports)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        super().tearDown()

    def request(self, method, path, data=None, extra=None):
        headers = {"Content-Type": "application/json", "X-Town-Client": "pixel-town"}
        if extra:
            headers.update(extra)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=json.dumps(data or {}).encode() if method not in ("GET", "HEAD") else None, headers=headers)
        result = connection.getresponse()
        status, response_headers, body = result.status, dict(result.getheaders()), result.read()
        connection.close()
        return status, response_headers, body

    def test_origin_host_and_client_headers(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        valid_origin = "http://127.0.0.1:%s" % self.port
        self.assertEqual(self.request("POST", "/api/preferences", {"selected_agent_id": "scout"}, {"Origin": valid_origin})[0], 200)
        self.assertEqual(self.request("POST", "/api/preferences", {"selected_agent_id": "scout"}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", extra={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/preferences", {"selected_agent_id": "scout"}, {"X-Town-Client": ""})[0], 403)
        self.assertEqual(self.request("POST", "/api/preferences", {"selected_agent_id": "scout"}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("OPTIONS", "/api/chat", extra={"Origin": "https://evil.example"})[0], 403)

    def test_paths_reports_and_csp(self):
        report = self.reports / "2026-11-09"
        report.mkdir()
        (report / "report.md").write_text("# 今日报告\n\n可追溯的真实研究摘要。", encoding="utf-8")
        (report / "report.html").write_text("<script>fetch('/api/agents')</script><p>真实报告</p>", encoding="utf-8")
        (report / "secret.json").write_text("private", encoding="utf-8")
        self.assertEqual(self.request("GET", "/reports/2026-11-09/secret.json")[0], 403)
        for path in ("/../source/config.json", "/%2e%2e/source/config.json", "/reports/2026-11-09/%2e%2e/report.html", "/%5c../config.json", "/%00"):
            self.assertEqual(self.request("GET", path)[0], 403, path)
        (self.web / "leak.txt").symlink_to(self.source / "config.json")
        self.assertEqual(self.request("GET", "/leak.txt")[0], 403)
        status, headers, body = self.request("GET", "/reports/2026-11-09/report.html")
        self.assertEqual(status, 200)
        self.assertIn("sandbox;", headers["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("allow-scripts", headers["Content-Security-Policy"])
        state = json.loads(self.request("GET", "/api/state")[2])
        self.assertEqual(len(state["reports"]), 1)
        self.assertIn("可追溯", state["reports"][0]["summary"])
        self.assertEqual(state["agents"][1]["stats"]["accepted_tasks"], 0)

    def test_http_async_and_input_validation(self):
        self.bridge.release.clear()
        before = time.monotonic()
        status, _, body = self.request("POST", "/api/chat", {"agent_id": "scout", "message": "聊聊天", "request_id": "api-1"})
        self.assertEqual(status, 202)
        self.assertLess(time.monotonic() - before, 1)
        job_id = json.loads(body)["job_id"]
        self.assertEqual(self.request("GET", "/api/jobs/" + job_id)[0], 200)
        self.assertEqual(self.request("POST", "/api/jobs/" + job_id + "/cancel")[0], 200)
        self.assertEqual(self.request("POST", "/api/chat", {"agent_id": "scout", "message": "很" * 8001, "request_id": "api-2"})[0], 400)
        self.assertEqual(self.request("PATCH", "/api/agents/scout", {"memories": ["a"] * 21})[0], 400)
        self.assertEqual(self.request("POST", "/api/agents", {"name": "新伙伴", "role": "观察员", "color": "url(javascript:bad)"})[0], 400)
        status, _, body = self.request("POST", "/api/agents", {"name": "新伙伴", "role": "观察员"})
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(body)["agent"]["id"].startswith("resident-"))
        self.bridge.release.set()


if __name__ == "__main__":
    unittest.main()
