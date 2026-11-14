"""Both desktop payloads must boot independently with the shipped public defaults."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def build_module(name):
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        spec = importlib.util.spec_from_file_location("starhaven_packaging_" + name, ROOT / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(scripts)


class DesktopPayloadTest(unittest.TestCase):
    def test_macos_and_windows_payloads_boot_with_ten_public_residents(self):
        program = """
import json
from pathlib import Path
import sys
backend = Path(sys.argv[1])
sys.path.insert(0, str(backend))
import scout_journey
import town_roads
from server import TownApp
class OfflineBridge:
    def probe(self):
        return {'available': False, 'authenticated': False, 'status': 'test'}
root = Path(sys.argv[2])
app = TownApp(data_dir=root / 'data', bridge=OfflineBridge(), source_dir=root / 'empty-team',
              web_dir=backend / 'web', report_root=root / 'empty-reports')
try:
    residents = app.state()['agents']
    print(json.dumps({'residents': [{'id': row['id'], 'name': row['name']} for row in residents],
                      'journey_module': str(Path(scout_journey.__file__).parent),
                      'roads_module': str(Path(town_roads.__file__).parent),
                      'road_accesses': len(app.state()['map']['road_network']['accesses']),
                      'daily_enabled': app.scout.settings()['daily_enabled']}, ensure_ascii=False))
finally:
    app.close()
"""
        for builder in ("build_macos", "build_windows"):
            with self.subTest(builder=builder), tempfile.TemporaryDirectory(prefix="starhaven-payload-") as temporary:
                root = Path(temporary)
                backend = root / "desktop" / "backend"
                build_module(builder).copy_backend(backend)
                completed = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-c", program,
                                            str(backend), str(root)], cwd=str(root), capture_output=True,
                                           encoding="utf-8", timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                state = json.loads(completed.stdout)
                residents = state["residents"]
                self.assertEqual(len(residents), 11, residents)
                self.assertTrue({"linghang", "scout", "resident-officer", "resident-inspector"}.issubset({row["id"] for row in residents}), residents)
                self.assertTrue({"吉吉", "东日", "科德", "爬山佬", "杨辉", "阿力哥"}.issubset({row["name"] for row in residents}), residents)
                self.assertEqual(Path(state["journey_module"]), backend)
                self.assertEqual(Path(state["roads_module"]), backend)
                self.assertEqual(state["road_accesses"], 31)
                self.assertFalse(state["daily_enabled"])
                self.assertTrue((backend / "defaults" / "residents.json").is_file())
                self.assertTrue((backend / "web" / "forest-art.js").is_file())
                self.assertTrue((backend / "web" / "scout-journey.js").is_file())
                for asset in ("road-art.js", "road-navigation.js", "assets/police-car-front.svg", "assets/police-car-back.svg"):
                    self.assertTrue((backend / "web" / asset).is_file(), asset)
                self.assertFalse((backend / "data").exists())

    def test_source_export_contains_journey_and_public_defaults(self):
        exporter = build_module("export_source")
        names = {path.relative_to(ROOT).as_posix() for path in exporter.public_files()}
        self.assertTrue({"scout_journey.py", "town_security.py", "town_roads.py", "defaults/residents.json", "web/forest-art.js",
                         "web/road-art.js", "web/road-navigation.js", "web/assets/police-car-front.svg", "web/assets/police-car-back.svg",
                         "web/scout-journey.js", "web/security-workspace.js", "web/assets/officer.svg", "web/assets/inspector.svg", "tests/test_scout_journey.js"}.issubset(names), names)
        self.assertFalse(any(name.startswith(("data/", "build/", "dist/")) for name in names))


if __name__ == "__main__":
    unittest.main()
