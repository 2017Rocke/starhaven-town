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
    PLOT_H, PLOT_SLOTS, PLOT_W, SUPPORTED_AVATARS, TownApp, WORLD_H, WORLD_W, WORLD_X, WORLD_Y,
    create_server, design_house, painted_house,
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
                self.assertGreaterEqual(x, WORLD_X)
                self.assertGreaterEqual(y, WORLD_Y)
                self.assertLessEqual(x + PLOT_W, WORLD_X + WORLD_W)
                self.assertLessEqual(y + PLOT_H, WORLD_Y + WORLD_H)
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
        valid_slot = residents[1]["plot"]["slot"]
        damaged = [{}, {"slot": valid_slot}, {"slot": valid_slot}, {"slot": True}, {"slot": [2]},
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
        self.assertEqual(repaired[residents[1]["id"]]["plot"]["slot"], valid_slot)
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

    def test_profession_and_interests_produce_distinct_combined_homes(self):
        examples = [
            ("开发工程师", "解释逻辑，迭代开发和测试，代码简洁，工具分类整齐", "喜欢摇滚节奏",
             "workshop", "music", "节拍工坊"),
            ("架构工程师", "重构系统，研究大脑构造和股票短线", "研究底层逻辑与软件架构",
             "architecture", "finance", "蓝图研舍"),
            ("养鸡、教学、突发处理", "温柔、直接、有耐心", "了解鸡的各阶段需求与健康",
             "farm", "poultry", "暖羽鸡舍"),
        ]
        for role, personality, bio, style, motif, label in examples:
            with self.subTest(role=role):
                house = design_house("stable-id", "测试伙伴", role, personality, bio, "#abc123")
                self.assertEqual((house["style"], house["motif"], house["label"]), (style, motif, label))
                self.assertEqual(house["name"], "测试伙伴的" + label)
                self.assertTrue(house["feature"])
                renamed = design_house("stable-id", "另一位伙伴", role, personality, bio, "#abc123")
                self.assertEqual({key: value for key, value in house.items() if key != "name"},
                                 {key: value for key, value in renamed.items() if key != "name"})

    def test_profession_remains_primary_and_all_ten_styles_are_available(self):
        roles = {"主管": "command", "软件架构工程师": "architecture", "开发工程师": "workshop",
                 "园艺师": "farm", "音乐人": "music", "股票分析师": "finance",
                 "教学老师": "school", "开源研究员": "observatory", "钓鱼佬": "angling"}
        for role, style in roles.items():
            with self.subTest(role=role):
                house = design_house("resident", "伙伴", role,
                                     "股票金融投资交易理财基金行情证券，喜欢音乐摇滚乐队乐器节奏",
                                     "爱研究底层系统、代码、农场与教学", "#abcdef")
                self.assertEqual(house["style"], style)
        home = design_house("resident", "伙伴", "小镇伙伴", "友善", "喜欢散步", "#abcdef")
        self.assertEqual((home["style"], home["motif"]), ("cottage", "none"))

    def test_house_motif_updates_on_edit_and_migration_without_moving(self):
        self.start()
        resident = self.app.add_agent({"name": "编曲伙伴", "role": "开发工程师", "bio": "喜欢摇滚"})["agent"]
        self.assertEqual(resident["house"]["label"], "节拍工坊")
        updated = self.app.patch_agent(resident["id"], {"bio": "喜欢股票行情与短线研究"})["agent"]
        self.assertEqual((updated["house"]["style"], updated["house"]["motif"]), ("workshop", "finance"))
        self.assertEqual(updated["plot"], resident["plot"])
        with self.app.db() as db:
            # Simulate a pre-personalization home saved by an older app version.
            db.execute("UPDATE agents SET house=?,home=? WHERE id=?",
                       ('{"style":"music","name":"旧屋"}', "旧屋", resident["id"]))
        self.start()
        migrated = self.agents()[resident["id"]]
        self.assertEqual(migrated["house"], updated["house"])
        self.assertEqual(migrated["plot"], resident["plot"])

    def test_avatar_edit_is_persistent_without_changing_permissions_or_records(self):
        self.start()
        resident = self.app.add_agent({"name": "换装伙伴", "role": "开发工程师", "avatar": "maker", "color": "#123abc"})["agent"]
        self.app.patch_agent(resident["id"], {"memories": ["保留这条约定"]})
        with self.app.db() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (
                "avatar-history", resident["id"], "user", "早先的对话", "sent", "2026-11-09"))
        before_messages = self.app.messages(resident["id"])
        for avatar in sorted(SUPPORTED_AVATARS):
            with self.subTest(avatar=avatar):
                updated = self.app.patch_agent(resident["id"], {"avatar": avatar})["agent"]
                self.assertEqual(updated["avatar"], avatar)
                self.assertEqual(updated["color"], resident["color"])
                self.assertEqual(updated["role"], resident["role"])
                self.assertEqual(updated["plot"], resident["plot"])
                self.assertEqual(updated["house"], resident["house"])
                self.assertEqual(updated["memories"], ["保留这条约定"])
                self.assertEqual(self.app.messages(resident["id"]), before_messages)
        self.app.patch_agent(resident["id"], {"avatar": "engineer"})
        self.start()
        self.assertEqual(self.agents()[resident["id"]]["avatar"], "engineer")
        self.assertEqual(self.app.messages(resident["id"]), before_messages)
        with self.assertRaises(APIError):
            self.app.patch_agent(resident["id"], {"avatar": "captain", "role": "主管"})
        self.assertEqual(self.agents()[resident["id"]]["avatar"], "engineer")

    def test_new_avatars_are_available_on_creation_and_invalid_edits_are_atomic(self):
        self.start()
        for avatar in ("engineer", "musician", "architect", "rancher", "scholar", "artist"):
            self.assertEqual(self.app.add_agent({"name": avatar, "avatar": avatar})["agent"]["avatar"], avatar)
        resident = self.app.add_agent({"name": "原名字"})["agent"]
        count = len(self.agents())
        for invalid in ("unknown", "../captain", "Captain", "", None, 42, ["engineer"]):
            with self.subTest(avatar=invalid):
                with self.assertRaises(APIError) as error:
                    self.app.patch_agent(resident["id"], {"name": "不应保存", "avatar": invalid})
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(self.agents()[resident["id"]]["name"], "原名字")
                with self.assertRaises(APIError):
                    self.app.add_agent({"name": "不应创建", "avatar": invalid})
                self.assertEqual(len(self.agents()), count)

    def test_unknown_legacy_avatar_falls_back_without_overwriting_saved_value(self):
        self.start()
        resident = self.app.add_agent({"name": "老伙伴"})["agent"]
        with self.app.db() as db:
            db.execute("UPDATE agents SET avatar='legacy-custom-look' WHERE id=?", (resident["id"],))
        self.start()
        self.assertEqual(self.agents()[resident["id"]]["avatar"], "resident")
        self.app.patch_agent(resident["id"], {"name": "老朋友", "memories": ["过去的故事"]})
        with self.app.db() as db:
            stored = db.execute("SELECT avatar,name,memories FROM agents WHERE id=?", (resident["id"],)).fetchone()
            self.assertEqual(tuple(stored), ("legacy-custom-look", "老朋友", '["过去的故事"]'))
        self.assertEqual(self.app.patch_agent(resident["id"], {"avatar": "artist"})["agent"]["avatar"], "artist")
        self.start()
        self.assertEqual(self.agents()[resident["id"]]["avatar"], "artist")
        self.assertEqual(self.agents()[resident["id"]]["memories"], ["过去的故事"])

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
        self.assertEqual(len(self.agents()), len(original))

    def test_concurrent_arrivals_do_not_share_plots_or_exceed_capacity(self):
        self.start()
        remaining = MAX_RESIDENTS - len(self.agents())

        def arrival(index):
            try:
                return self.app.add_agent({"name": "邻居%s" % index})["agent"]
            except APIError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(arrival, range(36)))
        accepted = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(accepted), remaining)
        self.assertEqual(len({agent["plot"]["slot"] for agent in accepted}), remaining)
        self.assertEqual([result for result in results if isinstance(result, int)], [400] * (36 - remaining))
        self.assertEqual(len(self.agents()), 30)


if __name__ == "__main__":
    unittest.main()
