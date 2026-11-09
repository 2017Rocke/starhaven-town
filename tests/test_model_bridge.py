import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_bridge import CodexBridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = CodexBridge(Path(self.temp.name))
        self.bridge.executable = "/test/codex"

    def test_probe_never_exposes_login_output(self):
        result = subprocess.CompletedProcess([], 0, "Logged in using an API key - private-credential", "")
        with patch("model_bridge.subprocess.run", return_value=result) as run:
            first = self.bridge.probe()
            second = self.bridge.probe()
        self.assertTrue(first["authenticated"])
        self.assertNotIn("private-credential", str(first))
        self.assertEqual(first, second)
        self.assertEqual(run.call_count, 1)

    def test_disables_configured_mcp_and_capabilities(self):
        features = "\n".join(name + " stable true" for name in self.bridge.DISABLED_FEATURES)
        outcomes = [subprocess.CompletedProcess([], 0, features, ""),
                    subprocess.CompletedProcess([], 0, '[{"name":"test-server"}]', "")]
        with patch("model_bridge.subprocess.run", side_effect=outcomes):
            args = self.bridge._conversation_args()
        self.assertIn('mcp_servers.test-server.enabled=false', args)
        self.assertIn("shell_tool", args)
        self.assertIn("hooks", args)
        self.assertIn("plugins", args)

    def test_fails_closed_when_inventory_is_unavailable(self):
        outcomes = [subprocess.CompletedProcess([], 0, "shell_tool stable true", ""),
                    subprocess.CompletedProcess([], 0, "invalid JSON", "")]
        with patch("model_bridge.subprocess.run", side_effect=outcomes):
            with self.assertRaises(RuntimeError):
                self.bridge._conversation_args()

    def test_cancellation_before_launch_does_not_start_model(self):
        cancel = threading.Event()
        cancel.set()
        with patch("model_bridge.subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeError):
                self.bridge.generate("你好", cancel)
        spawn.assert_not_called()

    def test_close_ends_owned_process_with_large_file_backed_input(self):
        ready = Path(self.temp.name) / "child-ready"
        original_popen = subprocess.Popen
        children = []
        failures = []

        def local_child(command, **kwargs):
            # Exercise the real stdin descriptor and process-group cleanup without
            # invoking Codex or making a network request. This exceeds pipe capacity.
            child = original_popen(
                [sys.executable, "-c",
                 "import sys,time; from pathlib import Path; "
                 "body=sys.stdin.buffer.read(); "
                 "assert body.endswith(('像素'*40000).encode('utf-8')); "
                 "Path(sys.argv[1]).write_text('ready'); time.sleep(60)",
                 str(ready)],
                **kwargs,
            )
            children.append(child)
            return child

        def generate():
            try:
                self.bridge.generate("像素" * 40000, threading.Event())
            except RuntimeError as exc:
                failures.append(exc)

        with patch.object(self.bridge, "_conversation_args", return_value=[]), \
                patch("model_bridge.subprocess.Popen", side_effect=local_child):
            worker = threading.Thread(target=generate, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and worker.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "child did not consume the complete large prompt")
                self.assertEqual(len(children), 1)
                self.assertIsNone(children[0].poll())
                self.bridge.close()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertIsNotNone(children[0].poll())
                self.assertEqual(len(failures), 1)
                self.assertEqual(self.bridge._processes, set())
                self.assertEqual(list(Path(self.temp.name).glob("reply-*")), [])
            finally:
                self.bridge.close()
                worker.join(timeout=5)

    def test_closed_bridge_does_not_start_another_conversation(self):
        self.bridge.close()
        self.bridge.close()  # Closing an idle bridge is idempotent.
        with patch.object(self.bridge, "_conversation_args", return_value=[]), \
                patch("model_bridge.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "对话已停止"):
                self.bridge.generate("你好", threading.Event())
        spawn.assert_not_called()

    def test_network_errors_do_not_echo_private_values(self):
        text = self.bridge._friendly_error("connection failed https://private.example/?token=secret")
        self.assertNotIn("private", text)
        self.assertNotIn("secret", text)
        self.assertIn("连不上", text)


if __name__ == "__main__":
    unittest.main()
