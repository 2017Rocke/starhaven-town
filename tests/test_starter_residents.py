"""Public starter profiles must never replace a resident's existing save."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import STARTER_RESIDENTS_PATH, TownApp


class OfflineBridge:
    def probe(self):
        return dict(available=False, authenticated=False, status="test")


class StarterResidentsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = None

    def tearDown(self):
        if self.app:
            self.app.close()
        self.temp.cleanup()

    def start(self):
        if self.app:
            self.app.close()
            self.app = None
        self.app = TownApp(self.root / "data", OfflineBridge(), self.root / "team", report_root=self.root / "reports")
        return self.app.state()

    def test_fresh_town_has_eight_public_profiles_without_private_history(self):
        state = self.start()
        self.assertEqual([a["name"] for a in state["agents"]], ["领航", "星探", "吉吉", "东日", "科德", "爬山佬", "巡检警官", "网安稽查官"])
        self.assertEqual([a["plot"]["slot"] for a in state["agents"][2:]], [0, 1, 2, 3, 4, 5])
        self.assertEqual([a["home"] for a in state["agents"]][2:5], ["吉吉的节拍工坊", "东日的蓝图研舍", "科德的暖羽鸡舍"])
        for agent in state["agents"]:
            self.assertEqual(agent["memories"], [])
            self.assertEqual(self.app.messages(agent["id"])["messages"], [])
        self.assertEqual(state["map"]["origin"], [0, -520])
        self.assertEqual(state["map"]["world"], [2880, 2200])
        self.assertEqual(state["map"]["core"], [960, 330, 960, 660])

    def test_restart_preserves_custom_edits_and_does_not_reseed_removed_profiles(self):
        self.start()
        updated = self.app.patch_agent("resident-jiji", {"name": "自己的伙伴", "memories": ["自己的记忆"], "avatar": "engineer"})["agent"]
        with self.app.db() as db:
            db.execute("DELETE FROM agents WHERE id='resident-kede'")
        state = self.start()
        self.assertEqual(len(state["agents"]), 7)
        actual = next(a for a in state["agents"] if a["id"] == "resident-jiji")
        for field in ("name", "avatar", "memories", "plot", "house"):
            self.assertEqual(actual[field], updated[field])

    def test_existing_town_is_not_populated_even_when_starter_file_is_missing(self):
        self.start()
        with self.app.db() as db:
            db.execute("DELETE FROM agents WHERE id LIKE 'resident-%'")
        own = self.app.add_agent({"name": "已有居民", "role": "自己的职业"})["agent"]
        self.app.patch_agent(own["id"], {"memories": ["保留存档"]})
        with patch("server.STARTER_RESIDENTS_PATH", self.root / "not-present.json"):
            state = self.start()
        self.assertEqual([a["name"] for a in state["agents"]], ["领航", "星探", "已有居民"])
        resident = state["agents"][-1]
        self.assertEqual(resident["id"], own["id"])
        self.assertEqual(resident["plot"], own["plot"])
        self.assertEqual(resident["memories"], ["保留存档"])

    def test_invalid_private_fields_roll_back_all_seeded_residents(self):
        payload = json.loads(STARTER_RESIDENTS_PATH.read_text())
        payload["residents"][1]["memories"] = ["must never be bundled"]
        fixture = self.root / "invalid.json"
        fixture.write_text(json.dumps(payload))
        with patch("server.STARTER_RESIDENTS_PATH", fixture):
            with self.assertRaisesRegex(RuntimeError, "初始居民配置无效"):
                self.start()
        con = sqlite3.connect(str(self.root / "data/town.sqlite3"))
        try:
            self.assertEqual(con.execute("SELECT count(*) FROM agents").fetchone()[0], 0)
        finally:
            con.close()
        self.assertEqual(len(self.start()["agents"]), 8)


if __name__ == "__main__":
    unittest.main()
