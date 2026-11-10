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


def read_shortcut_target(path):
    """Read the real persisted link through Unicode IShellLinkW, not WScript."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                    ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    evidence = {}
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()

    def method(pointer, index, result, *arguments):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(vtable[index])

    initialized = ole.CoInitializeEx(None, 2)  # apartment threaded
    evidence["initialize_hresult"] = initialized
    try:
        clsid = guid("00021401-0000-0000-c000-000000000046")
        iid = guid("000214f9-0000-0000-c000-000000000046")
        evidence["create_hresult"] = ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link))
        if not link:
            return evidence
        iid_file = guid("0000010b-0000-0000-c000-000000000046")
        evidence["query_hresult"] = method(link, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(link, ctypes.byref(iid_file), ctypes.byref(persist))
        if not persist:
            return evidence
        evidence["load_hresult"] = method(persist, 5, ctypes.c_long, wintypes.LPCWSTR, wintypes.DWORD)(persist, str(path), 0)
        for name, flags in (("target", 0), ("raw_target", 4)):
            buffer = ctypes.create_unicode_buffer(32768)
            evidence[name + "_hresult"] = method(link, 3, ctypes.c_long, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(link, buffer, len(buffer), None, flags)
            evidence[name] = buffer.value
        return evidence
    finally:
        for pointer in (persist, link):
            if pointer:
                method(pointer, 2, wintypes.ULONG)(pointer)
        if initialized in (0, 1):
            ole.CoUninitialize()


def launch_shortcut(path, report, data_directory):
    """Exercise the user's desktop shortcut through the Windows Shell."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE)]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # process handle, synchronous shell launch, no shell error UI
    info.lpVerb = "open"
    info.lpFile = str(path)
    info.lpParameters = subprocess.list2cmdline(["--self-test", str(report), "--data-dir", str(data_directory)])
    info.nShow = 1
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    initialized = ole.CoInitializeEx(None, 2)
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not info.hProcess:
            raise RuntimeError("Shell did not return the desktop shortcut's process handle")
        if kernel.WaitForSingleObject(info.hProcess, 110000) != 0:
            kernel.TerminateProcess(info.hProcess, 1)
            raise TimeoutError("Desktop shortcut did not complete the native map self-test")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)
        if initialized in (0, 1):
            ole.CoUninitialize()


def read_setup_log(path):
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")


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
                # Resolve the real desktop first; Unicode COM below loads and
                # validates the persisted shortcut before the Shell launches it.
                script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$link = Join-Path $desktop '星栖小镇.lnk'
$exists = Test-Path -LiteralPath $link -PathType Leaf
@{ desktop=$desktop; link=$link; exists=$exists; entries=@(Get-ChildItem -LiteralPath $desktop | Select-Object -ExpandProperty Name) } | ConvertTo-Json -Compress
"""
                encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
                raw = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], encoding="utf-8-sig")
                info = json.loads(raw)
                if info["exists"]:
                    shutil.copy2(info["link"], evidence_directory() / "desktop-shortcut.lnk")
                    info["native"] = read_shortcut_target(info["link"])
                (evidence_directory() / "shortcut.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                log = read_setup_log(evidence_directory() / "setup-1.log")
                self.assertTrue(info["exists"], "desktop shortcut missing: %s\n%s" % (info, "\n".join(log.splitlines()[-35:])))
                native = info["native"]
                self.assertEqual(native.get("load_hresult"), 0, native)
                self.assertEqual(native.get("target_hresult"), 0, native)
                self.assertTrue(native.get("target"), "native desktop shortcut target is empty: %s" % native)
                self.assertTrue(Path(native["target"]).samefile(target / "StarhavenTown.exe"), "desktop shortcut points to another executable: %s" % native)
                report = evidence_directory() / "shortcut-launch.json"
                exit_code = launch_shortcut(info["link"], report, Path(temporary) / "桌面启动 数据")
                self.assertTrue(report.is_file(), "desktop shortcut did not produce its self-test report")
                launch = json.loads(report.read_text(encoding="utf-8-sig"))
                self.assertEqual(exit_code, 0, launch)
                self.assertTrue(launch.get("ok"), launch)
                self.assertTrue(launch.get("graceful_shutdown"), launch)
                self.assertGreater(launch.get("residents", 0), 0, launch)
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
