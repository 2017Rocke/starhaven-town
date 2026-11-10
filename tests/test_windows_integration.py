"""Opt-in real Windows installer, WebView2 and Unicode-path checks.

Run only on a disposable Windows build/test account. The installer creates that
account's Start menu / desktop shortcuts and an uninstall registration.
"""
import base64
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


def evidence_directory():
    path = Path(__file__).resolve().parents[1] / "build" / "windows-test-evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path



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
                shutil.copy2(output, evidence_directory() / ("native-%d.json" % attempt))
                application_log = data_dir / "logs" / "desktop-windows.log"
                if application_log.is_file():
                    shutil.copy2(application_log, evidence_directory() / "desktop-windows.log")
                payload = json.loads(output.read_text(encoding="utf-8-sig"))
                self.assertEqual(result.returncode, 0, payload)
                self.assertTrue(payload.get("ok"), payload)
                self.assertTrue(payload.get("graceful_shutdown"), payload)
                self.assertIn("星栖", payload["title"])
                self.assertTrue((data_dir / "town.sqlite3").is_file())
                self.assertTrue(Path(payload["data_directory"]).samefile(data_dir), "native app used a different data directory")
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
                for attempt in range(2):
                    subprocess.run(args + ["/LOG=" + str(evidence_directory() / ("setup-%d.log" % attempt))], check=True, timeout=150)
                    self.assertTrue((target / "StarhavenTown.exe").is_file())
                    self.assertTrue((target / "unins000.exe").is_file())
                    self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")
                # Encode the script explicitly: Windows PowerShell 5.1 can lose
                # non-ASCII -Command text when launched through native wrappers.
                # CreateShortcut alone silently returns an empty object for a
                # missing link, so verify the real file before asking its target.
                script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$link = Join-Path $desktop '星栖小镇.lnk'
$exists = Test-Path -LiteralPath $link -PathType Leaf
$target = ''
if ($exists) { $shell = New-Object -ComObject WScript.Shell; $target = $shell.CreateShortcut($link).TargetPath }
@{ desktop=$desktop; link=$link; exists=$exists; target=$target; entries=@(Get-ChildItem -LiteralPath $desktop | Select-Object -ExpandProperty Name) } | ConvertTo-Json -Compress
"""
                encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
                raw = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], encoding="utf-8-sig")
                info = json.loads(raw)
                (evidence_directory() / "shortcut.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                log = (evidence_directory() / "setup-1.log").read_text(encoding="utf-8-sig", errors="replace")
                self.assertTrue(info["exists"], "desktop shortcut missing: %s\n%s" % (info, "\n".join(log.splitlines()[-35:])))
                self.assertTrue(info["target"], "desktop shortcut target is empty: %s" % info)
                self.assertTrue(Path(info["target"]).samefile(target / "StarhavenTown.exe"), "desktop shortcut points to another executable: %s" % info)
            finally:
                uninstaller = target / "unins000.exe"
                if uninstaller.is_file():
                    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=" + str(evidence_directory() / "uninstall.log")], check=True, timeout=90)
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
