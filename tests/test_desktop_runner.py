import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from desktop_runner import migrate_legacy, prepare


class MigrationTest(unittest.TestCase):
    def test_online_backup_preserves_wal_and_existing_destination(self):
        kwargs = {"ignore_cleanup_errors": True} if sys.version_info >= (3, 10) else {}
        with tempfile.TemporaryDirectory(**kwargs) as directory:
            root = Path(directory).resolve()
            legacy = root / "old"
            (legacy / "data").mkdir(parents=True)
            data = root / "new"
            original = sqlite3.connect(str(legacy / "data" / "town.sqlite3"))
            copy = None
            try:
                original.execute("PRAGMA journal_mode=WAL")
                original.execute("CREATE TABLE memories (value TEXT)")
                original.execute("INSERT INTO memories VALUES ('保留记忆')")
                original.commit()
                self.assertTrue(migrate_legacy(data, legacy))
                copy = sqlite3.connect(str(data / "town.sqlite3"))
                self.assertEqual(copy.execute("SELECT value FROM memories").fetchone()[0], "保留记忆")
                copy.execute("INSERT INTO memories VALUES ('新记录')")
                copy.commit()
                copy.close()
                copy = None
                self.assertFalse(migrate_legacy(data, legacy))
                self.assertEqual(original.execute("SELECT count(*) FROM memories").fetchone()[0], 1)
            finally:
                if copy is not None:
                    try:
                        copy.close()
                    except Exception:
                        pass
                try:
                    original.close()
                except Exception:
                    pass

    def test_new_install_does_not_read_author_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data, source, reports = prepare(root)
            self.assertEqual(source, root / "team")
            self.assertFalse((root / "settings.json").exists())
            self.assertFalse((root / "town.sqlite3").exists())

    def test_explicit_legacy_link_and_later_settings_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            legacy = root / "team" / "town"
            legacy.mkdir(parents=True)
            (legacy.parent / "config.json").write_text("{}")
            data, source, reports = prepare(root / "data", legacy)
            self.assertEqual(source, legacy.parent)
            custom = root / "custom-team"
            (data / "settings.json").write_text(json.dumps({"source_dir": str(custom)}))
            self.assertEqual(prepare(data, legacy)[1], custom)


if __name__ == "__main__":
    unittest.main()
