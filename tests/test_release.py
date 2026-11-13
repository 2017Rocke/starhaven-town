"""Release integrity and retry behavior, using temporary files and a fake API."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import check_release as release
from scripts import export_source


VERSION = "1.13.0"
IDENTITY = {"tag": "v" + VERSION, "version": VERSION, "commit": "a" * 40}
NOTES = "# 星栖小镇 1.13.0\n\n已验证的发布说明。\n"
SOURCE = "starhaven-town-1.13.0-source.zip"
PLATFORM_PAYLOADS = {
    "macos-arm64": ["StarhavenTown-1.13.0-macOS-arm64.dmg"],
    "macos-x86_64": ["StarhavenTown-1.13.0-macOS-x86_64.dmg"],
    "windows-x64": ["StarhavenTown-1.13.0-windows-x64-Setup.exe",
                    "StarhavenTown-1.13.0-windows-x64.zip"],
}
PAYLOADS = sum(PLATFORM_PAYLOADS.values(), []) + [SOURCE]
ASSETS = sorted(name for payload in PAYLOADS for name in (payload, payload + ".sha256"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checksum(path):
    path.with_name(path.name + ".sha256").write_bytes(
        (digest(path.read_bytes()) + "  " + path.name + "\n").encode("utf-8"))


def source_archive(path, version=VERSION, extra=b"same commit\n"):
    entries = {
        "VERSION": version + "\n",
        "AGENTS.md": "Public project instructions.\n",
        ".agents/skills/starhaven-release/SKILL.md": "Public release skill.\n",
        ".agents/skills/starhaven-release/agents/openai.yaml": "interface: {}\n",
        "docs/UPDATE_1.13.0.md": NOTES,
        "README.md": extra,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as package:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo("starhaven-town/" + name, (1980, 1, 1, 0, 0, 0))
            package.writestr(info, content)
    checksum(path)


def assets(directory, platform=None):
    directory.mkdir(parents=True, exist_ok=True)
    selected = PLATFORM_PAYLOADS[platform] + [SOURCE] if platform else PAYLOADS
    for name in selected:
        path = directory / name
        if name == SOURCE:
            source_archive(path)
        else:
            path.write_bytes(("Synthetic build: " + name + "\n").encode("utf-8"))
            checksum(path)
    return directory


class FakeGitHub:
    """Only the API calls used by publish; unexpected operations fail closed."""
    repository = "example/starhaven-fixture"

    def __init__(self, directory, draft=None, existing=()):
        self.directory = directory
        self.calls = []
        self.downloads = []
        self.stored = {name: (directory / name).read_bytes() for name in existing}
        self.target = {"type": "commit", "sha": IDENTITY["commit"]}
        self.runs = [{"id": 101, "head_sha": IDENTITY["commit"], "conclusion": "success"}]
        self.release = self.record(draft) if draft is not None else None
        self.upload_limit = None
        self.uploaded = 0
        self.download_error = None
        self.corrupt_download = False
        self.created_tag_override = None
        self.move_tag_after_upload = False
        self.move_tag_after_download = False

    @staticmethod
    def record(draft):
        return {"id": 42, "tag_name": IDENTITY["tag"], "target_commitish": IDENTITY["commit"],
                "body": NOTES, "draft": draft, "html_url": "https://example.invalid/releases/v1.13.0"}

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def api(self, method, endpoint, data=None, upload=None):
        self.calls.append((method, endpoint, copy.deepcopy(data), upload.name if upload else None))
        base = "repos/" + self.repository
        if method == "GET" and endpoint == base + "/git/ref/tags/" + IDENTITY["tag"]:
            return {"object": copy.deepcopy(self.target)}
        if method == "GET" and endpoint.startswith(base + "/git/tags/"):
            return {"object": {"type": "commit", "sha": IDENTITY["commit"]}}
        if method == "GET" and endpoint.startswith(base + "/actions/workflows/ci.yml/runs?"):
            return {"workflow_runs": copy.deepcopy(self.runs)}
        if method == "GET" and endpoint.startswith(base + "/releases?per_page=100&page="):
            return [copy.deepcopy(self.release)] if self.release else []
        if method == "POST" and endpoint == base + "/releases":
            if self.release is not None:
                raise AssertionError("A retry tried to create a second release")
            self.release = dict(self.record(True), **data)
            if self.created_tag_override:
                self.release["tag_name"] = self.created_tag_override
            return copy.deepcopy(self.release)
        if method == "GET" and endpoint == base + "/releases/42/assets?per_page=100":
            return [{"name": name, "state": "uploaded", "digest": "sha256:" + digest(content),
                     "size": len(content)} for name, content in self.stored.items()]
        if method == "POST" and endpoint.startswith("https://uploads.github.com/" + base + "/releases/42/assets?"):
            name = parse_qs(urlsplit(endpoint).query)["name"][0]
            if name in self.stored:
                raise AssertionError("A retry tried to replace an existing asset: " + name)
            if self.upload_limit is not None and self.uploaded >= self.upload_limit:
                raise RuntimeError("Simulated interrupted upload")
            self.stored[name] = upload.read_bytes()
            self.uploaded += 1
            if self.move_tag_after_upload:
                self.target = {"type": "commit", "sha": "b" * 40}
            return {"name": name}
        if method == "PATCH" and endpoint == base + "/releases/42":
            if data != {"draft": False}:
                raise AssertionError("Unexpected release rewrite")
            self.release["draft"] = False
            return copy.deepcopy(self.release)
        raise AssertionError("Unexpected API operation: " + method + " " + endpoint)

    def download(self, tag, directory):
        self.downloads.append(tag)
        if self.download_error:
            raise self.download_error
        for name, content in self.stored.items():
            (directory / name).write_bytes(content)
        if self.corrupt_download:
            (directory / PAYLOADS[0]).write_bytes(b"corrupted download")
        if self.move_tag_after_download:
            self.target = {"type": "commit", "sha": "b" * 40}


class ReleasePreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        (self.root / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
        (self.root / "docs" / ("UPDATE_" + VERSION + ".md")).write_text(NOTES, encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("# Changes\n\n## 1.13.0\n\nReady.\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("build/\ndist/\n", encoding="utf-8")
        (self.root / "server.py").write_text("VERSION = 'fixture'\n", encoding="utf-8")
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic release fixture")
        self.git("tag", IDENTITY["tag"])

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", "-c", "user.name=Release Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *arguments],
            cwd=self.root, text=True, stderr=subprocess.PIPE).strip()

    def test_existing_version_tag_identifies_the_actual_commit(self):
        identity = release.preflight(self.root, IDENTITY["tag"])
        self.assertEqual(identity, dict(IDENTITY, commit=self.git("rev-parse", "HEAD")))

    def test_mismatched_version_and_tag_are_rejected(self):
        for tag in ("1.13.0", "v1.12.0", "v1.13.0-rc1"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.preflight(self.root, tag)

    def test_tag_cannot_identify_a_different_checkout(self):
        (self.root / "server.py").write_text("VERSION = 'changed'\n", encoding="utf-8")
        self.git("add", "server.py")
        self.git("commit", "--quiet", "-m", "Later source commit")
        with self.assertRaisesRegex(ValueError, "immutable version tag"):
            release.preflight(self.root, IDENTITY["tag"])

    def test_version_documents_must_be_present_and_match_the_release(self):
        notes = self.root / "docs" / ("UPDATE_" + VERSION + ".md")
        notes.unlink()
        with self.assertRaisesRegex(ValueError, "notes are missing"):
            release.preflight(self.root, IDENTITY["tag"])
        for contents in ("", "# Version 1.12.0\n"):
            notes.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, "heading does not match"):
                release.preflight(self.root, IDENTITY["tag"])
        notes.write_text(NOTES, encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("## 1.12.0\n\n## 1.13.0\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Latest CHANGELOG"):
            release.preflight(self.root, IDENTITY["tag"])

    def test_uncommitted_or_untracked_source_is_not_a_tagged_build(self):
        source = self.root / "server.py"
        original = source.read_bytes()
        source.write_bytes(original + b"uncommitted = True\n")
        with self.assertRaises(ValueError):
            release.preflight(self.root, IDENTITY["tag"])
        source.write_bytes(original)
        (self.root / "new_module.py").write_text("untracked = True\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            release.preflight(self.root, IDENTITY["tag"])

    def test_ignored_build_outputs_do_not_change_the_source_identity(self):
        for name in ("build", "dist"):
            (self.root / name).mkdir()
            (self.root / name / "output.bin").write_bytes(b"previous local output")
        self.assertEqual(release.preflight(self.root, IDENTITY["tag"])["commit"], self.git("rev-parse", "HEAD"))


class ReleaseAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def builds(self):
        artifacts = self.root / "artifacts"
        for platform in PLATFORM_PAYLOADS:
            directory = assets(artifacts / ("starhaven-" + platform), platform)
            release.stamp(directory, IDENTITY, platform)
        return artifacts

    def test_release_requires_exactly_the_five_payloads_and_their_checksums(self):
        self.assertEqual(release.asset_names(VERSION), ASSETS)
        directory = assets(self.root / "complete")
        self.assertEqual(set(release.verify_files(directory, VERSION)), set(ASSETS))
        for name in ASSETS:
            with self.subTest(missing=name):
                saved = (directory / name).read_bytes()
                (directory / name).unlink()
                with self.assertRaisesRegex(ValueError, "Missing or invalid"):
                    release.verify_files(directory, VERSION)
                (directory / name).write_bytes(saved)

    def test_changed_payload_or_wrong_checksum_filename_is_rejected(self):
        directory = assets(self.root / "complete")
        payload = directory / PAYLOADS[0]
        payload.write_bytes(b"unexpected binary")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            release.verify_files(directory, VERSION)
        checksum(payload)
        (directory / (payload.name + ".sha256")).write_text(digest(payload.read_bytes()) + "  different.dmg\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            release.verify_files(directory, VERSION)

    def test_source_version_must_match_even_with_a_valid_checksum(self):
        directory = assets(self.root / "complete")
        source_archive(directory / SOURCE, version="1.12.0")
        with self.assertRaisesRegex(ValueError, "Source ZIP VERSION mismatch"):
            release.verify_files(directory, VERSION)

    def test_three_validated_builds_assemble_into_exactly_ten_assets(self):
        artifacts = self.builds()
        output = self.root / "assembled"
        verified = release.assemble(artifacts, output, IDENTITY)
        self.assertEqual(sorted(path.name for path in output.iterdir()), ASSETS)
        self.assertEqual(verified, {name: digest((output / name).read_bytes()) for name in ASSETS})

    def test_build_commit_tag_and_platform_cannot_be_substituted(self):
        artifacts = self.builds()
        manifest = artifacts / "starhaven-windows-x64" / release.MANIFEST
        original = json.loads(manifest.read_text(encoding="utf-8"))
        for key, value in (("commit", "b" * 40), ("tag", "v1.12.0"), ("version", "1.12.0"), ("platform", "macos-arm64")):
            with self.subTest(field=key):
                manifest.write_text(json.dumps(dict(original, **{key: value})), encoding="utf-8")
                output = self.root / ("wrong-" + key)
                with self.assertRaises(ValueError):
                    release.assemble(artifacts, output, IDENTITY)
                self.assertFalse(output.exists())
        manifest.write_text(json.dumps(original), encoding="utf-8")

    def test_payload_changes_after_stamping_are_rejected(self):
        artifacts = self.builds()
        directory = artifacts / "starhaven-macos-arm64"
        path = directory / PLATFORM_PAYLOADS["macos-arm64"][0]
        path.write_bytes(b"different but internally checksummed build")
        checksum(path)
        with self.assertRaisesRegex(ValueError, "Build changed after validation"):
            release.assemble(artifacts, self.root / "assembled", IDENTITY)

    def test_three_source_archives_must_be_byte_identical(self):
        artifacts = self.builds()
        directory = artifacts / "starhaven-windows-x64"
        source_archive(directory / SOURCE, extra=b"different source tree\n")
        release.stamp(directory, IDENTITY, "windows-x64")
        output = self.root / "assembled"
        with self.assertRaisesRegex(ValueError, "Cross-platform source archives differ"):
            release.assemble(artifacts, output, IDENTITY)
        self.assertFalse(output.exists())


class ReleasePublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = assets(self.root / "assets")
        self.evidence = self.root / "build" / "release-verification.json"

    def publish(self, client):
        return release.publish(client, IDENTITY, self.directory, NOTES, self.evidence)

    def test_remote_tag_and_exact_commit_checks_gate_all_remote_writes(self):
        cases = (
            ("wrong tag", {"type": "commit", "sha": "b" * 40}, [{"head_sha": IDENTITY["commit"], "conclusion": "success"}]),
            ("failed checks", None, [{"head_sha": IDENTITY["commit"], "conclusion": "failure"}]),
            ("other commit", None, [{"head_sha": "b" * 40, "conclusion": "success"}]),
            ("no checks", None, []),
        )
        for name, target, runs in cases:
            with self.subTest(case=name):
                client = FakeGitHub(self.directory)
                if target:
                    client.target = target
                client.runs = runs
                with self.assertRaises(ValueError):
                    self.publish(client)
                self.assertEqual(client.writes, [])
                self.assertFalse(self.evidence.exists())

    def test_missing_local_asset_is_rejected_before_api_calls(self):
        (self.directory / ASSETS[0]).unlink()
        client = FakeGitHub(self.directory)
        with self.assertRaises(ValueError):
            self.publish(client)
        self.assertEqual(client.calls, [])

    def test_published_conflicting_asset_is_never_replaced(self):
        client = FakeGitHub(self.directory, draft=False, existing=ASSETS)
        client.stored[PAYLOADS[0]] = b"previously published different content"
        before = copy.deepcopy(client.stored)
        with self.assertRaisesRegex(ValueError, "refusing replacement"):
            self.publish(client)
        self.assertEqual(client.writes, [])
        self.assertEqual(client.stored, before)
        self.assertFalse(self.evidence.exists())

    def test_incomplete_public_release_cannot_be_silently_rewritten(self):
        client = FakeGitHub(self.directory, draft=False, existing=ASSETS[:-1])
        with self.assertRaisesRegex(ValueError, "exactly ten assets"):
            self.publish(client)
        self.assertEqual(client.writes, [])

    def test_draft_conflicting_asset_also_stops_without_deleting_it(self):
        client = FakeGitHub(self.directory, draft=True, existing=ASSETS[:2])
        client.stored[ASSETS[0]] = b"other draft output"
        before = copy.deepcopy(client.stored)
        with self.assertRaisesRegex(ValueError, "refusing replacement"):
            self.publish(client)
        self.assertEqual(client.writes, [])
        self.assertEqual(client.stored, before)

    def test_interrupted_draft_resumes_only_missing_uploads(self):
        client = FakeGitHub(self.directory)
        client.upload_limit = 3
        with self.assertRaisesRegex(RuntimeError, "interrupted upload"):
            self.publish(client)
        self.assertTrue(client.release["draft"])
        self.assertEqual(len(client.stored), 3)
        self.assertFalse(self.evidence.exists())
        original = copy.deepcopy(client.stored)
        client.calls.clear()
        client.upload_limit = None
        record = self.publish(client)
        uploads = [call[3] for call in client.writes if call[3]]
        self.assertEqual(set(uploads), set(ASSETS) - set(original))
        self.assertEqual(len(uploads), len(ASSETS) - len(original))
        self.assertEqual({name: client.stored[name] for name in original}, original)
        self.assertEqual([call[0] for call in client.writes if not call[3]], ["PATCH"])
        self.assertFalse(client.release["draft"])
        self.assertTrue(record["downloaded_and_verified"])

    def test_matching_public_release_is_idempotent_and_still_downloads(self):
        client = FakeGitHub(self.directory, draft=False, existing=ASSETS)
        client.target = {"type": "tag", "sha": "c" * 40}
        for _ in range(2):
            record = self.publish(client)
            self.assertEqual(record["commit"], IDENTITY["commit"])
            self.assertEqual(record["checks_run"], 101)
            self.assertEqual(set(record["assets"]), set(ASSETS))
        self.assertEqual(client.writes, [])
        self.assertEqual(client.downloads, [IDENTITY["tag"], IDENTITY["tag"]])

    def test_existing_release_notes_mismatch_is_not_overwritten(self):
        client = FakeGitHub(self.directory, draft=True)
        client.release["body"] = "other release notes"
        with self.assertRaisesRegex(ValueError, "refusing replacement"):
            self.publish(client)
        self.assertEqual(client.writes, [])

    def test_created_release_with_wrong_tag_is_not_uploaded_or_published(self):
        client = FakeGitHub(self.directory)
        client.created_tag_override = "v1.12.0"
        with self.assertRaisesRegex(ValueError, "different tag or notes"):
            self.publish(client)
        self.assertEqual([(call[0], call[1]) for call in client.writes],
                         [("POST", "repos/" + client.repository + "/releases")])
        self.assertEqual(client.stored, {})
        self.assertFalse(self.evidence.exists())

    def test_branch_metadata_does_not_block_a_draft_with_the_verified_tag_commit(self):
        client = FakeGitHub(self.directory, draft=True, existing=ASSETS[:2])
        client.release["target_commitish"] = "main"
        original = copy.deepcopy(client.stored)
        record = self.publish(client)
        self.assertEqual(record["commit"], IDENTITY["commit"])
        self.assertEqual({name: client.stored[name] for name in original}, original)
        self.assertEqual({call[3] for call in client.writes if call[3]}, set(ASSETS) - set(original))
        self.assertFalse(client.release["draft"])

    def test_tag_moving_during_upload_prevents_publication(self):
        client = FakeGitHub(self.directory, draft=True)
        client.move_tag_after_upload = True
        with self.assertRaisesRegex(ValueError, "Remote tag differs"):
            self.publish(client)
        self.assertFalse(any(call[0] == "PATCH" for call in client.writes))
        self.assertTrue(client.release["draft"])
        self.assertFalse(self.evidence.exists())

    def test_tag_moving_during_download_prevents_success_evidence(self):
        client = FakeGitHub(self.directory, draft=False, existing=ASSETS)
        client.move_tag_after_download = True
        with self.assertRaisesRegex(ValueError, "Remote tag differs"):
            self.publish(client)
        self.assertEqual(client.writes, [])
        self.assertFalse(self.evidence.exists())

    def test_download_failure_or_corruption_never_writes_success_evidence(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                client = FakeGitHub(self.directory, draft=False, existing=ASSETS)
                if corrupt:
                    client.corrupt_download = True
                else:
                    client.download_error = RuntimeError("Simulated failed download")
                with self.assertRaises((ValueError, RuntimeError)):
                    self.publish(client)
                self.assertEqual(client.writes, [])
                self.assertFalse(self.evidence.exists())


class DeterministicSourceExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source_tree(self, root, newline):
        files = {
            "VERSION": VERSION + "\n", "README.md": "Public source.\nSecond line.\n",
            "AGENTS.md": "Public repository guidance.\n",
            ".agents/skills/starhaven-release/SKILL.md": "Release instructions.\n",
            ".agents/skills/starhaven-release/agents/openai.yaml": "interface: {}\n",
            ".agents/skills/private-helper/SKILL.md": "Private helper.\n",
            ".codex/skills/private-helper/SKILL.md": "Private helper.\n",
            ".agents/local-settings.json": "{}\n", "data/town.sqlite3": "personal data\n",
            "build/private-handoff.md": "Private handoff.\n",
            "打开星栖小镇.command": "#!/bin/sh\nexit 0\n",
            "docs/UPDATE_1.13.0.md": NOTES,
        }
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.replace("\n", newline).encode("utf-8"))
        binary = root / "web" / "pixel.png"
        binary.parent.mkdir(exist_ok=True)
        binary.write_bytes(b"\x89PNG\r\n\x1a\nfixture\r\nbinary")

    def export(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            return export_source.export_source(root)

    def test_export_contains_public_guidance_and_only_the_release_skill(self):
        root = self.root / "source"
        self.source_tree(root, "\n")
        archive = self.export(root)
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            self.assertIn("starhaven-town/AGENTS.md", names)
            self.assertIn("starhaven-town/.agents/skills/starhaven-release/SKILL.md", names)
            self.assertIn("starhaven-town/.agents/skills/starhaven-release/agents/openai.yaml", names)
            self.assertFalse(any("private-helper" in name or "local-settings" in name for name in names))
            self.assertFalse(any(name.startswith(("starhaven-town/data/", "starhaven-town/build/", "starhaven-town/.codex/")) for name in names))

    def test_source_bytes_ignore_checkout_newlines_timestamps_and_permissions(self):
        unix, windows = self.root / "unix", self.root / "windows"
        self.source_tree(unix, "\n")
        self.source_tree(windows, "\r\n")
        for path in windows.rglob("*"):
            if path.is_file():
                os.utime(path, (1700000000, 1700000000))
                path.chmod(0o755)
        first, second = self.export(unix), self.export(windows)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.with_name(first.name + ".sha256").read_bytes(), second.with_name(second.name + ".sha256").read_bytes())
        self.assertEqual((unix / "dist" / "source-files.txt").read_bytes(), (windows / "dist" / "source-files.txt").read_bytes())
        with zipfile.ZipFile(first) as package:
            self.assertEqual(package.namelist(), sorted(package.namelist()))
            self.assertEqual(package.read("starhaven-town/web/pixel.png"), (unix / "web" / "pixel.png").read_bytes())
            for item in package.infolist():
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(item.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(item.create_system, 3)
                self.assertEqual(item.external_attr >> 16, 0o100755 if item.filename.endswith(".command") else 0o100644)


if __name__ == "__main__":
    unittest.main()
