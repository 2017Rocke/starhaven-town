"""Provider/security regression checks: synthetic credentials and local mocks only."""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credential_store import CredentialStore, CredentialError
from provider_bridge import MAX_RESPONSE, ProviderBridge, SettingsError, normalize_base_url
from server import APIError, TownApp, VERSION, create_server


class MemoryCredentials:
    storage = "keychain"
    supports_persistence = True

    def __init__(self):
        self.items = {}

    def get(self, url):
        return self.items.get(url, "")

    def set(self, url, value):
        self.items[url] = value


def reply_bytes(text="你好，欢迎来到小镇。"):
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode()


class MockOpener:
    def __init__(self, body=None, error=None, wait=None):
        self.body = body if body is not None else reply_bytes()
        self.error = error
        self.wait = wait
        self.started = threading.Event()
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        self.started.set()
        if self.wait is not None:
            self.wait.wait(3)
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.credentials = MemoryCredentials()
        self.opener = MockOpener()
        self.bridge = ProviderBridge(self.root / "runtime", self.root / "data", self.credentials,
                                     opener_factory=lambda: self.opener)
        self.config = {"mode": "api", "base_url": "https://models.example/v1", "model": "user-selected-model"}
        self.bridge.save_settings(self.config)

    def tearDown(self):
        if self.opener.wait:
            self.opener.wait.set()
        self.bridge.close()
        self.temp.cleanup()

    def test_settings_never_return_or_write_key_and_destinations_are_isolated(self):
        secret = "synthetic-key-for-test-only"
        saved = self.bridge.save_settings(dict(self.config, api_key=secret))
        self.assertTrue(saved["has_api_key"])
        self.assertNotIn(secret, json.dumps(saved))
        self.assertNotIn("api_key", json.loads(self.bridge.path.read_text()))
        self.assertNotIn(secret, self.bridge.path.read_text())
        self.assertEqual(self.bridge.path.stat().st_mode & 0o777, 0o600)
        different = self.bridge.save_settings(dict(self.config, base_url="https://other.example/v1"))
        self.assertFalse(different["has_api_key"])
        self.assertTrue(self.bridge.save_settings(self.config)["has_api_key"])
        self.assertFalse(self.bridge.save_settings(dict(self.config, api_key=""))["has_api_key"])

    def test_url_validation_and_explicit_model(self):
        for bad in ("http://models.example/v1", "file:///tmp/key", "https://user:pass@example.com/v1",
                    "https://models.example/v1?key=secret", "https://models.example/#token", "http://127.0.0.1.evil/v1",
                    "https://example.com\\@evil/v1", "https://example.com/\nsecret", "https://example.com:0/v1"):
            with self.subTest(url=bad), self.assertRaises(SettingsError):
                normalize_base_url(bad)
        self.assertEqual(normalize_base_url("http://localhost:11434/v1/"), "http://localhost:11434/v1")
        self.assertEqual(normalize_base_url("https://example.com/v1/chat/completions"), "https://example.com/v1")
        with self.assertRaises(SettingsError):
            self.bridge.save_settings(dict(self.config, model=""))
        with self.assertRaises(SettingsError):
            self.bridge.save_settings(dict(self.config, api_key="key\r\ninjected-header"))
        self.assertEqual(self.bridge.settings()["model"], "user-selected-model")

    def test_text_protocol_sends_only_explicit_model_prompt_and_key_header(self):
        self.bridge.save_settings(dict(self.config, api_key="synthetic-test-key"))
        reply = self.bridge.generate("本次角色上下文", threading.Event())
        self.assertIn("欢迎", reply)
        request = self.opener.requests[0]
        self.assertEqual(request.full_url, "https://models.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "user-selected-model")
        self.assertEqual(payload["messages"][1]["content"], "本次角色上下文")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertNotIn("tools", payload)
        self.assertNotIn("synthetic-test-key", request.data.decode())

    def test_local_model_can_run_without_a_key(self):
        self.bridge.save_settings(dict(self.config, base_url="http://127.0.0.1:11434/v1"))
        self.bridge.generate("你好", threading.Event())
        self.assertIsNone(self.opener.requests[0].get_header("Authorization"))

    def test_errors_and_even_echoed_key_are_redacted(self):
        secret = "synthetic-do-not-expose"
        self.bridge.save_settings(dict(self.config, api_key=secret))
        self.opener = MockOpener(error=urllib.error.HTTPError("https://example.com/" + secret, 401, secret, {}, io.BytesIO(secret.encode())))
        with self.assertRaises(RuntimeError) as caught:
            self.bridge.generate("你好", threading.Event())
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("密钥", str(caught.exception))
        self.opener = MockOpener(body=reply_bytes("服务错误回显 " + secret))
        self.assertNotIn(secret, self.bridge.generate("你好", threading.Event()))

    def test_oversize_and_malformed_responses_are_rejected(self):
        for body in (b"x" * (MAX_RESPONSE + 1), b"not json", b'{"choices":[]}'):
            self.opener = MockOpener(body=body)
            with self.assertRaises(RuntimeError):
                self.bridge.generate("你好", threading.Event())

    def test_cancel_is_prompt_and_retains_slot_until_connection_ends(self):
        release = threading.Event()
        self.opener = MockOpener(wait=release)
        cancel = threading.Event()
        result = []
        def run():
            try:
                result.append(self.bridge.generate("等待", cancel))
            except RuntimeError as exc:
                result.append(str(exc))
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(self.opener.started.wait(1))
        cancel.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIn("已取消", result[0])
        self.assertEqual(len(self.bridge._pending), 1)
        with self.assertRaises(SettingsError):
            self.bridge.save_settings(self.config)
        release.set()
        deadline = time.monotonic() + 1
        while self.bridge._pending and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.bridge._pending)

    def test_timeout_and_close_never_deliver_late_reply(self):
        release = threading.Event()
        self.opener = MockOpener(wait=release)
        self.bridge.timeout = .1
        with self.assertRaisesRegex(RuntimeError, "等待较久"):
            self.bridge.generate("等待", threading.Event())
        release.set()
        self.bridge.close()
        with self.assertRaisesRegex(RuntimeError, "已取消"):
            self.bridge.generate("已经关闭", threading.Event())

    def test_corrupt_configuration_does_not_guess_model_or_expose_content(self):
        self.bridge.path.write_text("synthetic-secret-invalid-json")
        another = ProviderBridge(self.root / "runtime2", self.root / "data", self.credentials)
        try:
            self.assertFalse(another.settings()["configured"])
            self.assertEqual(another.settings()["model"], "")
            self.assertNotIn("synthetic-secret", json.dumps(another.settings()))
        finally:
            another.close()

    def test_non_mac_environment_only_and_key_scope(self):
        credentials = CredentialStore(self.root)
        credentials.supports_persistence = False
        with patch.dict(os.environ, {"STARHAVEN_API_KEY": "environment-test-key"}):
            self.assertEqual(credentials.get(self.config["base_url"]), "environment-test-key")
        with self.assertRaises(CredentialError):
            credentials.set(self.config["base_url"], "not-written")
        self.assertNotEqual(credentials._account("https://one.example"), credentials._account("https://two.example"))

    def test_active_chat_refuses_settings_and_save_resets_connection_evidence(self):
        app = TownApp(self.root / "app-data", bridge=self.bridge)
        try:
            app.provider_connected = True
            app.workers["synthetic"] = threading.current_thread()
            with self.assertRaises(APIError) as caught:
                app.save_model_settings(self.config)
            self.assertEqual(caught.exception.code, 409)
            app.workers.clear()
            app.save_model_settings(self.config)
            self.assertFalse(app.provider_connected)
        finally:
            app.workers.clear()
            app.close()


class LocalHTTPTest(unittest.TestCase):
    def test_redirect_does_not_forward_authorization(self):
        received = []
        class MockHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append((self.path, self.headers.get("Authorization")))
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:%s/leaked" % self.server.server_port)
                self.send_header("Content-Length", "0")
                self.end_headers()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                bridge = ProviderBridge(Path(directory) / "runtime", directory, MemoryCredentials())
                bridge.save_settings({"mode": "api", "base_url": "http://127.0.0.1:%s/v1" % httpd.server_port,
                                      "model": "local-test", "api_key": "synthetic-local-key"})
                try:
                    with self.assertRaisesRegex(RuntimeError, "重定向"):
                        bridge.generate("本机模拟请求", threading.Event())
                finally:
                    bridge.close()
            self.assertEqual(received, [("/v1/chat/completions", "Bearer synthetic-local-key")])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

    def test_settings_http_is_private_and_health_has_no_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ProviderBridge(root / "runtime", root / "data", MemoryCredentials())
            server = create_server(port=0, data_dir=root / "data", bridge=bridge)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, path, data=None):
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                client.request(method, path, body=json.dumps(data).encode() if data else None,
                               headers={"Content-Type": "application/json", "X-Town-Client": "pixel-town"})
                response = client.getresponse()
                result = response.status, json.loads(response.read())
                client.close()
                return result
            try:
                status, health = request("GET", "/api/health")
                self.assertEqual((status, health), (200, {"application": "starhaven-town", "version": VERSION}))
                secret = "synthetic-api-settings-secret"
                status, saved = request("POST", "/api/model-settings", {
                    "mode": "api", "base_url": "https://models.example/v1", "model": "provided-model", "api_key": secret})
                self.assertEqual(status, 200)
                self.assertTrue(saved["has_api_key"])
                self.assertNotIn(secret, json.dumps(saved))
                self.assertNotIn(secret, json.dumps(request("GET", "/api/model-settings")))
                self.assertEqual(request("GET", "/api/state")[1]["version"], VERSION)
                self.assertEqual(request("POST", "/api/model-settings", {"mode": "invalid"})[0], 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
