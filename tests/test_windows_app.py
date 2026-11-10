import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_windows_app():
    path = Path(__file__).resolve().parents[1] / "desktop" / "windows_app.py"
    spec = importlib.util.spec_from_file_location("starhaven_windows_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsAppHelpersTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_windows_app()

    def test_backend_directory_prefers_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / "backend"
            backend.mkdir()
            (backend / "desktop_runner.py").write_text("#", encoding="utf-8")
            self.assertEqual(self.mod.backend_directory(root), backend)

    def test_wait_ready_reads_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"

            class Proc:
                def poll(self):
                    return None

            def writer():
                time.sleep(0.05)
                ready.write_text(json.dumps({"port": 12345, "pid": 1}), encoding="utf-8")

            threading.Thread(target=writer, daemon=True).start()
            payload = self.mod.wait_ready(ready, Proc(), timeout=2)
            self.assertEqual(payload["port"], 12345)

    def test_wait_ready_detects_backend_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"

            class Proc:
                def poll(self):
                    return 1

            with self.assertRaisesRegex(RuntimeError, "后端启动失败"):
                self.mod.wait_ready(ready, Proc(), timeout=1)


class CredentialStorePlatformTest(unittest.TestCase):
    def test_storage_labels(self):
        from credential_store import CredentialStore
        store = CredentialStore(Path(tempfile.gettempdir()) / "starhaven-cred-test")
        if sys.platform == "darwin":
            self.assertEqual(store.storage, "keychain")
            self.assertTrue(store.supports_persistence)
        elif sys.platform == "win32":
            self.assertEqual(store.storage, "credential-manager")
            self.assertTrue(store.supports_persistence)
        else:
            self.assertEqual(store.storage, "environment")
            self.assertFalse(store.supports_persistence)


class WindowsProcessLifetimeTest(unittest.TestCase):
    def test_parent_liveness_keeps_full_native_handles(self):
        from desktop_runner import _parent_running
        self.assertTrue(_parent_running(os.getpid()))
        self.assertFalse(_parent_running(-1))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        self.assertFalse(_parent_running(child.pid))


class DesktopGracefulShutdownTest(unittest.TestCase):
    def test_private_stop_signal_shuts_down_and_removes_ready_file(self):
        with tempfile.TemporaryDirectory(prefix="starhaven-shutdown-") as directory:
            root = Path(directory)
            ready = root / "ready.json"
            stop = root / "stop.request"
            runner = Path(__file__).resolve().parents[1] / "desktop_runner.py"
            process = subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-B", str(runner),
                "--data-dir", str(root / "用户 数据"), "--ready-file", str(ready),
                "--stop-file", str(stop), "--parent-pid", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                mod = load_windows_app()
                try:
                    mod.wait_ready(ready, process, timeout=20)
                except RuntimeError as exc:
                    if process.poll() is None:
                        process.kill()
                    _, error = process.communicate(timeout=5)
                    self.fail("backend did not start (%s): %s" % (exc, error.decode("utf-8", errors="replace")))
                stop.write_text("stop", encoding="utf-8")
                _, error = process.communicate(timeout=12)
                self.assertEqual(process.returncode, 0, error)
                self.assertFalse(ready.exists())
                self.assertTrue((root / "用户 数据" / "town.sqlite3").is_file())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                elif process.stderr:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
