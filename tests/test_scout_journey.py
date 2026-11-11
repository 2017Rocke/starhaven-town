"""Offline journey timing, persistent delivery, and truthful material counts."""
import datetime as dt
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scout_journey import journey_state
from scout_workshop import LOCAL_TZ, ScoutWorkshop, WorkshopError
from test_scout_workshop import FakePublicSource


class ScoutJourneyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.started = dt.datetime(2026, 11, 11, 8, 59, 50, tzinfo=LOCAL_TZ)
        self.current = self.started
        self.source = FakePublicSource()
        self.instances = []
        self.desk = self.open_desk()

    def open_desk(self):
        desk = ScoutWorkshop(self.root, discover=self.source.discover, collect=self.source.collect,
                             clock=lambda: self.current, start_scheduler=False)
        self.instances.append(desk)
        return desk

    def tearDown(self):
        if self.source.gate:
            self.source.gate.set()
        for desk in self.instances:
            desk.close()
        self.temporary.cleanup()

    def at(self, seconds, desk=None):
        self.current = self.started + dt.timedelta(seconds=seconds)
        return (desk or self.desk).workspace()

    def finish(self, desk=None):
        desk = desk or self.desk
        deadline = time.monotonic() + 5
        while desk.worker is not None and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertIsNone(desk.worker, "offline collection did not complete")
        return desk.workspace()

    def test_fast_success_saves_before_walking_and_only_racks_after_unloading(self):
        self.desk.start_collect({"limit": 1})
        saved = self.finish()
        material = saved["materials"][0]
        self.assertTrue((self.desk.material_root / material["id"] / "main.py").is_file())
        self.assertEqual(saved["job"]["phase"], "completed")
        self.assertEqual(saved["journey"]["phase"], "outbound")
        self.assertEqual(saved["journey"]["cargo_count"], 1)
        self.assertEqual((saved["counts"]["raw"], saved["counts"]["rack"], saved["counts"]["in_transit"]), (1, 0, 1))
        self.assertEqual(material["delivery"], "in_transit")
        self.assertIsNone(material["delivered_at"])
        for seconds, expected_phase, progress in ((6, "outbound", .5), (12, "working", 1),
                (14, "returning", 1), (20, "returning", .5), (26, "unloading", 0)):
            with self.subTest(seconds=seconds):
                state = self.at(seconds)
                self.assertEqual(state["journey"]["phase"], expected_phase)
                self.assertAlmostEqual(state["journey"]["route_progress"], progress)
                self.assertEqual(state["counts"]["rack"], 0)
                self.assertFalse(state["journey"]["can_start"])
        home = self.at(28)
        self.assertEqual(home["journey"]["phase"], "home")
        self.assertEqual((home["counts"]["rack"], home["counts"]["in_transit"]), (1, 0))
        detail = self.desk.material_details(material["id"])["material"]
        self.assertEqual(detail["delivery"], "delivered")
        self.assertEqual(detail["delivered_at"], home["journey"]["home_at"])

    def test_long_work_waits_in_forest_without_inventing_return_times_or_cargo(self):
        self.source.gate = threading.Event()
        self.desk.start_collect({"limit": 1})
        state = self.at(70)
        self.assertEqual(state["journey"]["phase"], "working")
        self.assertEqual(state["journey"]["route_progress"], 1)
        self.assertEqual(state["journey"]["cargo_count"], 0)
        self.assertIsNone(state["journey"]["return_started_at"])
        self.assertFalse(state["journey"]["can_start"])
        self.source.gate.set()
        finished = self.finish()
        self.assertEqual(finished["journey"]["phase"], "returning")
        self.assertEqual(finished["journey"]["return_started_at"], self.current.isoformat())
        self.assertEqual(self.at(84)["counts"]["rack"], 1)

    def test_early_failure_turns_back_at_current_distance_and_explains_rate_limit(self):
        def unavailable(**kwargs):
            self.current = self.started + dt.timedelta(seconds=4)
            return dict(status="failed", failures=[dict(message="GitHub 匿名请求遇到限流，请稍后重试。")])
        self.desk._discover = unavailable
        self.desk.start_collect()
        failed = self.finish()
        self.assertEqual(failed["job"]["phase"], "failed")
        self.assertEqual(failed["journey"]["phase"], "returning")
        self.assertAlmostEqual(failed["journey"]["return_from_progress"], 1 / 3)
        self.assertEqual(failed["journey"]["cargo_count"], 0)
        self.assertIn("限流", failed["journey"]["message"])
        self.assertAlmostEqual(self.at(6)["journey"]["route_progress"], 1 / 6)
        home = self.at(8)
        self.assertEqual(home["journey"]["phase"], "home")
        self.assertEqual(home["journey"]["home_at"], home["journey"]["arrival_at"])
        self.assertTrue(home["journey"]["can_start"])
        self.assertEqual(home["counts"]["rack"], 0)

    def test_partial_success_brings_only_available_successful_projects(self):
        self.source.candidates = [dict(full_name="sample/good", repo_id=1), dict(full_name="sample/broken", repo_id=2)]
        self.source.fail_names.add("sample/broken")
        self.desk.start_collect({"limit": 2})
        state = self.finish()
        self.assertEqual(state["job"]["phase"], "partial")
        self.assertEqual(state["journey"]["cargo_count"], 1)
        self.assertEqual(state["journey"]["outcome"], "partial")
        self.assertIn("部分", self.at(15)["journey"]["message"])
        self.assertEqual(self.at(30)["counts"]["rack"], 1)

    def test_old_materials_are_never_loaded_into_a_new_empty_trip(self):
        self.desk.start_collect({"limit": 1})
        self.finish()
        self.at(30)
        self.source.gate = threading.Event()
        second = self.desk.start_collect({"limit": 1})
        state = self.at(45)
        self.assertEqual(state["journey"]["job_id"], second["job"]["id"])
        self.assertEqual(state["journey"]["cargo_count"], 0)
        self.assertEqual((state["counts"]["rack"], state["counts"]["in_transit"]), (1, 0))
        self.source.gate.set()
        self.finish()
        empty = self.at(60)
        self.assertEqual(empty["journey"]["phase"], "home")
        self.assertEqual(empty["journey"]["outcome"], "empty")
        self.assertEqual(empty["counts"]["rack"], 1)

    def test_repeat_manual_and_daily_departure_wait_for_resident_to_return(self):
        self.desk.start_collect({"limit": 1})
        self.finish()
        with self.assertRaises(WorkshopError) as caught:
            self.desk.start_collect({"limit": 1})
        self.assertEqual(caught.exception.code, 409)
        self.desk.update_settings(dict(daily_enabled=True))
        with self.desk.db() as db:
            db.execute("UPDATE settings SET value='null' WHERE key='last_run_date'")
        self.at(15)
        self.assertFalse(self.desk.maybe_schedule())
        self.assertIsNone(self.desk.settings()["last_run_date"])
        self.at(30)
        self.assertTrue(self.desk.maybe_schedule())
        self.finish()
        self.assertFalse(self.desk.maybe_schedule())

    def test_restart_resumes_return_and_finishes_delivery_without_browser_confirmation(self):
        self.desk.start_collect({"limit": 1})
        self.finish()
        self.at(20)
        self.desk.close()
        reopened = self.open_desk()
        returning = reopened.workspace()
        self.assertEqual(returning["journey"]["phase"], "returning")
        self.assertAlmostEqual(returning["journey"]["route_progress"], .5)
        self.assertEqual(returning["counts"]["in_transit"], 1)
        self.current = self.started + dt.timedelta(seconds=50)
        # Daily scheduling also settles deliveries even while disabled.
        self.assertFalse(reopened.maybe_schedule())
        with reopened.db() as db:
            marker = db.execute("SELECT journey_delivered_at FROM collection_jobs").fetchone()[0]
        self.assertTrue(marker)
        state = reopened.workspace()
        self.assertEqual((state["counts"]["rack"], state["counts"]["in_transit"]), (1, 0))
        self.assertEqual(len(self.source.collections), 1)

    def test_missing_materials_are_not_shown_as_cargo_or_rack_and_do_not_change_route(self):
        self.desk.start_collect({"limit": 1})
        state = self.finish()
        material = state["materials"][0]
        planned_home = state["journey"]["home_at"]
        shutil.rmtree(self.desk.material_root / material["id"])
        returning = self.at(15)
        self.assertEqual(returning["journey"]["phase"], "returning")
        self.assertEqual(returning["journey"]["home_at"], planned_home)
        self.assertEqual(returning["journey"]["cargo_count"], 0)
        self.assertIn("文件目前不可用", returning["journey"]["message"])
        self.assertEqual(returning["counts"]["in_transit"], 0)
        home = self.at(30)
        self.assertEqual(home["counts"]["materials"], 1)
        self.assertEqual(home["counts"]["rack"], 0)
        self.assertIn("sample/tools", self.desk._seen()[0])

    def test_delivery_is_idempotent_and_does_not_reverse_after_clock_correction(self):
        self.desk.start_collect({"limit": 1})
        self.finish()
        for seconds in (28, 28, 40, 10, 90):
            with self.subTest(seconds=seconds):
                state = self.at(seconds)
                self.assertEqual(state["journey"]["phase"], "home")
                self.assertEqual((state["counts"]["rack"], state["counts"]["in_transit"]), (1, 0))
                self.assertEqual(state["materials"][0]["delivery"], "delivered")
        with self.desk.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM materials").fetchone()[0], 1)
        self.assertEqual(len(self.source.collections), 1)

    def test_legacy_schema_migration_keeps_existing_materials_home(self):
        self.desk.start_collect({"limit": 1})
        self.finish()
        self.desk.close()
        # Recreate the exact pre-journey job schema without touching real files.
        with self.desk.db() as db:
            db.executescript("""
                CREATE TABLE old_collection_jobs AS SELECT id,phase,message,completed,total,
                    attempted,skipped,failed,started_at,finished_at,trigger,errors,recent_events
                    FROM collection_jobs;
                DROP TABLE collection_jobs;
                ALTER TABLE old_collection_jobs RENAME TO collection_jobs;
            """)
        reopened = self.open_desk()
        state = reopened.workspace()
        self.assertEqual(state["job"]["journey_version"], 0)
        self.assertEqual(state["journey"]["phase"], "home")
        self.assertTrue(state["journey"]["can_start"])
        self.assertEqual((state["counts"]["rack"], state["counts"]["in_transit"]), (1, 0))
        self.assertEqual(state["materials"][0]["delivery"], "delivered")
        self.assertIsNone(state["journey"]["return_started_at"])

    def test_interrupted_job_with_saved_bundle_returns_it_after_restart(self):
        gate = threading.Event()
        original_collect = self.source.collect
        def collect(candidate, destination, **kwargs):
            if candidate["full_name"] == "sample/second":
                gate.set()
                while not kwargs["cancel_event"].wait(.01):
                    pass
                raise RuntimeError("interrupted test")
            return original_collect(candidate, destination, **kwargs)
        self.desk._collect = collect
        self.source.candidates = [dict(full_name="sample/first", repo_id=1), dict(full_name="sample/second", repo_id=2)]
        self.desk.start_collect({"limit": 2})
        self.assertTrue(gate.wait(3))
        self.at(18)
        self.desk.close()
        reopened = self.open_desk()
        state = reopened.workspace()
        self.assertEqual(state["job"]["phase"], "interrupted")
        self.assertEqual(state["journey"]["phase"], "returning")
        self.assertEqual(state["journey"]["cargo_count"], 1)
        self.assertEqual(state["counts"]["in_transit"], 1)
        self.assertEqual(self.at(32, reopened)["counts"]["rack"], 1)

    def test_pure_timing_handles_empty_and_prestart_clocks(self):
        empty = journey_state({}, self.current)
        self.assertTrue(empty["can_start"])
        self.assertEqual(empty["phase"], "home")
        job = dict(id="test", started_at=(self.current + dt.timedelta(seconds=5)).isoformat(),
                   phase="searching", completed=0, journey_version=1)
        state = journey_state(job, self.current)
        self.assertEqual(state["phase"], "outbound")
        self.assertEqual(state["route_progress"], 0)
        self.assertFalse(state["can_start"])


if __name__ == "__main__":
    unittest.main()
