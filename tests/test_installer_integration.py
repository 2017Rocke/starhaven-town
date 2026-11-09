"""Exercise the real macOS installer only when explicitly enabled.

Run before launching the real desktop app, for example::

    STARHAVEN_TEST_INSTALLER='/path/星栖小镇安装器.app' \
        python3 tests/test_installer_integration.py -v

Every app, shortcut, backup and data fixture created by these tests is inside a
TemporaryDirectory. No model requests or app launches are made.
"""

import hashlib
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest


INSTALLER_ENV = os.environ.get("STARHAVEN_TEST_INSTALLER", "")
APP_NAME = "星栖小镇.app"
BUNDLE_ID = "cn.starhaven.town"


@unittest.skipUnless(
    sys.platform == "darwin" and bool(INSTALLER_ENV),
    "Set STARHAVEN_TEST_INSTALLER to a built macOS installer bundle to run integration tests.",
)
class InstallerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer_bundle = Path(INSTALLER_ENV).expanduser().resolve()
        cls.installer = cls.installer_bundle / "Contents" / "MacOS" / "StarhavenInstaller"
        if not cls.installer.is_file():
            raise AssertionError("STARHAVEN_TEST_INSTALLER must point to the built installer .app bundle")
        cls.payload = cls.installer_bundle / "Contents" / "Resources" / "Payload" / APP_NAME
        cls.payload_info = (cls.payload / "Contents" / "Info.plist").read_bytes()
        cls.payload_settings = plistlib.loads(cls.payload_info)
        if cls.payload_settings.get("CFBundleIdentifier") != BUNDLE_ID:
            raise AssertionError("Installer payload is not the Starhaven Town application")
        cls.payload_executable = cls.payload_settings["CFBundleExecutable"]
        cls.payload_binary_hash = cls._hash(
            cls.payload / "Contents" / "MacOS" / cls.payload_executable
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="starhaven-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.apps = self.root / "Applications"
        self.desktop = self.root / "Desktop"
        self.target = self.apps / APP_NAME
        self.shortcut = self.desktop / APP_NAME
        self.backups = self.apps / ".starhaven-backups"
        self.data = self.root / "Library" / "Application Support" / "StarhavenTown"
        self.data.mkdir(parents=True)
        (self.data / "settings.json").write_text(
            '{"theme":"pixel","test_marker":"keep-this-setting"}', encoding="utf-8"
        )
        (self.data / "memories").mkdir()
        (self.data / "memories" / "resident.txt").write_text("保留居民的独特记忆", encoding="utf-8")
        with sqlite3.connect(self.data / "town.sqlite3") as database:
            database.execute("CREATE TABLE test_memories (value TEXT NOT NULL)")
            database.execute("INSERT INTO test_memories VALUES (?)", ("原有聊天与成长记录",))
        self.original_data = self._data_snapshot()

    @staticmethod
    def _hash(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _data_snapshot(self):
        return {
            str(path.relative_to(self.data)): self._hash(path)
            for path in self.data.rglob("*") if path.is_file()
        }

    def _install(self, *, desktop=None):
        return subprocess.run(
            [str(self.installer), "--install", "--no-launch",
             "--install-root", str(self.apps),
             "--desktop-dir", str(desktop or self.desktop)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )

    def _assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("安装成功", result.stdout)
        self.assertEqual((self.target / "Contents" / "Info.plist").read_bytes(), self.payload_info)
        binary = self.target / "Contents" / "MacOS" / self.payload_executable
        self.assertEqual(self._hash(binary), self.payload_binary_hash)
        self.assertTrue(os.access(binary, os.X_OK), "Installed executable lost its execute permissions")

    def _assert_data_preserved(self):
        self.assertEqual(self._data_snapshot(), self.original_data)
        self.assertFalse(list(self.apps.glob(".Starhaven-install-*.app")), "Staging app was left behind")

    def test_first_install_creates_app_and_desktop_link_without_changing_data(self):
        self._assert_success(self._install())
        self.assertTrue(self.shortcut.is_symlink())
        self.assertEqual(self.shortcut.resolve(), self.target)
        self.assertFalse(self.backups.exists(), "First install should not create an old-app backup")
        self._assert_data_preserved()

    def test_update_retains_old_app_backup_and_desktop_link(self):
        self._assert_success(self._install())
        old_inode = self.target.stat().st_ino
        self._assert_success(self._install())
        saved = list(self.backups.glob("*.app"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].stat().st_ino, old_inode, "Old app should be moved intact to its backup")
        self.assertEqual((saved[0] / "Contents" / "Info.plist").read_bytes(), self.payload_info)
        self.assertEqual(self.shortcut.resolve(), self.target)
        self.assertNotEqual(self.target.stat().st_ino, old_inode)
        self._assert_data_preserved()

    def test_desktop_name_conflict_preserves_existing_item(self):
        self.desktop.mkdir()
        conflict = b"This desktop item belongs to the user."
        self.shortcut.write_bytes(conflict)
        result = self._install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("桌面已有同名内容", result.stderr)
        self.assertEqual(self.shortcut.read_bytes(), conflict)
        self.assertFalse(self.shortcut.is_symlink())
        self.assertFalse(self.target.exists())
        self._assert_data_preserved()

    def test_failed_update_restores_old_app_and_retains_backup(self):
        self._assert_success(self._install())
        old_inode = self.target.stat().st_ino
        blocked_parent = self.root / "desktop-parent-is-a-file"
        blocked_parent.write_bytes(b"preserve this blocking file")
        result = self._install(desktop=blocked_parent / "Desktop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("安装没有完成", result.stderr)
        self.assertEqual(blocked_parent.read_bytes(), b"preserve this blocking file")
        self.assertEqual((self.target / "Contents" / "Info.plist").read_bytes(), self.payload_info)
        self.assertEqual(
            self._hash(self.target / "Contents" / "MacOS" / self.payload_executable),
            self.payload_binary_hash,
        )
        saved = list(self.backups.glob("*.app"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].stat().st_ino, old_inode)
        self.assertEqual(self.shortcut.resolve(), self.target)
        self._assert_data_preserved()


if __name__ == "__main__":
    unittest.main()
