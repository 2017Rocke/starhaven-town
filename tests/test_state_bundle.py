"""Portable state bundles: what travels, what must never travel."""
from contextlib import redirect_stdout
import hashlib
import io
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

    def test_export_excludes_local_budget_verification_records_and_temporary_files(self):
        private = json.dumps({"endpoint": "https://synthetic-private-gateway.example",
                              "verified": True, "marker": "SYNTHETIC_LOCAL_BUDGET_RECORD"}).encode()
        names = ("model-budget-verifications.json", ".model-budget-verifications-synthetic.tmp",
                 "MODEL-BUDGET-VERIFICATIONS.JSON")
        for name in names:
            (self.data / name).write_bytes(private)
        output = io.StringIO()
        with redirect_stdout(output):
            bundle = self.export("without-local-verifications.zip")
        with zipfile.ZipFile(bundle) as archive:
            payloads = b"".join(archive.read(name) for name in archive.namelist())
            for name in names:
                self.assertNotIn("data/" + name, archive.namelist())
            self.assertNotIn(b"synthetic-private-gateway", payloads)
            self.assertNotIn(b"SYNTHETIC_LOCAL_BUDGET_RECORD", payloads)
        manifest = self.manifest(bundle)
        self.assertIn("model-budget-verifications.json", manifest["excluded"])
        self.assertIn("本机重新验证", output.getvalue())
        self.assertTrue((self.data / "model-budget-verifications.json").is_file())

    def test_protocol_and_authentication_round_trip_without_credentials(self):
        for protocol, auth_type in (("openai", "bearer"), ("anthropic", "api_key"), ("anthropic", "bearer")):
            with self.subTest(protocol=protocol, auth_type=auth_type):
                settings = dict(mode="api", base_url="https://models.example/v1", model="supplied-model",
                                protocol=protocol, auth_type=auth_type)
                secret = "synthetic-api-credential-never-export"
                (self.data / "provider.json").write_text(json.dumps(dict(settings, api_key=secret)), encoding="utf-8")
                bundle = self.export(protocol + "-" + auth_type + ".zip")
                with zipfile.ZipFile(bundle) as archive:
                    self.assertEqual(json.loads(archive.read("data/provider.json")), settings)
                    self.assertNotIn(secret.encode(), b"".join(archive.read(name) for name in archive.namelist()))
                target = self.root / (protocol + "-" + auth_type)
                self.assertEqual(state_bundle.main(["restore", str(bundle), "--data", str(target)]), 0)
                self.assertEqual(json.loads((target / "provider.json").read_text(encoding="utf-8")), settings)

    def test_legacy_provider_stays_compatible_without_added_selectors(self):
        bundle = self.export("legacy.zip")
        target = self.root / "legacy"
        self.assertEqual(state_bundle.main(["restore", str(bundle), "--data", str(target)]), 0)
        settings = json.loads((target / "provider.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, dict(mode="api", base_url="https://api.deepseek.com", model="deepseek-flash"))

    def test_selector_fields_only_accept_supported_values(self):
        for value in ("secret-string", ["anthropic"], {"api_key": "synthetic-key"}, None):
            with self.subTest(value=value):
                settings = dict(mode="api", base_url="https://models.example/v1", model="model",
                                protocol=value, auth_type=value, api_key="synthetic-key")
                kept, notes = state_bundle.provider_fields(settings)
                self.assertEqual(kept, {})
                self.assertTrue(any("重新配置" in note for note in notes))
                self.assertNotIn("synthetic-key", " ".join(notes))
        self.assertEqual(state_bundle.provider_fields([])[0], {})

    def test_invalid_selectors_cannot_inherit_deepseek_budget_profile(self):
        from provider_bridge import ProviderBridge
        for index, selectors in enumerate((dict(protocol="future-protocol"), dict(auth_type="future-auth"),
                                            dict(protocol="openai", auth_type="api_key"), dict(auth_type="api_key"))):
            with self.subTest(selectors=selectors):
                settings = dict(mode="api", base_url="https://api.deepseek.com", model="deepseek-flash", **selectors)
                (self.data / "provider.json").write_text(json.dumps(settings), encoding="utf-8")
                bundle = self.export("invalid-%s.zip" % index)
                with zipfile.ZipFile(bundle) as archive:
                    self.assertEqual(json.loads(archive.read("data/provider.json")), {})
                target = self.root / ("invalid-restored-%s" % index)
                self.assertEqual(state_bundle.main(["restore", str(bundle), "--data", str(target)]), 0)
                restored = json.loads((target / "provider.json").read_text(encoding="utf-8"))
                self.assertEqual(restored, {})
                self.assertFalse(ProviderBridge.budget_capability(restored)["enforce_supported"])
                self.assertTrue((target / "town.sqlite3").is_file())

    def test_restore_reapplies_provider_whitelist_to_external_bundle(self):
        original = self.export("original.zip")
        external = self.root / "external.zip"
        provider = dict(mode="api", base_url="https://models.example/v1", model="supplied-model",
                        protocol="anthropic", auth_type="api_key", api_key="synthetic-import-secret")
        payload = json.dumps(provider).encode()
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(external, "w") as sink:
            manifest = json.loads(source.read("manifest.json"))
            manifest["files"]["data/provider.json"] = dict(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
            for name in source.namelist():
                if name == "manifest.json":
                    sink.writestr(name, json.dumps(manifest))
                else:
                    sink.writestr(name, payload if name == "data/provider.json" else source.read(name))
        target = self.root / "external-restored"
        self.assertEqual(state_bundle.main(["restore", str(external), "--data", str(target)]), 0)
        restored = json.loads((target / "provider.json").read_text(encoding="utf-8"))
        self.assertEqual(restored, {key: value for key, value in provider.items() if key != "api_key"})
        self.assertNotIn("synthetic-import-secret", (target / "provider.json").read_text(encoding="utf-8"))

    def test_restore_skips_injected_local_budget_verifications_and_preserves_local_record(self):
        original = self.export("original-without-verifications.zip")
        external = self.root / "external-with-verifications.zip"
        injected = json.dumps({"endpoint": "https://synthetic-private-gateway.example",
                               "verified": True, "marker": "SYNTHETIC_IMPORTED_QUALIFICATION"}).encode()
        injected_names = ("data/model-budget-verifications.json",
                          "data/./MODEL-BUDGET-VERIFICATIONS.JSON",
                          "data/nested/../model-budget-verifications.json",
                          "data\\model-budget-verifications.json",
                          "data/model-budget-verifications.json. ",
                          "data/.model-budget-verifications-imported.tmp")
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(external, "w") as sink:
            manifest = json.loads(source.read("manifest.json"))
            for name in injected_names:
                manifest["files"][name] = dict(sha256=hashlib.sha256(injected).hexdigest(), bytes=len(injected))
            manifest["file_count"] = len(manifest["files"])
            manifest["total_bytes"] += len(injected) * len(injected_names)
            for name in source.namelist():
                sink.writestr(name, json.dumps(manifest) if name == "manifest.json" else source.read(name))
            for name in injected_names:
                sink.writestr(name, injected)
        for existing in (False, True):
            with self.subTest(existing_local_record=existing):
                target = self.root / ("restored-local-%s" % existing)
                target.mkdir()
                local = target / "model-budget-verifications.json"
                if existing:
                    local.write_bytes(b"SYNTHETIC_EXISTING_LOCAL_RECORD")
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(state_bundle.main(["restore", str(external), "--data", str(target)]), 0)
                self.assertIn("已跳过", output.getvalue())
                self.assertIn("本机重新验证", output.getvalue())
                self.assertNotIn("synthetic-private-gateway", output.getvalue())
                expected = {"town.sqlite3", "provider.json", "workspaces"}
                if existing:
                    expected.add("model-budget-verifications.json")
                    self.assertEqual(local.read_bytes(), b"SYNTHETIC_EXISTING_LOCAL_RECORD")
                else:
                    self.assertFalse(local.exists())
                self.assertEqual({path.name for path in target.iterdir()}, expected)
                self.assertTrue((target / "workspaces" / "scout" / "results" / "abc.md").is_file())
                self.assertEqual(json.loads((target / "provider.json").read_text())["model"], "deepseek-flash")

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
