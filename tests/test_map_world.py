"""Map migration and resident behavior, using temporary data only."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import (
    APIError, CORE_H, CORE_W, CORE_X, CORE_Y, MAX_RESIDENTS,
    PLOT_H, PLOT_SLOTS, PLOT_W, TownApp, WORLD_H, WORLD_W, create_server, design_house, painted_house,
)


class OfflineBridge:
    def probe(self):
        return {"available": False, "authenticated": False, "status": "test"}


class MapWorldTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.app = None

    def tearDown(self):
        if self.app is not None:
            self.app.close()
        self.temp.cleanup()

    def start(self):
        if self.app is not None:
            self.app.close()
            self.app = None
        self.app = TownApp(self.data, OfflineBridge(), self.root / "source",
                           self.root / "web", self.root / "reports")
        return self.app

    def agents(self):
        return {agent["id"]: agent for agent in self.app.state()["agents"]}

    def test_local_map_service_starts_without_hostname_lookup(self):
        with mock.patch("socket.getfqdn", side_effect=AssertionError("local startup must not resolve DNS")):
            with create_server(port=0, data_dir=self.data, bridge=OfflineBridge(),
                               source_dir=self.root / "source", web_dir=self.root / "web",
                               report_root=self.root / "reports") as server:
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertGreater(server.server_port, 0)

    def test_plots_fit_world_without_overlapping_homes_or_village(self):
        # Full legacy towns must fit, including their two original buildings.
        self.assertEqual(MAX_RESIDENTS, 30)
        self.assertGreaterEqual(len(PLOT_SLOTS), 28)
        regions = [(CORE_X, CORE_Y, CORE_W, CORE_H)]
        for slot, (cx, cy) in enumerate(PLOT_SLOTS):
            x, y = cx - PLOT_W // 2, cy - PLOT_H // 2
            with self.subTest(slot=slot):
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + PLOT_W, WORLD_W)
                self.assertLessEqual(y + PLOT_H, WORLD_H)
                for rx, ry, rw, rh in regions:
                    self.assertFalse(x < rx + rw and rx < x + PLOT_W
                                     and y < ry + rh and ry < y + PLOT_H)
            regions.append((x, y, PLOT_W, PLOT_H))

    def test_full_legacy_town_migrates_without_losing_profile_or_history(self):
        # Build the pre-map schema independently of the current implementation.
        # SQLite's transaction context does not close the file handle on exit.
        with closing(sqlite3.connect(str(self.data / "town.sqlite3"))) as db, db:
            db.executescript("""
                CREATE TABLE agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                    personality TEXT NOT NULL, bio TEXT NOT NULL, color TEXT NOT NULL,
                    avatar TEXT NOT NULL, home TEXT NOT NULL, greeting TEXT NOT NULL,
                    suggestions TEXT NOT NULL, memories TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            for index in range(28):
                db.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    "legacy-%s" % index, "伙伴%s" % index, "园艺师", "喜欢种植",
                    "这是升级前保存的简介", "#7fa987", "resident", "伙伴小屋",
                    "你好", '["聊聊花园"]', '["喜欢蓝花"]', "2026-11-09T00:00:00+00:00"))
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (
                "old-message", "legacy-27", "user", "请记住这次约定", "sent", "2026-11-09"))
            db.execute("INSERT INTO preferences VALUES ('selected_agent_id','legacy-27')")
            db.execute("INSERT INTO preferences VALUES ('custom-setting','unchanged')")
            before_profiles = db.execute("SELECT id,name,role,personality,bio,color,avatar,greeting,suggestions,memories,created_at FROM agents ORDER BY id").fetchall()
            before_messages = db.execute("SELECT * FROM messages").fetchall()
            before_preferences = db.execute("SELECT * FROM preferences ORDER BY key").fetchall()

        self.start()
        agents = self.agents()
        self.assertEqual(len(agents), 30)
        slots = [agents["legacy-%s" % i]["plot"]["slot"] for i in range(28)]
        self.assertEqual(len(set(slots)), 28)
        for index in range(28):
            agent = agents["legacy-%s" % index]
            self.assertEqual(agent["house"]["style"], "farm")
            self.assertTrue(agent["home"].startswith(agent["name"]))
        with self.app.db() as db:
            after_profiles = [tuple(row) for row in db.execute("SELECT id,name,role,personality,bio,color,avatar,greeting,suggestions,memories,created_at FROM agents WHERE id LIKE 'legacy-%' ORDER BY id")]
            self.assertEqual(after_profiles, before_profiles)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM messages")], before_messages)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM preferences ORDER BY key")], before_preferences)
            first_migration = [tuple(row) for row in db.execute("SELECT * FROM agents ORDER BY rowid")]
        self.start()
        with self.app.db() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM agents ORDER BY rowid")], first_migration)

    def test_migration_repairs_invalid_and_duplicate_slots_preserving_valid_owners(self):
        self.start()
        residents = [self.app.add_agent({"name": "伙伴%s" % i})["agent"] for i in range(9)]
        damaged = [{}, {"slot": 0}, {"slot": 0}, {"slot": True}, {"slot": [2]},
                   {"slot": -1}, {"slot": 999}, {"slot": 1.0}, {"slot": "2"}]
        with self.app.db() as db:
            for agent, plot in zip(residents, damaged):
                plot["pos"] = [-1000, 9000]
                db.execute("UPDATE agents SET plot=?,house=? WHERE id=?",
                           (json.dumps(plot), '{"style":"invalid","painted":true}', agent["id"]))
            # A malformed home on an original resident must never reserve a lot.
            db.execute("UPDATE agents SET plot=?,house=? WHERE id='linghang'", ('{"slot":2}', '[]'))
        self.start()
        repaired = self.agents()
        self.assertEqual(repaired[residents[1]["id"]]["plot"]["slot"], 0)
        lots = [repaired[agent["id"]]["plot"] for agent in residents]
        self.assertEqual(len({plot["slot"] for plot in lots}), len(lots))
        for agent in residents:
            actual = repaired[agent["id"]]
            self.assertIs(type(actual["plot"]["slot"]), int)
            self.assertGreaterEqual(min(actual["plot"]["pos"]), 0)
            self.assertEqual(actual["house"]["style"], "cottage")
            self.assertFalse(actual["house"]["painted"])
        self.assertIsNone(repaired["linghang"]["plot"]["slot"])
        self.assertEqual(repaired["linghang"]["house"], painted_house("linghang"))

    def test_invalid_json_is_repaired_and_restart_does_not_move_residents(self):
        self.start()
        resident = self.app.add_agent({"name": "修复者"})["agent"]
        with self.app.db() as db:
            db.execute("UPDATE agents SET plot='not-json',house='null' WHERE id=?", (resident["id"],))
        self.start()
        repaired = self.agents()[resident["id"]]
        self.assertEqual(repaired["house"]["style"], "cottage")
        self.start()
        reopened = self.agents()[resident["id"]]
        self.assertEqual(reopened["plot"], repaired["plot"])
        self.assertEqual(reopened["house"], repaired["house"])

    def test_profile_edits_refresh_house_without_moving_or_changing_role(self):
        self.start()
        resident = self.app.add_agent({"name": "阿禾", "personality": "喜欢种植、园艺", "color": "#Ab12Cd"})["agent"]
        self.assertEqual(resident["house"]["style"], "farm")
        updated = self.app.patch_agent(resident["id"], {
            "name": "小调", "personality": "热爱音乐、乐器和节奏", "bio": "希望分享旋律",
            "memories": ["保留我的约定"],
        })["agent"]
        self.assertEqual(updated["house"]["style"], "music")
        self.assertEqual(updated["home"], "小调的旋律小屋")
        self.assertEqual(updated["plot"], resident["plot"])
        self.assertEqual(updated["role"], resident["role"])
        self.assertEqual(updated["house"]["variant"], resident["house"]["variant"])
        self.assertEqual(updated["house"]["palette"]["accent"], "#ab12cd")
        self.start()
        reopened = self.agents()[resident["id"]]
        self.assertEqual(reopened["house"], updated["house"])
        self.assertEqual(reopened["memories"], ["保留我的约定"])

    def test_original_buildings_remain_in_village_after_profile_edits(self):
        self.start()
        before = self.agents()
        for identity in ("linghang", "scout"):
            updated = self.app.patch_agent(identity, {
                "name": "新名字", "personality": "音乐、乐器、节奏", "bio": "开一座农场",
            })["agent"]
            self.assertEqual(updated["plot"], before[identity]["plot"])
            self.assertEqual(updated["house"], before[identity]["house"])
            self.assertEqual(updated["home"], before[identity]["home"])
        self.start()
        for identity in ("linghang", "scout"):
            self.assertEqual(self.agents()[identity]["house"], before[identity]["house"])

    def test_failed_migration_rolls_back_all_resident_changes_and_releases_lock(self):
        self.start()
        self.app.add_agent({"name": "先迁移的邻居"})
        self.app.add_agent({"name": "后迁移的邻居"})
        with self.app.db() as db:
            db.execute("UPDATE agents SET plot='{}',house='{}'")
            original = [tuple(row) for row in db.execute("SELECT * FROM agents ORDER BY rowid")]
        self.app.close()
        self.app = None
        calls = []

        def fail_second_house(*args):
            calls.append(args[0])
            if len(calls) == 2:
                raise RuntimeError("模拟升级中断")
            return design_house(*args)

        with mock.patch("server.design_house", side_effect=fail_second_house):
            with self.assertRaisesRegex(RuntimeError, "模拟升级中断"):
                self.start()
        with closing(sqlite3.connect(str(self.data / "town.sqlite3"))) as db:
            self.assertEqual(db.execute("SELECT * FROM agents ORDER BY rowid").fetchall(), original)
        self.start()
        self.assertEqual(len(self.agents()), 4)

    def test_concurrent_arrivals_do_not_share_plots_or_exceed_capacity(self):
        self.start()

        def arrival(index):
            try:
                return self.app.add_agent({"name": "邻居%s" % index})["agent"]
            except APIError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(arrival, range(36)))
        accepted = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(accepted), 28)
        self.assertEqual(len({agent["plot"]["slot"] for agent in accepted}), 28)
        self.assertEqual([result for result in results if isinstance(result, int)], [400] * 8)
        self.assertEqual(len(self.agents()), 30)


if __name__ == "__main__":
    unittest.main()
