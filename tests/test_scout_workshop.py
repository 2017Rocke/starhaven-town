"""Real temporary files and persistent jobs; public HTTP is replaced offline."""
import concurrent.futures
import datetime as dt
import http.client
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scout_workshop import LOCAL_TZ, ScoutWorkshop, WorkshopError, plain_text

SHA = "a" * 40


class FakePublicSource:
    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [dict(full_name="sample/tools", repo_id=101)]
        self.discoveries, self.collections = [], []
        self.fail_names = set()
        self.gate = None

    def discover(self, *, limit, seen_repo_ids, seen_full_names, cancel_event, progress):
        self.discoveries.append(dict(ids=set(seen_repo_ids), names=set(seen_full_names), limit=limit))
        if self.gate:
            while not self.gate.wait(.01):
                if cancel_event.is_set():
                    raise RuntimeError("cancelled test discovery")
        return dict(candidates=self.candidates, failures=[], status="ok", partial=False)

    def collect(self, candidate, destination, *, cancel_event, progress):
        self.collections.append(candidate["full_name"])
        progress(dict(stage="download", message="正在取回固定版本源码。"))
        destination.mkdir()
        (destination / "README.md").write_text(
            "# Example project\nUseful source. <script>do_not_run()</script>\n"
            "api_key=sk-" + "X" * 30 + "\n", encoding="utf-8")
        (destination / "main.py").write_text("# Static data only; never executed.\n", encoding="utf-8")
        if candidate["full_name"] in self.fail_names:
            raise OSError("simulated write failure")
        progress(dict(stage="scan", message="正在标注静态线索。"))
        return dict(candidate, description="Useful tool", category="开发工具", stars=42, language="Python",
                    license="MIT", commit_sha=SHA, source_status="ok", files=["README.md", "main.py"],
                    readme_path="README.md", file_count=2, scan=dict(level="medium", findings=[
                        dict(path="main.py", line=1, severity="medium", title="待复核线索", snippet="example", reason="静态示例")],
                    limitations=["未运行源码"]))


class WorkshopTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.source = FakePublicSource()
        self.current = dt.datetime(2026, 11, 11, 8, 30, tzinfo=LOCAL_TZ)
        self.instances = []
        self.desk = self.open_desk()

    def open_desk(self):
        result = ScoutWorkshop(self.root / "data", self.reports, discover=self.source.discover,
                               collect=self.source.collect, clock=lambda: self.current, start_scheduler=False)
        self.instances.append(result)
        return result

    def tearDown(self):
        if self.source.gate:
            self.source.gate.set()
        for instance in self.instances:
            instance.close()
        self.temporary.cleanup()

    def done(self, desk=None):
        desk = desk or self.desk
        deadline = time.monotonic() + 5
        while desk.worker is not None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNone(desk.worker, "collection did not finish")
        return desk.workspace()["job"]

    def history_day(self, date="2026-11-09", with_source=True, with_report=True):
        day = self.reports / date
        day.mkdir(exist_ok=True)
        if with_source:
            source = day / "sources" / "sample--old"
            source.mkdir(parents=True)
            (source / "README.md").write_text("# Historical source\nReal old material.", encoding="utf-8")
        evidence = dict(date=date, generated_at=date + "T09:12:00+08:00", projects=[dict(
            full_name="sample/old", source_dir="sources/sample--old", sha=SHA,
            source_status="ok", stars=31, categories=["知识工具"], description="Historical project")])
        (day / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        if with_report:
            (day / "review.json").write_text(json.dumps(dict(projects=[dict(full_name="sample/old")])), encoding="utf-8")
            (day / "report.md").write_text("# Actual research report\nReviewed findings, not raw material.", encoding="utf-8")
            (day / "report.html").write_text("<!doctype html><p>Existing report</p>", encoding="utf-8")
        return day

    def test_default_daily_is_off_and_empty_desk_has_no_invented_books(self):
        state = self.desk.workspace()
        self.assertEqual(state["job"]["phase"], "idle")
        self.assertEqual(state["materials"], [])
        self.assertEqual(state["books"], [])
        self.assertFalse(state["settings"]["daily_enabled"])
        self.current = self.current.replace(hour=12)
        self.assertFalse(self.desk.maybe_schedule())
        self.assertEqual(self.source.discoveries, [])

    def test_collect_persists_real_files_origin_and_redacted_plain_text(self):
        self.desk.start_collect({"limit": 1})
        job = self.done()
        self.assertEqual((job["phase"], job["completed"], job["total"]), ("completed", 1, 1))
        state = self.desk.workspace()
        self.assertEqual(state["books"], [])
        material = state["materials"][0]
        self.assertEqual(material["file_count"], 2)
        self.assertEqual(material["sha"], SHA)
        self.assertEqual(material["owner"], dict(name="sample", url="https://github.com/sample"))
        self.assertTrue((self.desk.material_root / material["id"] / "main.py").is_file())
        detail = self.desk.material_details(material["id"])["material"]
        self.assertIn("Useful source", detail["readme"])
        self.assertNotIn("do_not_run", detail["readme"])
        self.assertNotIn("X" * 30, detail["readme"])
        self.assertEqual(detail["files"], ["README.md", "main.py"])
        self.assertIn("/blob/" + SHA + "/main.py#L1", detail["evidence"][0]["url"])
        self.assertIn("不构成安全认证", "".join(detail["risk"]["limitations"]))
        self.assertEqual([item["phase"] for item in job["recent_events"]],
                         ["searching", "collecting", "storing", "storing", "completed"])

    def test_partial_source_retains_coverage_and_never_claims_complete_collection(self):
        def collect(candidate, destination, **kwargs):
            raw = self.source.collect(candidate, destination, **kwargs)
            raw.update(source_status="partial", extraction=dict(file_count=2, bytes_written=123,
                       skipped=dict(links_or_special_files=2, submodules_not_fetched=1)))
            raw["scan"]["coverage"] = dict(files_seen=2, files_scanned=1, bytes_scanned=42,
                enumeration_complete=True, findings_truncated=True, findings_matched=6, findings_suppressed=5,
                skipped_by_reason=dict(binary_content=1), limits=dict(files=3000),
                untrusted="must not be returned")
            return raw

        self.desk._collect = collect
        self.desk.start_collect({"limit": 1})
        job = self.done()
        self.assertEqual((job["phase"], job["completed"], job["failed"]), ("partial", 1, 0))
        self.assertEqual(job["errors"][0]["kind"], "source_partial")
        self.assertIn("3 项", job["errors"][0]["message"])
        material = self.desk.workspace()["materials"][0]
        detail = self.desk.material_details(material["id"])["material"]
        self.assertEqual(detail["extraction"]["skipped"]["links_or_special_files"], 2)
        self.assertEqual(detail["coverage"]["files_scanned"], 1)
        self.assertEqual(detail["coverage"]["limits"], dict(files=3000))
        self.assertNotIn("untrusted", detail["coverage"])

    def test_failed_candidate_uses_distinct_reserve_with_bounded_attempts(self):
        self.source.fail_names = {"sample/broken"}
        self.desk._discover = lambda **kwargs: dict(candidates=[dict(full_name="sample/broken", repo_id=1)],
            reserve_candidates=[dict(full_name="SAMPLE/BROKEN", repo_id=1), dict(full_name="sample/spare", repo_id=2)])
        self.desk.start_collect({"limit": 1})
        job = self.done()
        self.assertEqual((job["phase"], job["completed"], job["attempted"]), ("partial", 1, 2))
        self.assertEqual(self.source.collections, ["sample/broken", "sample/spare"])
        self.assertEqual(self.desk.workspace()["materials"][0]["full_name"], "sample/spare")

    def test_manifest_name_collision_never_overwrites_repository_file(self):
        pending = self.desk.pending_root / "reserved-file"
        raw = self.source.collect(self.source.candidates[0], pending, cancel_event=threading.Event(), progress=lambda _: None)
        reserved = pending / ".starhaven-material.json"
        reserved.write_text("original repository content", encoding="utf-8")
        with self.assertRaises(WorkshopError):
            self.desk._publish(pending, raw)
        self.assertEqual(reserved.read_text(encoding="utf-8"), "original repository content")
        self.assertEqual(self.desk.workspace()["materials"], [])

    def test_repeated_and_renamed_projects_are_deduplicated_across_restarts(self):
        self.desk.start_collect()
        self.done()
        self.desk.close()
        reopened = self.open_desk()
        self.source.candidates = [dict(full_name="SAMPLE/TOOLS", repo_id=101), dict(full_name="new-owner/renamed", repo_id=101)]
        reopened.start_collect()
        job = self.done(reopened)
        self.assertEqual((job["completed"], job["skipped"]), (0, 2))
        self.assertEqual(len(reopened.workspace()["materials"]), 1)
        self.assertIn("sample/tools", self.source.discoveries[-1]["names"])
        self.assertIn(101, self.source.discoveries[-1]["ids"])
        self.assertEqual(self.source.collections, ["sample/tools"])

    def test_concurrent_clicks_start_one_real_job(self):
        self.source.gate = threading.Event()

        def launch(_):
            try:
                return self.desk.start_collect()["job"]["id"]
            except WorkshopError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(launch, range(8)))
        self.assertEqual(sum(isinstance(value, str) for value in results), 1)
        self.assertEqual(results.count(409), 7)
        self.source.gate.set()
        self.done()
        self.assertEqual(len(self.source.discoveries), 1)

    def test_partial_failure_only_indexes_successfully_written_materials(self):
        self.source.candidates = [dict(full_name="sample/good", repo_id=1), dict(full_name="sample/broken", repo_id=2)]
        self.source.fail_names.add("sample/broken")
        self.desk.start_collect({"limit": 2})
        job = self.done()
        self.assertEqual((job["phase"], job["completed"], job["failed"], job["total"]), ("partial", 1, 1, 2))
        self.assertEqual([item["full_name"] for item in self.desk.workspace()["materials"]], ["sample/good"])
        self.assertEqual(list(self.desk.pending_root.iterdir()), [])
        self.assertNotIn("sample/broken", self.desk._seen()[0])
        self.source.fail_names.clear()
        self.desk.start_collect({"limit": 2})
        self.done()
        self.assertEqual(len(self.desk.workspace()["materials"]), 2)

    def test_no_files_and_unavailable_discovery_never_claim_success(self):
        self.desk._collect = lambda candidate, destination, **kwargs: dict(candidate, commit_sha=SHA, files=[])
        self.desk.start_collect()
        self.assertEqual(self.done()["phase"], "failed")
        self.assertEqual(self.desk.workspace()["materials"], [])
        self.desk._discover = lambda **kwargs: dict(candidates=[], failures=[dict(message="来源不可用")], status="partial", partial=True)
        self.desk.start_collect()
        job = self.done()
        self.assertEqual(job["phase"], "failed")
        self.assertIn("不能确认", job["message"])

    def test_closing_recovers_interrupted_status_without_fabricating_materials(self):
        self.source.gate = threading.Event()
        self.desk.start_collect()
        self.desk.close()
        reopened = self.open_desk()
        self.assertEqual(reopened.workspace()["job"]["phase"], "interrupted")
        self.assertEqual(reopened.workspace()["job"]["completed"], 0)
        self.assertEqual(reopened.workspace()["materials"], [])
        self.source.gate.set()
        reopened.start_collect()
        self.assertEqual(self.done(reopened)["completed"], 1)

    def test_complete_files_are_recovered_after_index_commit_failure(self):
        identity = uuid.uuid4().hex
        with self.desk.db() as db:
            db.execute("INSERT INTO collection_jobs(id,phase,message,total,started_at,trigger) VALUES (?,?,?,?,?,?)",
                       (identity, "storing", "test", 1, self.current.isoformat(), "manual"))
        pending = self.desk.pending_root / "completed-bundle"
        raw = self.source.collect(self.source.candidates[0], pending, cancel_event=threading.Event(), progress=lambda _: None)
        with mock.patch.object(self.desk, "_insert_material", side_effect=sqlite3.OperationalError("test index failure")):
            with self.assertRaises(sqlite3.OperationalError):
                self.desk._publish(pending, raw, job_id=identity)
        self.assertEqual(self.desk.workspace()["materials"], [])
        self.desk.close()
        reopened = self.open_desk()
        self.assertEqual(len(reopened.workspace()["materials"]), 1)
        self.assertEqual(reopened.workspace()["job"]["phase"], "interrupted")
        self.assertEqual(reopened.workspace()["job"]["completed"], 1)

    def test_daily_schedule_runs_once_and_only_catches_up_current_day(self):
        self.desk.update_settings(dict(daily_enabled=True))
        self.assertFalse(self.desk.maybe_schedule())
        self.current = self.current.replace(hour=9)
        self.assertTrue(self.desk.maybe_schedule())
        self.done()
        self.assertFalse(self.desk.maybe_schedule())
        self.desk.close()
        self.current += dt.timedelta(days=4, hours=3)
        reopened = self.open_desk()
        self.assertTrue(reopened.maybe_schedule())
        self.done(reopened)
        self.assertFalse(reopened.maybe_schedule())
        self.assertEqual(len(self.source.discoveries), 2)
        self.assertEqual(reopened.settings()["last_run_date"], self.current.date().isoformat())

    def test_existing_report_prevents_duplicate_daily_collection(self):
        self.history_day(date=self.current.date().isoformat())
        self.current = self.current.replace(hour=11)
        self.desk.update_settings(dict(daily_enabled=True))
        self.assertFalse(self.desk.maybe_schedule())
        self.assertEqual(self.source.discoveries, [])
        self.assertEqual(self.desk.settings()["last_run_date"], self.current.date().isoformat())

    def test_historical_sources_are_read_only_and_keep_dates_and_real_books(self):
        day = self.history_day()
        missing = self.history_day(date="2026-11-08", with_source=False, with_report=False)
        evidence = json.loads((missing / "evidence.json").read_text())
        evidence["projects"][0]["full_name"] = "sample/missing"
        (missing / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        before = {str(path): path.read_bytes() for path in self.reports.rglob("*") if path.is_file()}
        self.desk.close()
        reopened = self.open_desk()
        state = reopened.workspace()
        self.assertEqual(len(state["materials"]), 1)
        material = state["materials"][0]
        self.assertEqual(material["collected_at"], "2026-11-09T09:12:00+08:00")
        detail = reopened.material_details(material["id"])["material"]
        self.assertEqual(detail["provenance"]["source_date"], "2026-11-09")
        self.assertIn("sample/missing", reopened._seen()[0])
        self.assertEqual(len(state["books"]), 1)
        self.assertEqual(state["books"][0]["url"], "/reports/2026-11-09/report.html")
        self.assertEqual({str(path): path.read_bytes() for path in self.reports.rglob("*") if path.is_file()}, before)
        self.assertTrue((day / "sources" / "sample--old" / "README.md").is_file())

    def test_history_paths_and_material_ids_cannot_read_arbitrary_files(self):
        day = self.history_day(with_source=False, with_report=False)
        secret = self.root / "private"
        secret.mkdir()
        (secret / "README.md").write_text("must not be imported", encoding="utf-8")
        evidence = json.loads((day / "evidence.json").read_text())
        evidence["projects"][0]["source_dir"] = "../../private"
        (day / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        self.desk.close()
        reopened = self.open_desk()
        self.assertEqual(reopened.workspace()["materials"], [])
        for invalid in ("../../private/README.md", "z" * 32, "a" * 32):
            with self.subTest(identity=invalid), self.assertRaises(WorkshopError) as error:
                reopened.material_details(invalid)
            self.assertEqual(error.exception.code, 404)

    def test_removed_files_are_reported_missing_without_erasing_history(self):
        self.desk.start_collect()
        self.done()
        material = self.desk.workspace()["materials"][0]
        shutil.rmtree(self.desk.material_root / material["id"])
        detail = self.desk.material_details(material["id"])["material"]
        self.assertEqual(detail["source_status"], "missing")
        self.assertEqual(detail["files_available"], 0)
        self.assertIn("sample/tools", self.desk._seen()[0])

    def test_list_and_detail_agree_when_first_or_later_files_disappear(self):
        for filename in ("README.md", "main.py"):
            with self.subTest(filename=filename):
                self.source.candidates = [dict(full_name="sample/" + filename.replace(".", "-"))]
                self.desk.start_collect({"limit": 1})
                self.done()
                material = next(item for item in self.desk.workspace()["materials"]
                                if item["full_name"] == self.source.candidates[0]["full_name"])
                (self.desk.material_root / material["id"] / filename).unlink()
                # Polling uses its recent check; opening details immediately
                # refreshes the shared availability value for both surfaces.
                cached = next(item for item in self.desk.workspace()["materials"] if item["id"] == material["id"])
                self.assertEqual(cached["files_available"], 2)
                self.assertTrue(cached["availability_checked_at"])
                detailed = self.desk.material_details(material["id"])["material"]
                listed = next(item for item in self.desk.workspace()["materials"] if item["id"] == material["id"])
                self.assertEqual((listed["source_status"], listed["files_available"]), ("partial", 1))
                self.assertEqual(listed["source_status"], detailed["source_status"])

    def test_settings_validation_is_atomic_and_does_not_accept_paths(self):
        initial = self.desk.settings()
        for data in ({"daily_enabled": 1}, {"daily_time": "24:00"}, {"daily_limit": True},
                     {"daily_limit": 6}, {"timezone": "UTC"}, {"source_dir": str(self.root)},
                     {"daily_enabled": True, "daily_time": "bad"}):
            with self.subTest(data=data), self.assertRaises(WorkshopError):
                self.desk.update_settings(data)
            self.assertEqual(self.desk.settings(), initial)
        self.desk.update_settings(dict(daily_time="18:30", daily_limit=2))
        self.desk.close()
        self.assertEqual(self.open_desk().settings()["daily_time"], "18:30")

    def test_plain_text_removes_html_and_common_secrets(self):
        text = plain_text("<h1>Title</h1><script>alert('bad')</script>\npassword=example-secret\n"
                          '"password": "json secret with spaces"\nAuthorization: Bearer opaque-credential-value\n'
                          "-----BEGIN PRIVATE KEY-----\nsecret body without closing boundary")
        self.assertIn("Title", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("example-secret", text)
        self.assertNotIn("json secret", text)
        self.assertNotIn("opaque-credential", text)
        self.assertNotIn("secret body", text)
        jwt = "eyJ" + "a" * 18 + "." + "b" * 20 + "." + "c" * 20
        self.assertNotIn(jwt, plain_text("Example credential " + jwt))

    def test_chat_summary_contains_real_counts_without_repository_content(self):
        self.desk.start_collect({"limit": 1})
        self.done()
        summary = self.desk.chat_summary()
        self.assertEqual(summary["materials"], 1)
        self.assertEqual(summary["research_reports"], 0)
        self.assertEqual(summary["last_collection"]["completed"], 1)
        text = json.dumps(summary)
        for excluded in ("sample/tools", "Useful source", "readme", "snippet", "evidence"):
            self.assertNotIn(excluded, text)


class ScoutHTTPTest(unittest.TestCase):
    def test_workspace_routes_and_write_protection(self):
        from server import create_server

        class Bridge:
            def probe(self):
                return dict(available=False, authenticated=False)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = create_server(port=0, data_dir=root / "data", bridge=Bridge(),
                                   source_dir=root / "source", report_root=root / "reports", web_dir=root / "web")
            source = FakePublicSource()
            server.app.scout._discover, server.app.scout._collect = source.discover, source.collect
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, data=None, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    merged = {"Content-Type": "application/json", "X-Town-Client": "pixel-town"}
                    merged.update(headers or {})
                    connection.request(method, path, json.dumps(data or {}) if method != "GET" else None, merged)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                status, state = request("GET", "/api/scout/workspace")
                self.assertEqual(status, 200)
                self.assertEqual(state["materials"], [])
                self.assertEqual(request("POST", "/api/scout/collect", headers={"X-Town-Client": ""})[0], 403)
                self.assertEqual(request("PATCH", "/api/scout/settings", {"daily_limit": 1})[0], 200)
                self.assertEqual(request("PATCH", "/api/scout/settings", {"source_dir": str(root)})[0], 400)
                self.assertEqual(request("POST", "/api/scout/collect")[0], 202)
                deadline = time.monotonic() + 5
                while server.app.scout.worker is not None and time.monotonic() < deadline:
                    time.sleep(.01)
                material = request("GET", "/api/scout/workspace")[1]["materials"][0]
                status, detail = request("GET", "/api/scout/materials/" + material["id"])
                self.assertEqual(status, 200)
                self.assertIn("readme", detail["material"])
                self.assertEqual(request("GET", "/api/scout/materials/" + "a" * 32)[0], 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
