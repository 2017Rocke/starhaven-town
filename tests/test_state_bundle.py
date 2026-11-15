"""Portable state bundles: what travels, what must never travel."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import state_bundle  # noqa: E402


def make_town(root):
    """A minimal but realistic data directory: database, provider, one artefact."""
    data = root / "data"
    (data / "workspaces" / "scout" / "results").mkdir(parents=True)
    (data / "workspaces" / "scout" / "results" / "abc.md").write_text("# 成果\n\n星探的方法说明。", encoding="utf-8")
    (data / "logs").mkdir()
    (data / "logs" / "desktop.log").write_text("noise", encoding="utf-8")
    (data / "WebView2").mkdir()
    (data / "WebView2" / "cache.bin").write_bytes(b"x" * 1024)
    (data / "town.lock").write_text("0", encoding="utf-8")
    (data / "provider.json").write_text(json.dumps({
        "mode": "api", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
        "api_key": "sk-" + "a" * 32,
    }), encoding="utf-8")
    connection = sqlite3.connect(str(data / "town.sqlite3"))
    try:
        connection.executescript(
            "CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT);"
            "CREATE TABLE messages (id TEXT PRIMARY KEY, agent_id TEXT, content TEXT);"
            "CREATE TABLE v2_tasks (id TEXT PRIMARY KEY);"
            "CREATE TABLE v2_artifacts (id TEXT PRIMARY KEY);"
            "CREATE TABLE v2_skills (id TEXT PRIMARY KEY);"
            "CREATE TABLE v2_calls (id TEXT PRIMARY KEY);"
            "CREATE TABLE v2_message_sources (id TEXT PRIMARY KEY);")
        connection.executemany("INSERT INTO agents VALUES (?,?)",
                               [("scout", "星探"), ("resident-jiji", "吉吉")])
        connection.executemany("INSERT INTO messages VALUES (?,?,?)",
                               [("m1", "scout", "我今天做了什么"), ("m2", "scout", "再看一次")])
        connection.execute("INSERT INTO v2_artifacts VALUES ('art-1')")
        connection.execute("INSERT INTO v2_message_sources VALUES ('src-1')")
        connection.commit()
    finally:
        connection.close()
    return data


class StateBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = make_town(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def export(self, name, **kwargs):
        output = self.root / name
        arguments = dict(data=str(self.data), out=str(output), include_materials=False,
                         allow_running=False, strip_chat=False)
        arguments.update(kwargs)
        self.assertEqual(state_bundle.main(["export"] + [
            "--data", arguments["data"], "--out", arguments["out"]]
            + (["--strip-chat"] if arguments["strip_chat"] else [])
            + (["--include-materials"] if arguments["include_materials"] else [])), 0)
        return output

    def manifest(self, bundle):
        with zipfile.ZipFile(bundle) as archive:
            return json.loads(archive.read("manifest.json"))

    def names(self, bundle):
        with zipfile.ZipFile(bundle) as archive:
            return archive.namelist()

    def test_bundle_never_carries_a_credential_or_transient_files(self):
        bundle = self.export("state.zip")
        manifest = self.manifest(bundle)
        with zipfile.ZipFile(bundle) as archive:
            provider = json.loads(archive.read("data/provider.json"))
            payloads = {name: archive.read(name) for name in archive.namelist()}
        self.assertEqual(sorted(provider), ["base_url", "mode", "model"])
        self.assertNotIn(b"sk-" + b"a" * 32, b"".join(payloads.values()))
        names = self.names(bundle)
        for excluded in ("data/town.lock", "data/logs/", "data/WebView2/", "data/settings.json"):
            self.assertFalse([name for name in names if name.startswith(excluded)], excluded)
        self.assertFalse([name for name in names if name.endswith(("-wal", "-shm", "-journal"))])
        self.assertTrue(manifest["credentials"].startswith("不含任何模型密钥"))
        self.assertEqual(manifest["database"]["messages"], 2)

    def test_strip_chat_leaves_no_conversation_rows_behind(self):
        bundle = self.export("public.zip", strip_chat=True)
        self.assertTrue(self.manifest(bundle)["chat_stripped"])
        with zipfile.ZipFile(bundle) as archive:
            self.assertFalse([name for name in archive.namelist() if name.endswith(("-wal", "-shm"))])
            staged = self.root / "staged.sqlite3"
            staged.write_bytes(archive.read("data/town.sqlite3"))
        connection = sqlite3.connect(str(staged))
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM v2_message_sources").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM agents").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM v2_artifacts").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_restore_rebuilds_the_data_directory_and_rejects_a_modified_payload(self):
        bundle = self.export("public.zip", strip_chat=True)
        target = self.root / "restored"
        self.assertEqual(state_bundle.main(["restore", str(bundle), "--data", str(target)]), 0)
        self.assertTrue((target / "town.sqlite3").is_file())
        self.assertTrue((target / "workspaces" / "scout" / "results" / "abc.md").is_file())
        self.assertEqual(json.loads((target / "provider.json").read_text(encoding="utf-8"))["model"], "deepseek-flash")

        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as sink:
            for name in source.namelist():
                payload = source.read(name)
                if name == "data/workspaces/scout/results/abc.md":
                    payload = b"# changed"
                sink.writestr(name, payload)
        with self.assertRaises(SystemExit):
            state_bundle.main(["restore", str(tampered), "--data", str(self.root / "second")])

    def test_restore_refuses_to_overwrite_a_used_directory_without_force(self):
        bundle = self.export("state.zip")
        with self.assertRaises(SystemExit):
            state_bundle.main(["restore", str(bundle), "--data", str(self.data)])
        target = self.root / "fresh"
        self.assertEqual(state_bundle.main(["restore", str(bundle), "--data", str(target), "--force"]), 0)

    def test_inspect_reports_counts_without_extracting(self):
        bundle = self.export("state.zip")
        self.assertEqual(state_bundle.main(["inspect", str(bundle)]), 0)
        self.assertFalse((self.root / "data" / "restored").exists())


if __name__ == "__main__":
    unittest.main()
