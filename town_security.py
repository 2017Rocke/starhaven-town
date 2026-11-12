"""Local, bounded security observations; never executes inspected content.

The append-only hash chain detects application-level edits. It is not a defence
against an administrator who can replace this program and its local database.
"""
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
import uuid

from scout_scan import RULES, EXCLUDED_DIRS, BINARY_SUFFIXES, redact_text, redact_snippet

RULE_VERSION = "1.11.0-r1"
TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
OFFICES = {
    "police": dict(agent_id="resident-officer", name="警察署", description="检查技能、资料和结果中的执行与权限风险线索。"),
    "inspection": dict(agent_id="resident-inspector", name="稽查署", description="检查密钥与外发线索，并记录小镇程序实际接入的网络检查。"),
}
ACTIVE = ("preparing", "scanning", "filing")
LIMITS = dict(files=400, entries=4000, roots=128, file_bytes=262144, total_bytes=8 * 1024 * 1024, findings_per_file=20, seconds=25)
LIMITATIONS = [
    "这是有范围和数量上限的静态线索巡查，不运行源码或技能，不是安全认证，也不证明未发现问题的内容绝对安全。",
    "仅检查星探材料、研究报告，以及居民工作区 skills、books、materials、results；跳过链接、二进制、过大文件及达到上限的内容。",
    "记录由程序生成并只追加，指纹与校验链防止应用内覆盖；不能抵御可以修改程序和数据库的本机管理员。",
    "网络记录只覆盖接入检查的小镇请求；外部浏览器、Codex CLI、其他软件及第三方工具的真实网络活动不在此覆盖范围。",
]
KNOWN_SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN [^-]*PRIVATE KEY-----)")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")
CATEGORIES = {"skills", "books", "materials", "results"}


class SecurityError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def clean(value, limit=500):
    text = redact_text(str(value or ""))
    text = re.sub(r"(https?://[^\s'\"<>?#]+)[?#][^\s'\"<>]*", r"\1?[REDACTED]", text, flags=re.I)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"(?:/(?:Users|home)/[^\s'\"<>]+|/(?:private/)?var/folders/[^\s'\"<>]+|[A-Za-z]:\\Users\\[^\s'\"<>]+)", "[本机路径]", text)
    return text[:limit]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class SecurityOffice:
    def __init__(self, data_dir, report_root=None, *, clock=None, start_scheduler=True):
        self.data_dir = Path(data_dir).resolve()
        self.report_root = Path(report_root).parent.resolve() / Path(report_root).name if report_root else None
        self.root = self.data_dir / "security-office"
        if self.root.is_symlink() or self.root.exists() and linked(self.root):
            raise SecurityError(503, "治安档案目录不能是链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "security.sqlite3"
        for path in (self.db_path, *(Path(str(self.db_path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            if path.is_symlink() or path.exists() and linked(path):
                raise SecurityError(503, "治安档案不能是链接")
        self.clock = clock or (lambda: dt.datetime.now(TZ))
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.workers = {}
        self.scheduler = None
        self.closed = False
        self._initialize()
        if start_scheduler:
            self.scheduler = threading.Thread(target=self._schedule_loop, name="town-security-clock", daemon=True)
            self.scheduler.start()

    @staticmethod
    def ensure_workspace(data_dir, identity):
        """Create only fixed work areas, never follow an existing link/junction."""
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            return False
        base = Path(data_dir).resolve()
        workspace = base / "workspaces"
        resident = workspace / identity
        try:
            for path in (workspace, resident, *(resident / category for category in sorted(CATEGORIES))):
                for parent in (path.parent, *path.parent.parents):
                    if linked(parent):
                        return False
                path.mkdir(exist_ok=True, mode=0o700)
                if linked(path) or not stat.S_ISDIR(path.lstat().st_mode):
                    return False
            return True
        except (OSError, ValueError):
            return False

    def _time(self):
        value = self.clock()
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)

    def _now(self):
        return self._time().isoformat()

    @contextmanager
    def db(self):
        con = sqlite3.connect(str(self.db_path), timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _initialize(self):
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS security_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, office TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS security_records_office ON security_records(office,id);
                CREATE TRIGGER IF NOT EXISTS security_records_no_update BEFORE UPDATE ON security_records
                    BEGIN SELECT RAISE(ABORT,'security records are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS security_records_no_delete BEFORE DELETE ON security_records
                    BEGIN SELECT RAISE(ABORT,'security records are append-only'); END;
                CREATE TABLE IF NOT EXISTS security_jobs (
                    id TEXT PRIMARY KEY, office TEXT NOT NULL, phase TEXT NOT NULL,
                    message TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    files_seen INTEGER NOT NULL DEFAULT 0, files_scanned INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0, findings INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0, coverage TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS security_schedules (
                    office TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    time TEXT NOT NULL DEFAULT '09:30', last_run_date TEXT
                );
            """)
            for office in OFFICES:
                db.execute("INSERT OR IGNORE INTO security_schedules(office,enabled,time) VALUES (?,1,?)",
                           (office, "09:30" if office == "police" else "10:00"))
            interrupted = db.execute("SELECT id,office FROM security_jobs WHERE phase IN ('preparing','scanning','filing')").fetchall()
            db.execute("UPDATE security_jobs SET phase='interrupted',message=?,finished_at=? WHERE phase IN ('preparing','scanning','filing')",
                       ("上次巡查被中断，已落档记录保留；未扫描部分不会标记为完成。", self._now()))
        for job in interrupted:
            try:
                self._append(job["office"], dict(kind="daily_report", job_id=job["id"], status="incomplete",
                    summary="上次巡查中断，已落档部分保留，剩余范围未覆盖。", coverage={"enumeration_complete": False}, findings=[]))
            except SecurityError:
                pass  # Damaged records remain visible, but must not be extended.

    def _append(self, office, payload):
        payload = dict(payload, rule_version=RULE_VERSION)
        if office not in OFFICES:
            raise SecurityError(400, "未知治安工作区")
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._integrity(db)["valid"] is not True:
                raise SecurityError(503, "档案校验未通过，已停止追加记录与网络放行，请保留档案并人工核查。")
            last = db.execute("SELECT record_hash FROM security_records ORDER BY id DESC LIMIT 1").fetchone()
            previous = last[0] if last else "0" * 64
            timestamp = self._now()
            encoded = canonical(payload)
            digest = hashlib.sha256(canonical(dict(office=office, created_at=timestamp, payload=payload, previous_hash=previous)).encode()).hexdigest()
            cursor = db.execute("INSERT INTO security_records(office,created_at,payload,previous_hash,record_hash) VALUES (?,?,?,?,?)",
                                (office, timestamp, encoded, previous, digest))
            return cursor.lastrowid

    def integrity(self):
        with self.db() as db:
            return self._integrity(db)

    @staticmethod
    def _integrity(db):
        rows = db.execute("SELECT * FROM security_records ORDER BY id")
        previous, checked = "0" * 64, 0
        for index, row in enumerate(rows):
            try:
                payload = json.loads(row["payload"])
                expected = hashlib.sha256(canonical(dict(office=row["office"], created_at=row["created_at"], payload=payload, previous_hash=previous)).encode()).hexdigest()
            except (ValueError, TypeError):
                expected = "invalid"
            if row["previous_hash"] != previous or row["record_hash"] != expected:
                return dict(valid=False, records_checked=index, message="档案校验链异常，不能将这些记录视为已核实证据。")
            previous = row["record_hash"]
            checked += 1
        return dict(valid=True, records_checked=checked, message="已核对当前档案校验链；此校验不能抵御本机管理员重写整库。")

    def _safe_path(self, base, relative=""):
        if not isinstance(relative, str):
            raise ValueError("unsafe_path")
        parts = PurePosixPath(relative).parts
        if "\\" in relative or PurePosixPath(relative).is_absolute() or any(p in ("..", ".") for p in parts):
            raise ValueError("unsafe_path")
        path = base
        # Reject symlink/reparse ancestors, including a linked scope directory.
        for parent in [base, *base.parents]:
            if linked(parent):
                raise ValueError("links_or_special_files")
        for part in parts:
            path = path / part
            if linked(path):
                raise ValueError("links_or_special_files")
        return path

    def _read(self, base, relative):
        path = self._safe_path(base, relative)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("links_or_special_files")
        if before.st_size > LIMITS["file_bytes"]:
            raise ValueError("file_too_large")
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("changed_during_scan")
            self._safe_path(base, relative)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read(LIMITS["file_bytes"] + 1)
            after = os.fstat(fd)
            self._safe_path(base, relative)
            current = path.lstat()
            if len(content) > LIMITS["file_bytes"]:
                raise ValueError("file_too_large")
            if ((opened.st_size, opened.st_mtime_ns, opened.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)
                    or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
                raise ValueError("changed_during_scan")
            return content
        finally:
            os.close(fd)

    def _scopes(self, coverage):
        scopes = [("scout-materials", self.data_dir / "scout-workshop" / "materials")]
        if self.report_root:
            scopes.append(("reports", self.report_root))
        workspace = self.data_dir / "workspaces"
        try:
            self._safe_path(workspace)
            with os.scandir(workspace) as entries:
                for index, entry in enumerate(entries):
                    if index >= 100:
                        self._skip(coverage, "workspace_limit"); break
                    if not IDENTITY.fullmatch(entry.name):
                        self._skip(coverage, "unsupported_workspace"); continue
                    candidate = workspace / entry.name
                    if linked(candidate) or not entry.is_dir(follow_symlinks=False):
                        self._skip(coverage, "links_or_special_files"); continue
                    for category in sorted(CATEGORIES):
                        scopes.append(("workspaces/" + entry.name + "/" + category, candidate / category))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self._skip(coverage, "workspace_unavailable")
        if len(scopes) > LIMITS["roots"]:
            self._skip(coverage, "root_limit", len(scopes) - LIMITS["roots"])
            scopes = scopes[:LIMITS["roots"]]
        return scopes

    @staticmethod
    def _skip(coverage, reason, amount=1):
        coverage["skipped"][reason] = coverage["skipped"].get(reason, 0) + amount
        coverage["enumeration_complete"] = False

    def _inventory(self, coverage, deadline):
        files, roots = [], []
        for scope, root in self._scopes(coverage):
            try:
                self._safe_path(root)
                if root.is_dir():
                    with os.scandir(root) as entries:
                        if next(entries, None) is not None:
                            roots.append((scope, root))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                self._skip(coverage, "scope_unavailable")
        # Every existing scope receives a share, so a large source repository
        # cannot permanently crowd skills/books/results out of daily patrols.
        file_share = max(1, LIMITS["files"] // max(1, len(roots)))
        entry_share = max(1, LIMITS["entries"] // max(1, len(roots)))
        coverage["scope_limits"] = dict(scopes=len(roots), files_per_scope=file_share, entries_per_scope=entry_share)
        for scope, root in roots:
            stack, scope_files, entries_seen, exhausted = [root], 0, 0, False
            while stack and not exhausted:
                if self.stop.is_set() or time.monotonic() >= deadline:
                    self._skip(coverage, "interrupted" if self.stop.is_set() else "time_limit")
                    return files
                directory = stack.pop()
                try:
                    self._safe_path(root, directory.relative_to(root).as_posix() if directory != root else "")
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            entries_seen += 1
                            if entries_seen > entry_share:
                                self._skip(coverage, "entry_limit")
                                exhausted = True
                                break
                            path = Path(entry.path)
                            if linked(path):
                                self._skip(coverage, "links_or_special_files")
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in EXCLUDED_DIRS:
                                    self._skip(coverage, "excluded_directory")
                                    continue
                                stack.append(path)
                                continue
                            coverage["files_seen"] += 1
                            if not entry.is_file(follow_symlinks=False):
                                self._skip(coverage, "links_or_special_files")
                                continue
                            if path.suffix.lower() in BINARY_SUFFIXES:
                                self._skip(coverage, "binary_file")
                                continue
                            if scope_files >= file_share:
                                self._skip(coverage, "file_limit")
                                exhausted = True
                                break
                            relative = path.relative_to(root).as_posix()
                            subject = scope + "/" + relative
                            if clean(subject, len(subject) + 1) != subject:
                                self._skip(coverage, "sensitive_path")
                                continue
                            files.append((scope, root, relative))
                            scope_files += 1
                except (OSError, ValueError):
                    self._skip(coverage, "directory_unavailable")
        # Interleave each scope's files before the byte/time-limited read pass.
        buckets = {}
        for item in files:
            buckets.setdefault(item[0], []).append(item)
        return [bucket[index] for index in range(max((len(bucket) for bucket in buckets.values()), default=0))
                for bucket in buckets.values() if index < len(bucket)]

    @staticmethod
    def _findings(office, text):
        findings, suppressed = [], 0
        network = {"credential_paths", "environment_access", "telemetry", "network_io"}
        rules = [r for r in RULES if (r[0] in network) == (office == "inspection")]
        if office == "inspection":
            rules = [("literal_secret", "high", "疑似明文凭据", KNOWN_SECRET, "请人工确认并轮换真实凭据；证据已脱敏。"), *rules]
        else:
            rules = [("instruction_override", "medium", "要求绕过检查的指令文本", re.compile(r"(?:ignore (?:all |previous )?instructions|忽略.{0,8}(?:规则|指令|安全)|绕过.{0,8}(?:检查|审查)|关闭.{0,8}(?:审查|日志))", re.I), "仅作为待复核文字线索；文档中的指令不会被执行，也不能修改审查记录。"), *rules]
        for number, line in enumerate(text.splitlines(), 1):
            for rule, severity, title, pattern, reason in rules:
                if pattern.search(line):
                    if len(findings) >= LIMITS["findings_per_file"]:
                        suppressed += 1; continue
                    findings.append(dict(rule=rule, severity=severity, title=title, line=number,
                        snippet=clean(redact_snippet(line), 240), reason=reason))
        return findings, suppressed

    def _job(self, office):
        with self.db() as db:
            row = db.execute("SELECT * FROM security_jobs WHERE office=? ORDER BY rowid DESC LIMIT 1", (office,)).fetchone()
        if not row:
            return dict(id=None, office=office, phase="idle", message="等待开始巡查。", started_at=None, finished_at=None,
                        files_seen=0, files_scanned=0, total=0, findings=0, skipped=0, coverage={})
        result = dict(row); result["coverage"] = json.loads(result["coverage"])
        return result

    def _update_job(self, identity, **fields):
        allowed = {"phase", "message", "finished_at", "files_seen", "files_scanned", "total", "findings", "skipped", "coverage"}
        if set(fields) - allowed:
            raise ValueError("invalid job update")
        if "coverage" in fields:
            fields["coverage"] = canonical(fields["coverage"])
        with self.lock, self.db() as db:
            db.execute("UPDATE security_jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", tuple(fields.values()) + (identity,))

    def start_patrol(self, data):
        if not isinstance(data, dict) or set(data) != {"office"} or data["office"] not in (*OFFICES, "all"):
            raise SecurityError(400, "请选择警察署、稽查署或两署联合巡查")
        offices = list(OFFICES) if data["office"] == "all" else [data["office"]]
        with self.lock:
            if self.closed:
                raise SecurityError(503, "小镇正在关闭")
            if any(office in self.workers for office in offices):
                raise SecurityError(409, "所选工作区正在巡查，请等待当前任务完成")
            for office in offices:
                identity = uuid.uuid4().hex
                with self.db() as db:
                    db.execute("INSERT INTO security_jobs(id,office,phase,message,started_at) VALUES (?,?,'preparing',?,?)",
                               (identity, office, "正在清点允许巡查的本机资料，不执行任何内容。", self._now()))
                    db.execute("UPDATE security_schedules SET last_run_date=? WHERE office=?", (self._time().date().isoformat(), office))
                worker = threading.Thread(target=self._run, args=(office, identity), name="security-" + office, daemon=True)
                self.workers[office] = worker; worker.start()
        return dict(jobs=[self._job(office) for office in offices])

    def _run(self, office, identity):
        coverage = dict(files_seen=0, files_scanned=0, bytes_scanned=0, skipped={}, enumeration_complete=True,
                        findings_suppressed=0, limits=dict(LIMITS))
        finding_count = 0
        deadline = time.monotonic() + LIMITS["seconds"]
        try:
            files = self._inventory(coverage, deadline)
            self._update_job(identity, phase="scanning", message="正在逐份核对文件指纹与静态风险线索。", total=len(files), files_seen=coverage["files_seen"], coverage=coverage)
            for scope, root, relative in files:
                if self.stop.is_set() or time.monotonic() >= deadline:
                    self._skip(coverage, "interrupted" if self.stop.is_set() else "time_limit"); break
                try:
                    content = self._read(root, relative)
                    if coverage["bytes_scanned"] + len(content) > LIMITS["total_bytes"]:
                        self._skip(coverage, "byte_limit"); break
                    if b"\x00" in content:
                        self._skip(coverage, "binary_content"); continue
                    try:
                        text = content.decode("utf-8")
                    except UnicodeError:
                        self._skip(coverage, "unsupported_encoding"); continue
                    findings, suppressed = self._findings(office, text)
                    coverage["findings_suppressed"] += suppressed
                    if suppressed:
                        coverage["enumeration_complete"] = False
                    digest = hashlib.sha256(content).hexdigest()
                    finding_count += len(findings)
                    coverage["files_scanned"] += 1; coverage["bytes_scanned"] += len(content)
                    self._append(office, dict(kind="file_review", job_id=identity,
                        summary="发现 %d 条待人工复核线索。" % len(findings) if findings else "本次有限规则未发现线索，仍需人工判断。",
                        status="review_required" if findings else "limited_no_findings", subject=clean(scope + "/" + relative, 600),
                        locator=dict(scope=scope, relative=relative), sha256=digest, findings=findings,
                        coverage=dict(bytes_scanned=len(content), findings_suppressed=suppressed, enumeration_complete=not bool(suppressed))))
                except (OSError, ValueError) as exc:
                    reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "file_unavailable"
                    self._skip(coverage, reason)
                self._update_job(identity, files_scanned=coverage["files_scanned"], findings=finding_count,
                                 skipped=sum(coverage["skipped"].values()), coverage=coverage)
            interrupted = self.stop.is_set()
            phase = "interrupted" if interrupted else "partial" if not coverage["enumeration_complete"] or not coverage["files_scanned"] else "completed"
            self._update_job(identity, phase="filing", message="正在追加不可覆盖的巡查记录与本次日报。")
            message = "已静态检查 %d 份文件，发现 %d 条待复核线索；未覆盖部分请查看范围。" % (coverage["files_scanned"], finding_count)
            if not coverage["files_scanned"]:
                message = "本次没有可完成扫描的文件，不出具通过结论。"
            self._append(office, dict(kind="daily_report", job_id=identity, summary=message,
                status="incomplete" if phase != "completed" else "review_required" if finding_count else "limited_no_findings",
                coverage=coverage, findings=[], finding_count=finding_count))
            self._update_job(identity, phase=phase, message=message, finished_at=self._now(), files_scanned=coverage["files_scanned"],
                             findings=finding_count, skipped=sum(coverage["skipped"].values()), coverage=coverage)
        except Exception:
            self._skip(coverage, "internal_error")
            try:
                self._append(office, dict(kind="daily_report", job_id=identity, status="incomplete",
                    summary="巡查因异常未完成；保留已记录部分，未完成范围不视为通过。", coverage=coverage,
                    findings=[], finding_count=finding_count))
            except Exception:
                pass  # Do not manufacture a report when durable storage is unavailable.
            self._update_job(identity, phase="failed", message="巡查未完成；已保存记录保留，未完成内容不视为通过。",
                             finished_at=self._now(), coverage=coverage, skipped=sum(coverage["skipped"].values()))
        finally:
            with self.lock:
                self.workers.pop(office, None)

    def schedule(self, data):
        if (not isinstance(data, dict) or set(data) != {"office", "enabled", "time"} or not isinstance(data["office"], str) or data["office"] not in OFFICES
                or type(data["enabled"]) is not bool or not isinstance(data["time"], str)
                or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", data["time"])):
            raise SecurityError(400, "每日巡查需要工作区、开关和有效的 HH:MM 时间")
        with self.lock, self.db() as db:
            if self.closed:
                raise SecurityError(503, "小镇正在关闭")
            db.execute("UPDATE security_schedules SET enabled=?,time=? WHERE office=?", (int(data["enabled"]), data["time"], data["office"]))
        self.wake.set()
        return dict(offices=[dict(id=office, schedule=self._schedule(office)) for office in OFFICES])

    def _schedule(self, office):
        with self.db() as db:
            result = dict(db.execute("SELECT * FROM security_schedules WHERE office=?", (office,)).fetchone())
        result.pop("office"); result["enabled"] = bool(result["enabled"])
        result.update(timezone="Asia/Shanghai", runs_while_open=True)
        return result

    def maybe_schedule(self):
        launched = []
        with self.lock:
            if self.closed:
                return launched
            current = self._time()
            for office in OFFICES:
                schedule = self._schedule(office)
                if (office not in self.workers and schedule["enabled"] and schedule["last_run_date"] != current.date().isoformat()
                        and current.strftime("%H:%M") >= schedule["time"]):
                    self.start_patrol(dict(office=office)); launched.append(office)
        return launched

    def _schedule_loop(self):
        while not self.stop.is_set():
            try:
                self.maybe_schedule()
            except (OSError, sqlite3.Error, SecurityError):
                pass
            self.wake.wait(30); self.wake.clear()

    def _locator(self, locator):
        if not isinstance(locator, dict) or not isinstance(locator.get("scope"), str):
            raise ValueError("invalid_locator")
        scope, relative = locator.get("scope", ""), locator.get("relative", "")
        if scope == "reports" and self.report_root:
            return self.report_root, relative
        if scope == "scout-materials":
            return self.data_dir / "scout-workshop" / "materials", relative
        parts = scope.split("/")
        if len(parts) == 3 and parts[0] == "workspaces" and IDENTITY.fullmatch(parts[1]) and parts[2] in CATEGORIES:
            return self.data_dir.joinpath(*parts), relative
        raise ValueError("invalid_scope")

    def _record(self, row, *, fresh=True):
        try:
            payload = json.loads(row["payload"])
            if not isinstance(payload, dict):
                raise ValueError("invalid_payload")
        except (TypeError, ValueError):
            payload = dict(kind="invalid_record", status="incomplete", summary="该记录内容损坏，无法作为已核实证据。", findings=[])
        result = dict(payload, id=row["id"], office=row["office"], created_at=row["created_at"],
                      previous_hash=row["previous_hash"], record_hash=row["record_hash"])
        locator = result.pop("locator", None)
        if not isinstance(result.get("findings"), list):
            result["findings"] = []
        result["freshness"] = "not_applicable"
        if locator and result.get("sha256"):
            if not fresh:
                result["freshness"] = "unverified"
            else:
                try:
                    base, relative = self._locator(locator)
                    content = self._read(base, relative)
                    result["freshness"] = "current" if hashlib.sha256(content).hexdigest() == result["sha256"] else "stale"
                    if result["freshness"] == "current" and result.get("rule_version") != RULE_VERSION:
                        result["freshness"] = "rules_changed"
                except FileNotFoundError:
                    result["freshness"] = "missing"
                except (OSError, ValueError):
                    result["freshness"] = "unverified"
        return result

    def records(self, office=None, limit=30, before=None):
        if office not in (*OFFICES, None) or type(limit) is not int or not 1 <= limit <= 50 or before is not None and (type(before) is not int or before < 1):
            raise SecurityError(400, "档案查询参数无效")
        where, parameters = [], []
        if office:
            where.append("office=?"); parameters.append(office)
        if before:
            where.append("id<?"); parameters.append(before)
        with self.db() as db:
            rows = db.execute("SELECT * FROM security_records" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?", tuple(parameters) + (limit + 1,)).fetchall()
        shown = rows[:limit]
        return dict(records=[self._record(row) for row in shown], next_before=shown[-1]["id"] if len(rows) > limit else None,
                    integrity=self.integrity(), limitations=list(LIMITATIONS))

    def workspace(self):
        offices = []
        with self.db() as db:
            for office, info in OFFICES.items():
                rows = db.execute("SELECT * FROM security_records WHERE office=? ORDER BY id DESC LIMIT 50", (office,)).fetchall()
                total = db.execute("SELECT count(*) FROM security_records WHERE office=?", (office,)).fetchone()[0]
                # The latest report must remain available after a busy network day.
                report = db.execute("SELECT * FROM security_records WHERE office=? AND payload LIKE ? ORDER BY id DESC LIMIT 1",
                                    (office, '%"kind":"daily_report"%')).fetchone()
                latest = self._record(report, fresh=False) if report else None
                # Coverage of this summary is explicit; full records are paginated.
                parsed = [self._record(row, fresh=False) for row in rows]
                offices.append(dict(id=office, **info, job=self._job(office), schedule=self._schedule(office), latest_report=latest,
                    counts=dict(records=total, findings=sum(len(item.get("findings", [])) for item in parsed), stale=None,
                                summary_records=len(rows), freshness_checked=False)))
        return dict(offices=offices, integrity=self.integrity(), limitations=list(LIMITATIONS), resident_notice="", server_now=self._now())

    def network_check(self, *, url, method="GET", purpose="github", agent_id="scout", allowed_hosts=(), payload_text=None, allow_http_loopback=False, credential_in_payload=False):
        reason, allowed, host, scheme = "目标通过本次有限外发规则。", True, "", ""
        try:
            parsed = urlsplit(url)
            host, scheme = (parsed.hostname or "").lower(), parsed.scheme.lower()
            port = parsed.port
            loopback = host == "localhost"
            try:
                loopback = loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
            known = {"api.github.com", "codeload.github.com"} if purpose == "github" else set()
            if not isinstance(allowed_hosts, (tuple, list, set, frozenset)):
                raise ValueError("invalid_allowed_hosts")
            known.update(str(item).lower() for item in allowed_hosts)
            if not host or parsed.username or parsed.password or host not in known:
                allowed, reason = False, "目的地不在本次明确配置的允许范围，或地址中含凭据。"
            elif scheme != "https" and not (scheme == "http" and loopback and allow_http_loopback):
                allowed, reason = False, "外发必须使用 HTTPS；仅明确配置的本机回环服务可使用 HTTP。"
            elif purpose == "github" and port not in (None, 443):
                allowed, reason = False, "GitHub 请求端口不在允许范围。"
            elif credential_in_payload:
                allowed, reason = False, "外发文本包含本次模型凭据，已阻止发送；请移除敏感内容。"
            elif payload_text is not None:
                if not isinstance(payload_text, str) or len(payload_text) > 1000000:
                    allowed, reason = False, "外发文本超出本次检查范围，未放行。"
                elif KNOWN_SECRET.search(payload_text) or re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", payload_text):
                    allowed, reason = False, "外发文本包含疑似明文凭据，已阻止发送；请移除敏感内容。"
        except (ValueError, TypeError):
            allowed, reason = False, "请求地址无效，已阻止发送。"
        identity = self._append("inspection", dict(kind="network_check", summary=reason, status="allowed" if allowed else "blocked",
            subject=clean((scheme + "://" + host) if host else "无效目的地", 250), purpose=clean(purpose, 60), agent_id=clean(agent_id, 90),
            method=method if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD") else "OTHER", findings=[],
            coverage=dict(scope="仅目的地、传输协议与显式文本线索；不含授权头、请求正文存档或对方服务行为。")))
        return dict(allowed=allowed, check_id=identity, reason=reason)

    def network_result(self, check, *, ok, status_code=None, error=None):
        identity = check.get("check_id") if isinstance(check, dict) else check
        if type(identity) is not int:
            raise SecurityError(400, "网络检查编号无效")
        with self.db() as db:
            row = db.execute("SELECT payload FROM security_records WHERE id=? AND office='inspection'", (identity,)).fetchone()
        if not row or json.loads(row[0]).get("kind") != "network_check":
            raise SecurityError(400, "没有对应的网络检查记录")
        original = json.loads(row[0])
        if original.get("status") != "allowed":
            raise SecurityError(400, "被阻止的请求不能登记为已观察到的网络请求")
        return self._append("inspection", dict(kind="network_result", check_id=identity, status="observed", findings=[],
            subject=original.get("subject"), agent_id=original.get("agent_id"),
            summary="已观察到请求完成。" if ok else "请求未完成；没有将网络成功视为内容安全结论。",
            ok=bool(ok), status_code=status_code if type(status_code) is int and 100 <= status_code <= 599 else None,
            error="请求发生错误；为避免记录凭据和请求参数，此处不保存原始异常文本。" if error else None))

    def external_notice(self, *, agent_id, purpose="external", message=None):
        return self._append("inspection", dict(kind="external_notice", status="incomplete", findings=[], agent_id=clean(agent_id, 90),
            purpose=clean(purpose, 60), summary=clean(message or "本次调用交给外部工具，无法观察其真实 HTTP 活动；未出具网络放行或安全结论。", 500),
            coverage=dict(network_observed=False)))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True; self.stop.set(); self.wake.set()
            workers = list(self.workers.values())
        for worker in workers + ([self.scheduler] if self.scheduler else []):
            if worker is not threading.current_thread():
                worker.join(timeout=4)
