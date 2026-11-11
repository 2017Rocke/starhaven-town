"""Persistent, local collection desk for public source material.

Downloaded repositories are data: this module never imports or executes them.
Only completed files are indexed, and collected material never becomes a book
without an independently written research report.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from scout_scan import redact_text


LOCAL_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
ACTIVE_PHASES = ("searching", "collecting", "storing")
LIMITATION = "仅静态线索初筛，未运行项目，不构成安全认证；未覆盖全部依赖、发布二进制和服务端行为。"
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}\Z")
COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")
MATERIAL_ID = re.compile(r"[0-9a-f]{32}\Z")


class WorkshopError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value, limit=12000):
    """Display excerpts only; strip markup and redact common credential forms."""
    if not isinstance(value, str):
        return ""
    value = value[:max(limit * 4, 2000)]
    value = redact_text(value)
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)", "[已隐藏私钥]", value)
    value = re.sub(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b", "[已隐藏密钥]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/-]{8,}=*", "Bearer [已隐藏]", value)
    value = re.sub(r'''(?i)((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)["']?\s*[:=]\s*)(["'])([^\r\n]*?)\2''', r"\1[已隐藏]", value)
    value = re.sub(r'''(?i)((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)["']?\s*[:=]\s*)([^\s,;]{4,})''', r"\1[已隐藏]", value)
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[已隐藏]@", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[图片]", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    parser = _PlainText()
    try:
        parser.feed(value)
        value = "".join(parser.parts)
    except (ValueError, AssertionError):
        value = re.sub(r"<[^>]*>", "", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", html.unescape(value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return redact_text(value).strip()[:limit]


def _read_json(path, max_bytes=10_000_000):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _safe_file(root, relative):
    """A stored relative path is still untrusted; never follow a symlink."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        return None
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in (".", "..") for part in parts):
        return None
    path = root
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        for part in parts:
            path = path / part
            if path.is_symlink():
                return None
        path.resolve().relative_to(root.resolve())
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def _source_files(root, limit=20000):
    if not root.is_dir() or root.is_symlink():
        return []
    result = []
    for directory, names, files in os.walk(str(root), followlinks=False):
        names[:] = sorted(name for name in names if name != ".git" and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file() or name == ".starhaven-material.json":
                continue
            result.append(path.relative_to(root).as_posix())
            if len(result) >= limit:
                return result
    return result


def _risk(scan):
    scan = scan if isinstance(scan, dict) else {}
    findings = scan.get("findings") if isinstance(scan.get("findings"), list) else []
    levels = {"high": 3, "medium": 2, "info": 1, "low": 1, "unknown": 0}
    level = scan.get("level")
    if level not in levels:
        level = max((item.get("severity", "unknown") for item in findings if isinstance(item, dict)),
                    key=lambda item: levels.get(item, 0), default="unknown")
    if level == "low":
        level = "info"
    if level not in levels:
        level = "unknown"
    labels = {"high": "有重点复核线索 · 待人工复核", "medium": "有待复核能力线索 · 待人工复核",
              "info": "静态初筛 · 仍需人工复核", "unknown": "扫描覆盖有限 · 待复核"}
    limitations = scan.get("limitations", [])
    if isinstance(limitations, str):
        limitations = [limitations]
    if not isinstance(limitations, list):
        limitations = []
    limitations = [plain_text(item, 600) for item in limitations[:8] if isinstance(item, str)]
    if LIMITATION not in limitations:
        limitations.append(LIMITATION)
    return dict(level=level, label=labels.get(level, labels["unknown"]), findings_count=len(findings), limitations=limitations)


def _counts(value, keys=None):
    """Keep bounded numeric coverage data, never arbitrary collector text."""
    if not isinstance(value, dict):
        return {}
    return {key: number for key, number in list(value.items())[:40]
            if isinstance(key, str) and re.fullmatch(r"[a-z_]{1,60}", key)
            and (keys is None or key in keys) and type(number) is int and 0 <= number <= 10**12}


def _coverage(scan):
    raw = scan.get("coverage", {}) if isinstance(scan, dict) else {}
    if not isinstance(raw, dict):
        return {}
    result = _counts(raw, {"files_seen", "files_scanned", "bytes_scanned", "bytes_read", "findings_matched", "findings_suppressed"})
    for key in ("enumeration_complete", "findings_truncated"):
        if type(raw.get(key)) is bool:
            result[key] = raw[key]
    for key in ("skipped_by_reason", "directories_skipped_by_reason", "limits"):
        result[key] = _counts(raw.get(key))
    return result


class ScoutWorkshop:
    def __init__(self, data_dir, report_root=None, *, discover=None, collect=None, clock=None, start_scheduler=True):
        self.root = Path(data_dir).resolve() / "scout-workshop"
        self.material_root = self.root / "materials"
        self.pending_root = self.root / "pending"
        for directory in (self.root, self.material_root, self.pending_root):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "workspace.sqlite3"
        self.report_root = Path(report_root).resolve() if report_root else None
        self._discover, self._collect = discover, collect
        self.clock = clock or (lambda: dt.datetime.now(LOCAL_TZ))
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        self.scheduler = None
        self.closed = False
        self._availability_cache = {}
        self._initialize()
        self._recover_materials()
        self._import_history()
        if start_scheduler:
            self.scheduler = threading.Thread(target=self._schedule_loop, name="scout-daily-clock", daemon=True)
            self.scheduler.start()

    def _time(self):
        value = self.clock()
        return value.replace(tzinfo=LOCAL_TZ) if value.tzinfo is None else value.astimezone(LOCAL_TZ)

    def _now(self):
        return self._time().isoformat()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(str(self.db_path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_jobs (
                    id TEXT PRIMARY KEY, phase TEXT NOT NULL, message TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT,
                    trigger TEXT NOT NULL, errors TEXT NOT NULL DEFAULT '[]'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_collection_job
                    ON collection_jobs((1)) WHERE phase IN ('searching','collecting','storing');
                CREATE TABLE IF NOT EXISTS seen_repositories (
                    full_name TEXT PRIMARY KEY COLLATE NOCASE, repo_id INTEGER,
                    seen_at TEXT NOT NULL, origin TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS seen_repo_id ON seen_repositories(repo_id);
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY, full_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    repo_id INTEGER, metadata TEXT NOT NULL, storage_kind TEXT NOT NULL,
                    storage_path TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS material_repo_id
                    ON materials(repo_id) WHERE repo_id IS NOT NULL;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(collection_jobs)")}
            if "recent_events" not in columns:
                db.execute("ALTER TABLE collection_jobs ADD COLUMN recent_events TEXT NOT NULL DEFAULT '[]'")
            defaults = {"daily_enabled": False, "daily_time": "09:00", "daily_limit": 3, "last_run_date": None}
            for key, value in defaults.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, json.dumps(value)))
            db.execute("UPDATE collection_jobs SET phase='interrupted',message=?,finished_at=? WHERE phase IN ('searching','collecting','storing')",
                       ("上次采集被中断，已保存的材料仍在；再次出发会跳过历史已收录项目。", self._now()))

    def settings(self):
        with self.db() as db:
            result = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}
        result.update(timezone="Asia/Shanghai", runs_while_open=True)
        return result

    def update_settings(self, data):
        if not isinstance(data, dict) or not data or set(data) - {"daily_enabled", "daily_time", "daily_limit"}:
            raise WorkshopError(400, "每日采集设置无效")
        if "daily_enabled" in data and type(data["daily_enabled"]) is not bool:
            raise WorkshopError(400, "每日采集开关需要是布尔值")
        if "daily_time" in data and (not isinstance(data["daily_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", data["daily_time"])):
            raise WorkshopError(400, "采集时间需要是 00:00 到 23:59")
        if "daily_limit" in data and (type(data["daily_limit"]) is not int or not 1 <= data["daily_limit"] <= 5):
            raise WorkshopError(400, "每次采集数量需要在 1 到 5 之间")
        with self.lock, self.db() as db:
            if self.closed:
                raise WorkshopError(503, "小镇正在关闭")
            for key, value in data.items():
                db.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
        self.wake.set()
        return dict(settings=self.settings())

    def _latest_job(self):
        with self.db() as db:
            row = db.execute("SELECT * FROM collection_jobs ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            return dict(id=None, phase="idle", message="星探在观察站等待出发。", completed=0, total=0,
                        attempted=0, skipped=0, failed=0, started_at=None, finished_at=None, errors=[], recent_events=[])
        result = dict(row)
        result["errors"] = json.loads(result["errors"])
        result["recent_events"] = json.loads(result["recent_events"])
        return result

    def _update_job(self, identity, **values):
        with self.lock, self.db() as db:
            if self.closed:
                return
            allowed = {"phase", "message", "completed", "total", "attempted", "skipped", "failed", "finished_at", "errors"}
            if set(values) - allowed:
                raise ValueError("Unknown collection progress field")
            if "phase" in values:
                row = db.execute("SELECT recent_events,completed FROM collection_jobs WHERE id=?", (identity,)).fetchone()
                if row:
                    events = json.loads(row["recent_events"])
                    events.append(dict(phase=values["phase"], message=values.get("message", ""),
                                       completed=values.get("completed", row["completed"]), at=self._now()))
                    values["recent_events"] = json.dumps(events[-16:], ensure_ascii=False)
            if "errors" in values:
                values["errors"] = json.dumps(values["errors"], ensure_ascii=False)
            db.execute("UPDATE collection_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", tuple(values.values()) + (identity,))

    def _seen(self):
        with self.db() as db:
            rows = db.execute("SELECT full_name,repo_id FROM seen_repositories").fetchall()
        return {row["full_name"].casefold() for row in rows}, {row["repo_id"] for row in rows if row["repo_id"] is not None}

    @staticmethod
    def _seen_candidate(candidate, names, identities):
        return (str(candidate.get("full_name", "")).casefold() in names
                or (candidate.get("repo_id") is not None and candidate.get("repo_id") in identities))

    def start_collect(self, data=None, *, trigger="manual"):
        data = {} if data is None else data
        if not isinstance(data, dict) or set(data) - {"limit"}:
            raise WorkshopError(400, "采集请求只支持本次数量")
        limit = data.get("limit", self.settings()["daily_limit"])
        if type(limit) is not int or not 1 <= limit <= 5:
            raise WorkshopError(400, "本次采集数量需要在 1 到 5 之间")
        with self.lock:
            if self.closed:
                raise WorkshopError(503, "小镇正在关闭")
            if self.worker is not None and self.worker.is_alive():
                raise WorkshopError(409, "星探正在采集，请等待本次出发结束")
            identity, timestamp = uuid.uuid4().hex, self._now()
            with self.db() as db:
                db.execute("INSERT INTO collection_jobs(id,phase,message,total,started_at,trigger) VALUES (?,?,?,?,?,?)",
                           (identity, "searching", "正在核对历史资料，寻找未收录的公开项目。", limit, timestamp, trigger))
                db.execute("UPDATE settings SET value=? WHERE key='last_run_date'", (json.dumps(self._time().date().isoformat()),))
            self._update_job(identity, phase="searching", message="正在核对历史资料，寻找未收录的公开项目。")
            self.worker = threading.Thread(target=self._run_collection, args=(identity, limit), name="scout-collect-" + identity[:8], daemon=True)
            self.worker.start()
        return dict(job=self._latest_job())

    def _progress(self, identity, event):
        if not isinstance(event, dict):
            return
        stage = event.get("stage")
        phase = "searching" if stage == "discover" else "storing" if stage == "scan" else "collecting"
        self._update_job(identity, phase=phase, message=plain_text(event.get("message", "正在收集公开资料。"), 240))

    def _run_collection(self, identity, limit):
        completed = attempted = skipped = failed = 0
        errors = []
        try:
            self._import_history()
            if self.stop.is_set():
                return
            discover, collect = self._discover, self._collect
            if discover is None or collect is None:
                from scout_collect import discover_repositories, collect_repository
                discover = discover or discover_repositories
                collect = collect or collect_repository
            names, identities = self._seen()
            discovered = discover(limit=limit, seen_repo_ids=identities, seen_full_names=names,
                                  cancel_event=self.stop, progress=lambda event: self._progress(identity, event))
            for item in discovered.get("failures", [])[:20]:
                message = item.get("message", item.get("error", "部分来源暂时不可用")) if isinstance(item, dict) else item
                errors.append(dict(project="发现来源", message=plain_text(str(message), 300)))
            if discovered.get("status") == "failed":
                raise WorkshopError(502, "公开项目来源暂时不可用，本次未虚构任何材料；稍后可重试。")
            candidates = discovered.get("candidates", [])
            if not isinstance(candidates, list):
                raise WorkshopError(502, "公开项目来源返回的格式无效")
            reserves = discovered.get("reserve_candidates", [])
            if isinstance(reserves, list):
                candidates = candidates + reserves
            attempted_names, attempted_ids = set(), set()
            for candidate in candidates:
                if self.stop.is_set() or completed >= limit:
                    break
                if not isinstance(candidate, dict):
                    continue
                if self._seen_candidate(candidate, names, identities):
                    skipped += 1
                    self._update_job(identity, skipped=skipped)
                    continue
                if self._seen_candidate(candidate, attempted_names, attempted_ids):
                    continue
                if attempted >= limit * 3:
                    break
                attempted += 1
                attempted_names.add(str(candidate.get("full_name", "")).casefold())
                if candidate.get("repo_id") is not None:
                    attempted_ids.add(candidate["repo_id"])
                pending = self.pending_root / uuid.uuid4().hex
                full_name = str(candidate.get("full_name", ""))
                try:
                    metadata = collect(candidate, pending, cancel_event=self.stop,
                                       progress=lambda event: self._progress(identity, event))
                    if self.stop.is_set():
                        break
                    if self._seen_candidate(metadata, names, identities):
                        skipped += 1
                        continue
                    self._update_job(identity, phase="storing", message="正在将真实源码与出处放入材料架。")
                    if self._publish(pending, metadata, job_id=identity):
                        completed += 1
                        if metadata.get("source_status") != "ok":
                            extraction = metadata.get("extraction", {})
                            excluded = sum(_counts(extraction.get("skipped")).values()) if isinstance(extraction, dict) else 0
                            message = "材料已保存，但部分源码未取回；不能视为完整项目副本。"
                            if excluded:
                                message += "归档中有 %d 项被跳过或未展开。" % excluded
                            errors.append(dict(project=plain_text(full_name, 220), message=message, kind="source_partial"))
                    else:
                        skipped += 1
                    names, identities = self._seen()
                except Exception as exc:
                    if self.stop.is_set():
                        break
                    failed += 1
                    message = getattr(exc, "message", "项目源码或材料保存未完成，未计入材料架。")
                    errors.append(dict(project=plain_text(full_name, 220), message=plain_text(str(message), 500)))
                finally:
                    if pending.exists() and not pending.is_symlink():
                        shutil.rmtree(pending, ignore_errors=True)
                    self._update_job(identity, completed=completed, attempted=attempted, skipped=skipped, failed=failed, errors=errors)
            if self.stop.is_set():
                return
            partial = bool(failed or errors or discovered.get("partial"))
            phase = "partial" if completed and partial else "failed" if partial and not completed else "completed"
            if completed:
                message = "已搬回 %d 份真实材料；静态标注仍需人工复核。" % completed
                if partial:
                    message += "部分来源或源码有缺口，已保存的材料不会丢失；请查看材料覆盖范围。"
            elif partial:
                message = "部分来源或项目未完成，没有新增材料；目前不能确认是否还有新项目，已有材料仍保留。"
            else:
                message = "本次没有发现新的可收录项目，历史重复项目已跳过。"
            self._update_job(identity, phase=phase, message=message, completed=completed, attempted=attempted,
                             skipped=skipped, failed=failed, errors=errors, finished_at=self._now())
        except Exception as exc:
            if not self.stop.is_set():
                message = getattr(exc, "message", "采集暂未完成，请稍后重试；已保存材料仍在。")
                self._update_job(identity, phase="partial" if completed else "failed", message=plain_text(str(message), 500),
                                 completed=completed, attempted=attempted, skipped=skipped, failed=failed,
                                 errors=errors, finished_at=self._now())
        finally:
            with self.lock:
                if self.worker is threading.current_thread():
                    self.worker = None

    def _normalize(self, raw, root, collected_at, *, origin="collection", source_date=None):
        if not isinstance(raw, dict) or not REPOSITORY.fullmatch(str(raw.get("full_name", ""))):
            raise WorkshopError(502, "项目身份信息无效，材料未入库")
        full_name = raw["full_name"]
        sha = raw.get("commit_sha", raw.get("sha", ""))
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
            raise WorkshopError(502, "项目缺少可追溯的固定源码版本，材料未入库")
        sha = sha.lower()
        files = raw.get("files")
        if not isinstance(files, list):
            files = _source_files(root)
        files = sorted(set(path for path in files if isinstance(path, str) and _safe_file(root, path)))
        if not files:
            raise WorkshopError(502, "没有找到已成功保存的真实源码文件，材料未入库")
        readme_path = raw.get("readme_path", "")
        if readme_path not in files:
            readme_path = next((path for path in files if PurePosixPath(path).name.lower() in ("readme.md", "readme.rst", "readme.txt", "readme")), "")
        readme = ""
        path = _safe_file(root, readme_path)
        if path:
            with path.open(encoding="utf-8", errors="replace") as stream:
                readme = plain_text(stream.read(48000), 12000)
        scan = raw.get("scan", {})
        scan = scan if isinstance(scan, dict) else {}
        repo_url = "https://github.com/" + full_name
        risk = _risk(scan)
        evidence = []
        for finding in scan.get("findings", [])[:150] if isinstance(scan.get("findings"), list) else []:
            if not isinstance(finding, dict) or finding.get("path") not in files:
                continue
            line = finding.get("line")
            if type(line) is not int or line < 1:
                continue
            evidence.append(dict(title=plain_text(finding.get("title", "静态线索"), 180),
                                 severity=finding.get("severity", "unknown"), path=finding["path"], line=line,
                                 snippet=plain_text(finding.get("snippet", ""), 800),
                                 reason=plain_text(finding.get("reason", ""), 800),
                                 url=repo_url + "/blob/" + sha + "/" + quote(finding["path"], safe="/") + "#L" + str(line)))
        category = raw.get("category")
        if not isinstance(category, str):
            categories = raw.get("categories")
            category = categories[0] if isinstance(categories, list) and categories and isinstance(categories[0], str) else "待分类"
        owner = full_name.split("/")[0]
        repo_id = raw.get("repo_id")
        repo_id = repo_id if type(repo_id) is int and repo_id > 0 else None
        stars = raw.get("stars")
        stars = stars if type(stars) is int and stars >= 0 else None
        source_status = raw.get("source_status")
        source_status = source_status if source_status in ("ok", "partial") else "partial"
        if len(files) >= 20000:
            source_status = "partial"
            risk["limitations"].append("文件索引达到 20000 项上限，不能据此声称覆盖整个项目。")
        extraction_raw = raw.get("extraction", {})
        extraction_raw = extraction_raw if isinstance(extraction_raw, dict) else {}
        extraction = _counts(extraction_raw, {"file_count", "bytes_written"})
        extraction.update(status=source_status, skipped=_counts(extraction_raw.get("skipped")))
        return dict(full_name=full_name, repo_id=repo_id, description=plain_text(raw.get("description", ""), 1200),
                    category=plain_text(category, 100), stars=stars, language=plain_text(raw.get("language", ""), 80),
                    license=plain_text(raw.get("license", "未确认"), 120), owner=dict(name=owner, url="https://github.com/" + owner),
                    repo_url=repo_url, sha=sha, collected_at=collected_at, source_status=source_status,
                    risk=risk, file_count=len(files), files=files, readme=readme, readme_path=readme_path, evidence=evidence,
                    extraction=extraction, coverage=_coverage(scan),
                    provenance=dict(origin=origin, source_date=source_date, repository=repo_url,
                                    commit=repo_url + "/commit/" + sha,
                                    archive="https://codeload.github.com/" + full_name + "/tar.gz/" + sha,
                                    archive_sha256=raw.get("archive_sha256") if re.fullmatch(r"[0-9a-f]{64}", str(raw.get("archive_sha256", ""))) else None))

    def _insert_material(self, identity, metadata, storage_kind, storage_path):
        with self.db() as db:
            inserted = db.execute("INSERT OR IGNORE INTO materials VALUES (?,?,?,?,?,?,?)", (
                identity, metadata["full_name"], metadata.get("repo_id"), json.dumps(metadata, ensure_ascii=False),
                storage_kind, str(storage_path), metadata["collected_at"])).rowcount == 1
            db.execute("INSERT OR IGNORE INTO seen_repositories VALUES (?,?,?,?)", (
                metadata["full_name"], metadata.get("repo_id"), metadata["collected_at"], metadata["provenance"]["origin"]))
            if inserted and MATERIAL_ID.fullmatch(str(metadata.get("job_id", ""))):
                db.execute("UPDATE collection_jobs SET completed=completed+1 WHERE id=?", (metadata["job_id"],))
        return inserted

    def _publish(self, pending, raw, job_id=None):
        manifest = pending / ".starhaven-material.json"
        if manifest.exists() or manifest.is_symlink():
            raise WorkshopError(502, "项目包含与材料清单冲突的保留文件名；为避免改写真实源码，本项目未入库。")
        metadata = self._normalize(raw, pending, self._now())
        if job_id:
            metadata["job_id"] = job_id
        identity = uuid.uuid4().hex
        destination = self.material_root / identity
        with manifest.open("w", encoding="utf-8") as stream:
            json.dump(dict(id=identity, metadata=metadata), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        with self.lock:
            if self.closed or self.stop.is_set():
                raise WorkshopError(409, "小镇已关闭，本次尚未保存的材料已停止入库")
            os.replace(str(pending), str(destination))
            inserted = self._insert_material(identity, metadata, "local", identity)
            if not inserted:
                shutil.rmtree(destination, ignore_errors=True)
            return inserted

    def _recover_materials(self):
        # A crash after rename but before SQLite commit leaves a complete bundle.
        # Its manifest lets the next process restore the index without re-fetching.
        with self.db() as db:
            known = {row[0] for row in db.execute("SELECT id FROM materials")}
        for directory in self.material_root.iterdir():
            if directory.name in known or not MATERIAL_ID.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
                continue
            saved = _read_json(directory / ".starhaven-material.json")
            metadata = saved.get("metadata")
            if saved.get("id") != directory.name or not isinstance(metadata, dict):
                continue
            if not REPOSITORY.fullmatch(str(metadata.get("full_name", ""))) or not COMMIT.fullmatch(str(metadata.get("sha", ""))):
                continue
            files = metadata.get("files", [])
            if not isinstance(files, list) or not files or not all(_safe_file(directory, path) for path in files):
                continue
            if (not isinstance(metadata.get("collected_at"), str) or
                    not isinstance(metadata.get("provenance"), dict) or
                    metadata["provenance"].get("origin") not in ("collection", "historical-evidence")):
                continue
            self._insert_material(directory.name, metadata, "local", directory.name)

    def _history_days(self):
        if not self.report_root or not self.report_root.is_dir() or self.report_root.is_symlink():
            return []
        return sorted((path for path in self.report_root.iterdir()
                       if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name) and path.is_dir() and not path.is_symlink()), reverse=True)

    def _import_history(self):
        for day in self._history_days():
            if self.stop.is_set():
                return
            evidence = _read_json(day / "evidence.json")
            projects = evidence.get("projects", [])
            if not isinstance(projects, list):
                continue
            for project in projects:
                if not isinstance(project, dict) or not REPOSITORY.fullmatch(str(project.get("full_name", ""))):
                    continue
                name = project["full_name"]
                timestamp = day.name
                recorded_time = evidence.get("generated_at")
                if isinstance(recorded_time, str):
                    try:
                        dt.datetime.fromisoformat(recorded_time.replace("Z", "+00:00"))
                        timestamp = recorded_time
                    except ValueError:
                        pass
                repo_id = project.get("repo_id", project.get("id"))
                repo_id = repo_id if type(repo_id) is int and repo_id > 0 else None
                with self.lock, self.db() as db:
                    if self.closed:
                        return
                    db.execute("INSERT OR IGNORE INTO seen_repositories VALUES (?,?,?,?)", (name, repo_id, timestamp, "historical-evidence"))
                    existing = db.execute("SELECT 1 FROM materials WHERE full_name=? COLLATE NOCASE", (name,)).fetchone()
                if existing:
                    continue
                relative = project.get("source_dir")
                if not isinstance(relative, str) or "\\" in relative:
                    continue
                parts = PurePosixPath(relative).parts
                if len(parts) != 2 or parts[0] != "sources" or parts[1] in (".", ".."):
                    continue
                source = day / relative
                if (day / "sources").is_symlink() or source.is_symlink() or not source.is_dir():
                    continue
                try:
                    source.resolve().relative_to(day.resolve())
                    metadata = self._normalize(project, source, timestamp, origin="historical-evidence", source_date=day.name)
                except (WorkshopError, OSError, ValueError):
                    continue
                identity = hashlib.sha256((name.casefold() + "\n" + str(source)).encode("utf-8")).hexdigest()[:32]
                with self.lock:
                    if not self.closed:
                        self._insert_material(identity, metadata, "history", source)

    def books(self):
        result = []
        for day in self._history_days():
            report, review = day / "report.md", _read_json(day / "review.json")
            projects = review.get("projects")
            if not isinstance(projects, list) or not projects or not _safe_file(day, "report.md"):
                continue
            try:
                with report.open(encoding="utf-8", errors="replace") as stream:
                    excerpt = plain_text(stream.read(5000), 800)
            except OSError:
                continue
            if not excerpt:
                continue
            result.append(dict(id="report-" + day.name, title="星探 · 开源项目观察", date=day.name,
                               summary=excerpt, project_count=len(projects), kind="research_report",
                               url="/reports/" + day.name + "/report.html" if _safe_file(day, "report.html") else None))
        return result

    def _storage_root(self, row):
        if row["storage_kind"] == "local":
            return self.material_root / row["id"] if row["storage_path"] == row["id"] else None
        if row["storage_kind"] == "history" and self.report_root:
            path = Path(row["storage_path"])
            try:
                parts = path.relative_to(self.report_root).parts
                if len(parts) == 3 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) and parts[1] == "sources":
                    if not any((self.report_root.joinpath(*parts[:index])).is_symlink() for index in range(1, 4)):
                        return path
            except ValueError:
                pass
        return None

    def _material(self, row, detailed=False):
        metadata = json.loads(row["metadata"])
        keys = ("full_name", "description", "category", "stars", "language", "license", "owner", "repo_url", "sha",
                "collected_at", "source_status", "risk", "file_count")
        result = {key: metadata.get(key) for key in keys}
        result["id"] = row["id"]
        root = self._storage_root(row)
        files = metadata.get("files", [])
        with self.lock:
            cached = self._availability_cache.get(row["id"])
            # A removed/replaced root is visible immediately; ordinary file
            # availability is cached for short UI polling, and details recheck it.
            if root is None or root.is_symlink() or not root.is_dir():
                cached = (time.monotonic(), 0, self._now())
                self._availability_cache[row["id"]] = cached
            elif detailed or cached is None or time.monotonic() - cached[0] >= 30:
                cached = (time.monotonic(), sum(1 for path in files if _safe_file(root, path)), self._now())
                self._availability_cache[row["id"]] = cached
        result["files_available"] = cached[1]
        result["availability_checked_at"] = cached[2]
        if not files or result["files_available"] != len(files):
            result["source_status"] = "missing" if not result["files_available"] else "partial"
        if detailed:
            # No file-content endpoint: only pre-redacted excerpts and relative names.
            result.update(readme=metadata.get("readme", ""), evidence=metadata.get("evidence", []),
                          files=files, provenance=metadata.get("provenance", {}),
                          extraction=metadata.get("extraction", {}), coverage=metadata.get("coverage", {}))
        return result

    def material_details(self, identity):
        if not isinstance(identity, str) or not MATERIAL_ID.fullmatch(identity):
            raise WorkshopError(404, "没有找到这份材料")
        with self.db() as db:
            row = db.execute("SELECT * FROM materials WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise WorkshopError(404, "没有找到这份材料")
        return dict(material=self._material(row, detailed=True))

    def workspace(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM materials ORDER BY collected_at DESC,rowid DESC").fetchall()
            seen = db.execute("SELECT count(*) FROM seen_repositories").fetchone()[0]
        materials = [self._material(row) for row in rows]
        books = self.books()
        return dict(job=self._latest_job(), materials=materials, books=books, settings=self.settings(),
                    counts=dict(materials=len(materials), raw=sum(item["source_status"] != "missing" for item in materials),
                                books=len(books), risk_review=len(materials), history_seen=seen))

    def chat_summary(self):
        """Use counts and a known phase only; never feed repository content to chat."""
        with self.db() as db:
            count = db.execute("SELECT count(*) FROM materials").fetchone()[0]
        job = self._latest_job()
        return dict(materials=count, research_reports=len(self.books()),
                    last_collection={key: job[key] for key in ("phase", "completed", "total", "started_at", "finished_at")})

    def maybe_schedule(self):
        with self.lock:
            if self.closed or self.worker is not None:
                return False
            settings = self.settings()
            current = self._time()
            today = current.date().isoformat()
            if not settings["daily_enabled"] or settings["last_run_date"] == today or current.strftime("%H:%M") < settings["daily_time"]:
                return False
            # An independently completed historical report already satisfies today.
            if any(book["date"] == today for book in self.books()):
                with self.db() as db:
                    db.execute("UPDATE settings SET value=? WHERE key='last_run_date'", (json.dumps(today),))
                return False
            self.start_collect(trigger="daily")
            return True

    def _schedule_loop(self):
        while not self.stop.is_set():
            try:
                self.maybe_schedule()
            except (OSError, sqlite3.Error, WorkshopError):
                # A transient clock/database issue must not terminate the app.
                pass
            self.wake.wait(30)
            self.wake.clear()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stop.set()
            self.wake.set()
            worker, scheduler = self.worker, self.scheduler
            with self.db() as db:
                db.execute("UPDATE collection_jobs SET phase='interrupted',message=?,finished_at=? WHERE phase IN ('searching','collecting','storing')",
                           ("采集随小镇关闭而暂停，已保存材料仍在；下次出发会跳过已收录项目。", self._now()))
        for thread in (worker, scheduler):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=4)
