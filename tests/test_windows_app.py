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


if __name__ == "__main__":
    unittest.main()
