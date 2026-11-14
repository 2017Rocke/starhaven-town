"""Versioned local records shared by the V2 services (standard library only)."""
import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from town_security import linked

TZ = dt.timezone(dt.timedelta(hours=8))


class TownError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def now():
    return dt.datetime.now(TZ).isoformat()


def day():
    return dt.datetime.now(TZ).date().isoformat()


def uid():
    return uuid.uuid4().hex


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else pack(value).encode("utf-8")).hexdigest()


def text(value, label, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit or "\x00" in value:
        raise TownError(400, label + "为空或超出长度限制")
    return value.strip()


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise TownError(400, label + "超出允许范围")
    return value


def backup_before_upgrade(data_dir):
    path = Path(data_dir) / "town.sqlite3"
    for item in (path, *(Path(str(path) + x) for x in ("-wal", "-shm", "-journal"))):
        if item.is_symlink() or item.exists() and linked(item):
            raise TownError(503, "小镇数据库不能是链接")
    if not path.exists():
        return
    with closing(sqlite3.connect(str(path))) as source:
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='v2_meta'").fetchone():
            return
        root = Path(data_dir) / "backups"
        if root.is_symlink() or root.exists() and linked(root):
            raise TownError(503, "备份目录不能是链接")
        root.mkdir(exist_ok=True)
        target = root / ("before-v2-" + uid() + ".sqlite3")
        with closing(sqlite3.connect(str(target))) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise TownError(503, "升级备份校验失败，未执行迁移")


def initialize(db):
    db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS v2_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO v2_meta VALUES ('schema','2');
        CREATE TABLE IF NOT EXISTS v2_events(
            seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS v2_events_no_update BEFORE UPDATE ON v2_events
            BEGIN SELECT RAISE(ABORT,'append only'); END;
        CREATE TRIGGER IF NOT EXISTS v2_events_no_delete BEFORE DELETE ON v2_events
            BEGIN SELECT RAISE(ABORT,'append only'); END;
        CREATE TABLE IF NOT EXISTS v2_artifacts(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES agents(id), category TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
            hash TEXT NOT NULL, bytes INTEGER NOT NULL, parents TEXT NOT NULL, root TEXT NOT NULL,
            demo INTEGER NOT NULL DEFAULT 0, policy TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
            UNIQUE(owner,source_type,source_id,category,kind,hash));
        CREATE TABLE IF NOT EXISTS v2_message_sources(message_id TEXT PRIMARY KEY, sources TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, artifact_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_daily_stats(
            id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, day TEXT NOT NULL, contract_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_trial_evidence(
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
            evidence TEXT NOT NULL, hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS v2_trial_no_update BEFORE UPDATE ON v2_trial_evidence
            BEGIN SELECT RAISE(ABORT,'immutable evaluation'); END;
        CREATE TRIGGER IF NOT EXISTS v2_trial_no_delete BEFORE DELETE ON v2_trial_evidence
            BEGIN SELECT RAISE(ABORT,'immutable evaluation'); END;
        CREATE TABLE IF NOT EXISTS v2_calls(
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, day TEXT NOT NULL, purpose TEXT NOT NULL,
            endpoint TEXT NOT NULL, model TEXT NOT NULL, reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0, usage TEXT, revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, settled_at TEXT);
        CREATE TABLE IF NOT EXISTS v2_intents(
            id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL, state_hash TEXT NOT NULL,
            digest TEXT NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_contracts(
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, requirements TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_acceptances(
            artifact_id TEXT PRIMARY KEY REFERENCES v2_artifacts(id), reviewer TEXT NOT NULL,
            note TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_skills(
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES v2_artifacts(id),
            dependencies TEXT NOT NULL, checks TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v2_plans(
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, day TEXT NOT NULL, role TEXT NOT NULL,
            profile TEXT NOT NULL, goal TEXT NOT NULL, policy_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(agent_id,day));
        CREATE TABLE IF NOT EXISTS v2_tasks(
            id TEXT PRIMARY KEY, plan_id TEXT REFERENCES v2_plans(id), agent_id TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL, rubric TEXT NOT NULL,
            sources TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            artifact_id TEXT, baseline TEXT, evaluation TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS v2_one_running ON v2_tasks(agent_id) WHERE status='running';
        CREATE TABLE IF NOT EXISTS v2_projects(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, assignments TEXT NOT NULL,
            created_at TEXT NOT NULL);
        COMMIT;
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(v2_contracts)")}
    if "effective_day" not in columns:
        db.execute("ALTER TABLE v2_contracts ADD COLUMN effective_day TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE v2_contracts ADD COLUMN weekdays TEXT NOT NULL DEFAULT '[1,2,3,4,5,6,7]'")
    if "published_at" not in {row[1] for row in db.execute("PRAGMA table_info(v2_artifacts)")}:
        db.execute("ALTER TABLE v2_artifacts ADD COLUMN published_at TEXT NOT NULL DEFAULT ''")
    if "policy_revision" not in {row[1] for row in db.execute("PRAGMA table_info(v2_tasks)")}:
        db.execute("ALTER TABLE v2_tasks ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT -1")
        db.execute("UPDATE v2_tasks SET policy_revision=COALESCE((SELECT policy_revision FROM v2_plans WHERE id=v2_tasks.plan_id),-1)")


def event(db, kind, subject, payload):
    # All callers hold a write transaction, so the chain cannot fork.
    previous = db.execute("SELECT hash FROM v2_events ORDER BY seq DESC LIMIT 1").fetchone()
    previous = previous[0] if previous else "0" * 64
    record = [kind, subject, pack(payload), now(), previous]
    db.execute("INSERT INTO v2_events(kind,subject,payload,created_at,previous,hash) VALUES (?,?,?,?,?,?)",
               (*record, digest(record)))


def verify_events(db):
    previous = "0" * 64
    for row in db.execute("SELECT * FROM v2_events ORDER BY seq"):
        record = [row[k] for k in ("kind", "subject", "payload", "created_at", "previous")]
        if row["previous"] != previous or row["hash"] != digest(record):
            return False
        previous = row["hash"]
    return True


def get_setting(db, key, fallback):
    row = db.execute("SELECT value FROM v2_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else fallback


def setting(db, key, value):
    db.execute("INSERT INTO v2_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, pack(value)))
