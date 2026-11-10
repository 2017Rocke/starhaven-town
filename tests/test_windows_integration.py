"""Opt-in real Windows installer, WebView2 and Unicode-path checks.

Run only on a disposable Windows build/test account. The installer creates that
account's Start menu / desktop shortcuts and an uninstall registration.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

APP = os.environ.get("STARHAVEN_WINDOWS_APP")
INSTALLER = os.environ.get("STARHAVEN_WINDOWS_INSTALLER")


@unittest.skipUnless(sys.platform == "win32" and APP, "set STARHAVEN_WINDOWS_APP on a Windows test account")
class WindowsNativeIntegrationTest(unittest.TestCase):
    def test_unicode_install_path_webview_backend_cleanup_and_saved_data(self):
        with tempfile.TemporaryDirectory(prefix="starhaven-test-") as temporary:
            root = Path(temporary)
            package = root / "中文小镇 with spaces"
            shutil.copytree(Path(APP).resolve().parent, package)
            executable = package / "StarhavenTown.exe"
            data_dir = root / "居民数据 with spaces"
            for attempt in range(2):
                output = root / ("result-%d.json" % attempt)
                result = subprocess.run([str(executable), "--self-test", str(output), "--data-dir", str(data_dir)], timeout=110, capture_output=True)
                self.assertTrue(output.is_file(), "native app produced no report; exit=%s" % result.returncode)
                payload = json.loads(output.read_text(encoding="utf-8-sig"))
                self.assertEqual(result.returncode, 0, payload)
                self.assertTrue(payload.get("ok"), payload)
                self.assertTrue(payload.get("graceful_shutdown"), payload)
                self.assertIn("星栖", payload["title"])
                self.assertTrue((data_dir / "town.sqlite3").is_file())
                self.assertEqual(Path(payload["data_directory"]), data_dir)
                if attempt:
                    self.assertEqual((data_dir / "keep-me.txt").read_text(encoding="utf-8"), "升级后保留")
                (data_dir / "keep-me.txt").write_text("升级后保留", encoding="utf-8")
                with socket.socket() as sock:
                    sock.settimeout(2)
                    self.assertNotEqual(sock.connect_ex(("127.0.0.1", payload["port"])), 0, "backend survived app shutdown")


@unittest.skipUnless(sys.platform == "win32" and INSTALLER, "set STARHAVEN_WINDOWS_INSTALLER on a disposable Windows test account")
class WindowsInstallerIntegrationTest(unittest.TestCase):
    def test_install_upgrade_shortcut_and_uninstall_preserve_user_data(self):
        with tempfile.TemporaryDirectory(prefix="starhaven-setup-") as temporary:
            target = Path(temporary) / "安装小镇 with spaces"
            user_data = Path(os.environ["LOCALAPPDATA"]) / "StarhavenTown"
            user_data.mkdir(parents=True, exist_ok=True)
            marker = user_data / ("installer-test-" + uuid.uuid4().hex + ".txt")
            marker.write_text("preserve me", encoding="utf-8")
            try:
                args = [str(Path(INSTALLER).resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/TASKS=desktopicon", "/DIR=" + str(target)]
                for _ in range(2):
                    subprocess.run(args, check=True, timeout=150)
                    self.assertTrue((target / "StarhavenTown.exe").is_file())
                    self.assertTrue((target / "unins000.exe").is_file())
                    self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")
                # WScript resolves redirected desktops and inspects the actual shortcut target.
                script = "$s=New-Object -ComObject WScript.Shell; $p=Join-Path ([Environment]::GetFolderPath('Desktop')) '星栖小镇.lnk'; $s.CreateShortcut($p).TargetPath"
                path = subprocess.check_output(["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script], encoding="utf-8-sig").strip()
                self.assertEqual(Path(path), target / "StarhavenTown.exe")
            finally:
                uninstaller = target / "unins000.exe"
                if uninstaller.is_file():
                    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=90)
                    # Inno may finish deleting through a short-lived second process.
                    for _ in range(50):
                        if not (target / "StarhavenTown.exe").exists():
                            break
                        time.sleep(.1)
                    self.assertFalse((target / "StarhavenTown.exe").exists())
                self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")
                marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
