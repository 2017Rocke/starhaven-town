"""The local cultivation UI can plan without granting paid execution rights."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from provider_bridge import ProviderBridge
from server import create_server
from test_provider_bridge import MemoryCredentials, MockOpener


class CultivationHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.opener = MockOpener()
        bridge = ProviderBridge(root / "runtime", root / "data", MemoryCredentials(), lambda: self.opener)
        self.server = create_server(0, root / "data", bridge, root / "team",
                                    report_root=root / "reports", start_growth=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            all_headers = {"Content-Type": "application/json", "X-Town-Client": "pixel-town"}
            all_headers.update(headers or {})
            client.request(method, path, None if body is None else json.dumps(body), all_headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_save_and_preview_work_with_training_off_without_native_grant(self):
        status, workspace = self.request("GET", "/api/growth")
        self.assertEqual(status, 200)
        original_policy = workspace["policy"]
        self.assertFalse(original_policy["enabled"])
        self.assertFalse(workspace["native_available"])
        status, saved = self.request("POST", "/api/growth/cultivation", {
            "expected_revision": workspace["cultivation"]["revision"],
            "town_goal": "改善居民之间的需求交接与验收说明",
        })
        self.assertEqual(status, 200, saved)
        revision = saved["revision"]
        status, saved = self.request("POST", "/api/growth/cultivation", {
            "expected_revision": revision, "agent_id": "resident-yanghui", "mode": "custom",
            "long_term": "提升需求澄清与产品验收能力", "focus": "需求变更",
            "success_criteria": "每个变更都标记受影响的验收条件",
        })
        self.assertEqual(status, 200, saved)
        person = next(row for row in saved["residents"] if row["agent_id"] == "resident-yanghui")
        self.assertEqual(person["mode"], "custom")
        self.assertIn("需求变更", person["effective_direction"])
        status, preview = self.request("POST", "/api/growth/cultivation/preview", {"agent_id": "resident-yanghui"})
        self.assertEqual(status, 200, preview)
        self.assertEqual(len(preview["days"]), 7)
        self.assertTrue(any(row["tasks"] for row in preview["days"]))
        status, after = self.request("GET", "/api/growth")
        self.assertEqual(status, 200)
        self.assertEqual(after["policy"], original_policy)
        self.assertEqual(after["cultivation"]["revision"], saved["revision"])
        self.assertEqual(after["tasks"], [])
        self.assertEqual(after["plans"], [])
        self.assertEqual(after["calls"], [])
        self.assertEqual(after["confirmations"], [])
        self.assertEqual(self.opener.requests, [])

    def test_stale_unknown_and_cross_origin_requests_cannot_overwrite_direction(self):
        _, workspace = self.request("GET", "/api/growth")
        revision = workspace["cultivation"]["revision"]
        status, saved = self.request("POST", "/api/growth/cultivation", {
            "expected_revision": revision, "town_goal": "提升岗位专业成果质量",
        })
        self.assertEqual(status, 200, saved)
        for payload, expected in (({
            "expected_revision": revision, "town_goal": "过期页面覆盖",
        }, 409), ({
            "expected_revision": saved["revision"], "town_goal": "扩权请求", "enabled": True,
        }, 400)):
            status, result = self.request("POST", "/api/growth/cultivation", payload)
            self.assertEqual(status, expected, result)
        status, result = self.request("POST", "/api/growth/cultivation", {
            "expected_revision": saved["revision"], "town_goal": "跨站覆盖",
        }, {"Origin": "https://unrelated.example"})
        self.assertEqual(status, 403, result)
        _, after = self.request("GET", "/api/growth")
        self.assertEqual(after["cultivation"], saved)
        self.assertFalse(after["policy"]["enabled"])


if __name__ == "__main__":
    unittest.main()
