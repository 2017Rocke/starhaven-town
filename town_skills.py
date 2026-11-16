"""Reviewed reference skills and versioned production contracts; never executes text."""
import json
import re
import datetime as dt
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from urllib.parse import unquote, urlsplit

from town_security import KNOWN_SECRET
from town_store import TownError, digest, event, integer, now, pack, uid, setting, get_setting, day, TZ
from town_artifacts import KINDS
from town_security import CATEGORIES
from town_security import linked


class Skills:
    def __init__(self, app):
        self.app = app

    def propose(self, data):
        if set(data) - {"artifact_id", "dependencies"}:
            raise TownError(400, "技能只能指定成果版本与依赖版本")
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            artifact = self.app.artifacts.row(db, data.get("artifact_id"))
            content = self.app.artifacts.content(artifact)
            if artifact["category"] != "skills" or artifact["kind"] != "skill":
                raise TownError(400, "请先保存为 skills 分类的 skill 类型成果")
            deps = data.get("dependencies", [])
            if not isinstance(deps, list) or len(deps) > 1 or any(not isinstance(v, str) for v in deps):
                raise TownError(400, "每个技能最多依赖一个明确版本")
            for dep in deps:
                row = self.row(db, dep)
                if row["agent_id"] != artifact["owner"]:
                    raise TownError(400, "依赖技能必须属于同一居民")
            hard, risks = [], []
            if artifact["bytes"] > 8192:
                hard.append("技能文本超过 8 KiB")
            if KNOWN_SECRET.search(content):
                hard.append("技能包含疑似凭据")
            if re.search(r"(?im)^\s*(?:tools|permissions|command|exec|network)\s*:", content):
                hard.append("技能不能声明执行、工具或网络权限")
            if re.search(r"忽略.{0,10}(指令|规则)|ignore.{0,20}instructions|https?://|```(?:sh|bash|powershell)", content, re.I):
                risks.append("包含指令覆盖、链接或命令示例，需人工逐项检查；文本不会执行")
            identity = uid()
            checks = dict(version="2.0-reference-1", hard_errors=hard, risk_flags=risks, hash=artifact["hash"], expires_at=(dt.datetime.now(TZ) + dt.timedelta(days=30)).isoformat())
            db.execute("INSERT INTO v2_skills VALUES (?,?,?,?,?,?,?)", (identity, artifact["owner"], artifact["id"], pack(deps), pack(checks), "draft", now()))
            event(db, "skill_proposed", identity, checks)
            return dict(id=identity, checks=checks)

    @staticmethod
    def row(db, identity):
        row = db.execute("SELECT * FROM v2_skills WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise TownError(404, "技能版本不存在")
        return dict(row)

    def closure(self, db, identities, agent, require_active=True):
        result, visiting = {}, set()
        def visit(identity):
            if identity in visiting:
                raise TownError(409, "技能依赖存在循环")
            if identity in result:
                return
            if len(result) + len(visiting) >= 2:
                raise TownError(409, "每次最多引用两个技能文本，包含依赖")
            visiting.add(identity)
            row = self.row(db, identity)
            if row["agent_id"] != agent or (require_active and row["status"] != "active"):
                raise TownError(403, "技能未启用或不属于当前居民")
            checks = json.loads(row["checks"])
            artifact = self.app.artifacts.row(db, row["artifact_id"])
            if checks["hard_errors"] or checks["hash"] != artifact["hash"] or checks.get("version") != "2.0-reference-1" or checks.get("expires_at", "") <= now():
                raise TownError(403, "技能硬检查未通过")
            self.app.artifacts.require(db, artifact["id"], agent)
            for dep in json.loads(row["dependencies"]):
                visit(dep)
            visiting.remove(identity)
            result[identity] = dict(artifact, skill_id=identity)
        for identity in identities:
            visit(identity)
        if sum(v["bytes"] for v in result.values()) > 8192:
            raise TownError(409, "技能与依赖文本总量超过 8 KiB")
        return list(result.values())

    def references(self, agent):
        with self.app.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM v2_skills WHERE agent_id=? AND status='active' ORDER BY created_at DESC", (agent,))]
            chosen = []
            for identity in ids:
                try:
                    candidates = self.closure(db, chosen + [identity], agent)
                except TownError:
                    continue
                chosen.append(identity)
            return self.closure(db, chosen, agent) if chosen else []

    def disable(self, identity):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.row(db, identity)
            # Disable dependants too; no silent fallback to an older approved version.
            disabled = {identity}
            rows = list(db.execute("SELECT id,dependencies FROM v2_skills WHERE status='active'"))
            while True:
                more = {r["id"] for r in rows if set(json.loads(r["dependencies"])) & disabled}
                if more <= disabled:
                    break
                disabled |= more
            for item in disabled:
                db.execute("UPDATE v2_skills SET status='disabled' WHERE id=?", (item,))
            event(db, "skills_disabled", identity, {"versions": sorted(disabled)})
            setting(db, "access_revision", get_setting(db, "access_revision", 0) + 1)
        return {}

    @staticmethod
    def validate_contract(data):
        if not isinstance(data, list) or not 1 <= len(data) <= 10:
            raise TownError(400, "产出约定需包含 1 至 10 项要求")
        seen = set()
        for item in data:
            if not isinstance(item, dict) or set(item) != {"category", "kind", "count", "min_bytes", "days", "accepted"}:
                raise TownError(400, "产出要求字段不完整")
            if item["category"] not in CATEGORIES or item["kind"] not in KINDS or type(item["accepted"]) is not bool:
                raise TownError(400, "产出分类、类型或验收要求无效")
            integer(item["count"], "数量", 1, 100)
            integer(item["min_bytes"], "单份字节数", 1, 300000)
            integer(item["days"], "统计天数", 1, 90)
            key = (item["category"], item["kind"], item["accepted"])
            if key in seen:
                raise TownError(400, "产出要求的分类、类型与指标不能重复")
            seen.add(key)
        return data

    def contract_status(self, db, agent, on_day=None):
        date = dt.date.fromisoformat(on_day or day())
        contract = db.execute("SELECT * FROM v2_contracts WHERE agent_id=? AND effective_day<=? ORDER BY effective_day DESC, rowid DESC LIMIT 1", (agent, date.isoformat())).fetchone()
        if not contract:
            pending = db.execute("SELECT effective_day FROM v2_contracts WHERE agent_id=? ORDER BY rowid DESC LIMIT 1", (agent,)).fetchone()
            return dict(configured=False, satisfied=False, requirements=[], outcome="not_scheduled", effective_day=pending[0] if pending else None)
        if contract["status"] == "disabled" or date.isoweekday() not in json.loads(contract["weekdays"]):
            return dict(configured=False, id=contract["id"], satisfied=False, requirements=[], outcome="not_scheduled")
        results = []
        for requirement in json.loads(contract["requirements"]):
            since = dt.datetime.combine(date - dt.timedelta(days=requirement["days"] - 1), dt.time(), TZ).isoformat()
            until = dt.datetime.combine(date + dt.timedelta(days=1), dt.time(), TZ).isoformat()
            when = "x.created_at" if requirement["accepted"] else "COALESCE(NULLIF(a.published_at,''),a.created_at)"
            rows = db.execute("SELECT a.*, x.artifact_id AS accepted_id FROM v2_artifacts a LEFT JOIN v2_acceptances x ON a.id=x.artifact_id WHERE a.owner=? AND " + when + ">=? AND " + when + "<? ORDER BY a.created_at", (agent, since, until))
            hashes, evidence, excluded = set(), [], []
            for row in rows:
                reason = ("classification" if row["category"] != requirement["category"] or row["kind"] != requirement["kind"] else
                          "too_short" if row["bytes"] < requirement["min_bytes"] else
                          "demo_or_copy" if row["demo"] or row["source_type"] not in ("assistant", "training", "project") else
                          "not_accepted" if requirement["accepted"] and not row["accepted_id"] else None)
                if not reason:
                    try:
                        self.app.artifacts.content(row)
                    except (TownError, OSError):
                        reason = "integrity"
                if not reason and row["hash"] in hashes:
                    reason = "duplicate_content"
                if reason:
                    excluded.append(dict(id=row["id"], reason=reason))
                    continue
                hashes.add(row["hash"])
                evidence.append(dict(id=row["id"], hash=row["hash"]))
            results.append(dict(requirement, actual=len(hashes), satisfied=len(hashes) >= requirement["count"], evidence=evidence, excluded=excluded))
        satisfied = bool(results) and all(r["satisfied"] for r in results)
        return dict(configured=True, id=contract["id"], day=date.isoformat(), phase="open" if date.isoformat() == day() else "final", satisfied=satisfied, requirements=results,
                    outcome="met" if satisfied else "partial" if any(r["actual"] for r in results) else "no_output")

    def settle_daily(self):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            today = day()
            previous = get_setting(db, "last_business_day", today)
            if today < previous:
                raise TownError(409, "系统日期回退，已暂停自动培养，请核对时钟")
            setting(db, "last_business_day", today)
            agents = [r[0] for r in db.execute("SELECT DISTINCT agent_id FROM v2_contracts")]
            dates = {today, (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()}
            dates.update(r[0] for r in db.execute("SELECT DISTINCT day FROM v2_daily_stats ORDER BY day DESC LIMIT 90"))
            for agent in agents:
                for date in sorted(dates):
                    result = self.contract_status(db, agent, date)
                    if not result.get("id"):
                        continue
                    fingerprint = digest(result)
                    previous = db.execute("SELECT fingerprint FROM v2_daily_stats WHERE agent_id=? AND day=? ORDER BY id DESC LIMIT 1", (agent, date)).fetchone()
                    if previous and previous[0] == fingerprint:
                        continue
                    db.execute("INSERT INTO v2_daily_stats(agent_id,day,contract_id,fingerprint,result,created_at) VALUES (?,?,?,?,?,?)", (agent, date, result["id"], fingerprint, pack(result), now()))
                    event(db, "contract_snapshot", agent, {"day": date, "hash": fingerprint, "contract_id": result["id"]})


LOCAL_SKILL_NAMES = (
    "java-coding-standard", "fortress-amount-logic-audit", "delivery-doc-standard",
    "fortress-selftest-playbook", "test-triage", "backend-production-triage", "starhaven-release",
)
LOCAL_SKILL_MAX_FILE = 2 * 1024 * 1024
LOCAL_SKILL_MAX_BYTES = 16 * 1024 * 1024
LOCAL_SKILL_MAX_FILES = 256
LOCAL_SKILL_CONTEXT_BYTES = 48 * 1024
LOCAL_SKILL_MAX_VERSIONS = 100
LOCAL_SKILL_PRIVATE = re.compile(r"(?i)(?:^|/)(?:\.env(?:\..+)?|[^/]+\.env|credentials(?:\.[^/]+)?|secrets?(?:\.[^/]+)?|id_(?:rsa|ed25519)|[^/]+\.(?:pem|p12|pfx|key|keystore))$")
LOCAL_SKILL_SKIP_DIRS = {".git", ".ssh", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}


class LocalSkills:
    """Immutable local guidance bundles; explicit per-message text, never tools."""
    def __init__(self, app, source_root=None):
        self.app = app
        self.source_root = Path(source_root or Path.home() / ".codex" / "skills").absolute()
        self.workspace_root = Path(app.data_dir).absolute() / "workspaces"
        with app.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS v2_local_skills(
                    id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,name TEXT NOT NULL,
                    bundle_hash TEXT NOT NULL,manifest_hash TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS v2_local_skills_immutable BEFORE UPDATE ON v2_local_skills
                    BEGIN SELECT RAISE(ABORT,'immutable local skill'); END;
                CREATE TABLE IF NOT EXISTS v2_message_local_skills(
                    message_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,skill_ids TEXT NOT NULL,
                    files TEXT NOT NULL DEFAULT '{}',hashes TEXT NOT NULL DEFAULT '{}');
                CREATE TRIGGER IF NOT EXISTS v2_message_local_skills_immutable BEFORE UPDATE ON v2_message_local_skills
                    BEGIN SELECT RAISE(ABORT,'immutable chat skill selection'); END;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(v2_message_local_skills)")}
            for column in ("files", "hashes"):
                if column not in columns:
                    db.execute("ALTER TABLE v2_message_local_skills ADD COLUMN " + column + " TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _safe(path, root):
        path, root = Path(path).absolute(), Path(root).absolute()
        if path != root and root not in path.parents:
            raise TownError(403, "技能资源路径超出允许范围")
        for parent in (path, *path.parents):
            if parent.is_symlink() or parent.exists() and linked(parent):
                raise TownError(503, "技能目录或资源不能包含链接")
        if path.exists() and (not (path.is_file() or path.is_dir()) or path.is_file() and path.stat().st_nlink != 1):
            raise TownError(503, "技能资源必须是独立的普通文件")
        return path

    @staticmethod
    def _relative(value):
        if (not isinstance(value, str) or not value or len(value) > 400 or "\\" in value or ":" in value
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/"))):
            raise TownError(400, "技能资源相对路径无效")
        return value

    def _read_file(self, path, root, limit=LOCAL_SKILL_MAX_FILE):
        path = self._safe(path, root)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise TownError(413, "技能资源文件过大或类型无效")
                value = stream.read(limit + 1)
                if len(value) != info.st_size:
                    raise TownError(409, "技能资源在读取时发生变化")
                return value
        except OSError:
            raise TownError(409, "技能资源不可读取") from None

    def _write_file(self, path, raw):
        self._safe(path, self.workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _agent(self, agent_id):
        if not isinstance(agent_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", agent_id):
            raise TownError(400, "开发居民编号无效")
        with self.app.db() as db:
            agent = self.app._agent(db, agent_id)
        allowed = bool(re.search(r"开发|程序员|软件工程师|后端工程师|前端工程师|研发工程师|\bdeveloper\b|\bsoftware\s+engineer\b", agent.get("role", ""), re.I))
        development = getattr(self.app, "development", None)
        if not allowed and callable(getattr(development, "db", None)):
            with development.db() as db:
                allowed = any(json.loads(row[0]).get("developer") == agent_id for row in db.execute("SELECT members FROM projects"))
        if not allowed:
            raise TownError(403, "本机研发技能书架仅向开发岗位或项目开发居民开放")
        return agent

    @staticmethod
    def _header(raw, name):
        try:
            value = raw.decode("utf-8-sig")
        except UnicodeError:
            raise TownError(400, "技能主文档必须为 UTF-8 文字") from None
        if "\x00" in value:
            raise TownError(400, "技能主文档含二进制内容")
        description = ""
        if value.startswith("---"):
            front = value.split("---", 2)
            if len(front) == 3:
                lines = front[1].splitlines()
                for index, line in enumerate(lines):
                    if line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip("\"'")
                        if description in (">", ">-", "|", "|-"):
                            description = " ".join(part.strip() for part in lines[index + 1:] if part.startswith((" ", "\t")))
                        break
        title = next((line.lstrip("#").strip() for line in value.splitlines() if line.startswith("# ")), name)
        return dict(name=name, title=title[:240], description=description[:2000])

    @staticmethod
    def _private(relative):
        # Templates are safe to preserve; the configured .env is never opened.
        if relative.lower().endswith((".example", ".sample", ".template")):
            return False
        return bool(LOCAL_SKILL_PRIVATE.search(relative))

    def _source(self, name):
        if name not in LOCAL_SKILL_NAMES:
            raise TownError(400, "请选择目录中的研发相关技能")
        root = self._safe(self.source_root / name, self.source_root)
        if not root.is_dir():
            raise TownError(404, "本机没有此技能来源")
        files, excluded, total = {}, [], 0
        stack = [root]
        while stack:
            directory = stack.pop()
            for path in sorted(directory.iterdir()):
                relative = path.relative_to(root).as_posix()
                if self._private(relative) or path.name in LOCAL_SKILL_SKIP_DIRS or path.name == ".DS_Store":
                    excluded.append(relative)
                    continue
                self._relative(relative)
                if relative == "local-manifest.json":
                    raise TownError(400, "技能来源包含保留的登记文件名")
                self._safe(path, self.source_root)
                if path.is_dir():
                    if len(PurePosixPath(relative).parts) > 10:
                        raise TownError(413, "技能资源目录层级过深")
                    stack.append(path)
                    continue
                if len(files) >= LOCAL_SKILL_MAX_FILES:
                    raise TownError(413, "技能资源数量超过 256 个")
                raw = self._read_file(path, self.source_root)
                total += len(raw)
                if total > LOCAL_SKILL_MAX_BYTES:
                    raise TownError(413, "技能资源总量超过 16 MiB")
                if KNOWN_SECRET.search(raw.decode("utf-8", errors="ignore")):
                    excluded.append(relative)
                    continue
                files[relative] = raw
        if "SKILL.md" not in files:
            raise TownError(400, "技能缺少可安全读取的 SKILL.md")
        header = self._header(files["SKILL.md"], name)
        missing = []
        for relative, raw in files.items():
            if not relative.lower().endswith(".md"):
                continue
            value = raw.decode("utf-8", errors="replace")
            references = re.findall(r"\]\(([^)\s]+)(?:\s+[^)]*)?\)", value)
            references += re.findall(r"`((?:references|scripts|assets|profiles)/[^`\s]+)`", value)
            for reference in references:
                target = unquote(reference.split("#", 1)[0])
                parsed = urlsplit(target)
                if not target or parsed.scheme or parsed.netloc:
                    continue
                candidate = Path(os.path.normpath(str(PurePosixPath(relative).parent / target)))
                destination = candidate.as_posix()
                if target.startswith("/") or destination == ".." or destination.startswith("../") or destination not in files:
                    missing.append(dict(file=relative, reference=reference[:400]))
        warnings = []
        if excluded:
            warnings.append("指南可用，环境凭据或私密资源未导入；脚本只保存，不会执行")
        if missing:
            warnings.append("部分引用属于其他技能、目标项目或本机环境，未自动导入；使用前需另行提供相关上下文")
        manifest_files = [dict(path=relative, bytes=len(raw), sha256=digest(raw)) for relative, raw in sorted(files.items())]
        source_hash = digest(dict(name=name, files=manifest_files, excluded=sorted(excluded), missing=missing))
        return dict(header, status="partial_dependencies" if excluded or missing else "imported", files=manifest_files,
                    bytes=sum(len(raw) for raw in files.values()), source_hash=source_hash,
                    excluded=sorted(excluded), missing_references=missing, warnings=warnings), files

    def catalog(self, agent_id):
        self._agent(agent_id)
        result = []
        for name in LOCAL_SKILL_NAMES:
            if not (self.source_root / name).exists():
                continue
            try:
                item, unused = self._source(name)
                result.append({key: item[key] for key in ("name", "title", "description", "source_hash", "bytes", "warnings")}
                              | dict(status="available", import_status=item["status"], reason=""))
            except TownError as error:
                result.append(dict(name=name, title=name, description="", status="blocked", reason=str(error), warnings=[]))
        return result

    def _target(self, agent_id, name, bundle_hash):
        if name not in LOCAL_SKILL_NAMES or not re.fullmatch(r"[a-f0-9]{64}", bundle_hash):
            raise TownError(409, "技能来源或版本编号无效")
        return self._safe(self.workspace_root / agent_id / "local-skills" / name / bundle_hash, self.workspace_root)

    def import_local(self, data):
        if not isinstance(data, dict) or set(data) != {"agent_id", "names"}:
            raise TownError(400, "导入技能需指定开发居民与技能名称")
        agent_id, names = data["agent_id"], data["names"]
        self._agent(agent_id)
        if (not isinstance(names, list) or not names or len(names) > len(LOCAL_SKILL_NAMES)
                or any(not isinstance(name, str) or name not in LOCAL_SKILL_NAMES for name in names) or len(set(names)) != len(names)):
            raise TownError(400, "请选择不重复的本机研发技能")
        result = []
        with self.app.lock:
            for name in names:
                metadata, files = self._source(name)
                fingerprint = metadata["source_hash"]
                with self.app.db() as db:
                    previous = db.execute("SELECT id FROM v2_local_skills WHERE agent_id=? AND name=? AND bundle_hash=?",
                                          (agent_id, name, fingerprint)).fetchone()
                    count = db.execute("SELECT COUNT(*) FROM v2_local_skills WHERE agent_id=?", (agent_id,)).fetchone()[0]
                if previous:
                    result.append(self.read(previous["id"], agent_id))
                    continue
                if count >= LOCAL_SKILL_MAX_VERSIONS:
                    raise TownError(413, "本机技能版本已达 100 个上限")
                identity = uid()
                manifest = dict(metadata, id=identity, agent_id=agent_id, hash=fingerprint, imported_at=now(),
                                source=dict(kind="local-codex-skill", path=str(self.source_root / name), hash=fingerprint))
                encoded = pack(manifest).encode("utf-8")
                target = self._target(agent_id, name, fingerprint)
                pending = target.with_name(fingerprint + ".pending-" + identity)
                self._safe(pending, self.workspace_root)
                if target.exists():
                    raise TownError(409, "此技能版本目录已存在但登记缺失，请核对本机文件")
                pending.mkdir(parents=True, mode=0o700)
                stored = False
                try:
                    for relative, raw in files.items():
                        self._write_file(pending / relative, raw)
                    self._write_file(pending / "local-manifest.json", encoded)
                    self._safe(pending, self.workspace_root)
                    pending.rename(target)
                    with self.app.db() as db:
                        db.execute("INSERT INTO v2_local_skills VALUES (?,?,?,?,?,?)",
                                   (identity, agent_id, name, fingerprint, digest(encoded), manifest["imported_at"]))
                    stored = True
                finally:
                    if not stored:
                        for directory in (pending, target):
                            if directory.exists() and not directory.is_symlink():
                                shutil.rmtree(directory)
                result.append(manifest)
        return result

    def read(self, identity, agent_id):
        self._agent(agent_id)
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise TownError(400, "本机技能版本编号无效")
        with self.app.db() as db:
            row = db.execute("SELECT * FROM v2_local_skills WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise TownError(404, "本机技能版本不存在")
        if row["agent_id"] != agent_id:
            raise TownError(403, "本机技能仅供所属开发居民读取")
        target = self._target(agent_id, row["name"], row["bundle_hash"])
        raw = self._read_file(target / "local-manifest.json", self.workspace_root)
        if digest(raw) != row["manifest_hash"]:
            raise TownError(409, "本机技能登记指纹已变化")
        try:
            value = json.loads(raw)
            if value["id"] != identity or value["agent_id"] != agent_id or value["hash"] != row["bundle_hash"]:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise TownError(409, "本机技能登记结构无效") from None

    def list(self, agent_id):
        self._agent(agent_id)
        with self.app.db() as db:
            identities = [row[0] for row in db.execute("SELECT id FROM v2_local_skills WHERE agent_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100", (agent_id,))]
        return [self.read(identity, agent_id) for identity in identities]

    def workspace(self, agent_id):
        try:
            self._agent(agent_id)
        except TownError as error:
            if error.code == 403:
                return dict(allowed=False, reason=str(error), available=[], installed=[])
            raise
        return dict(allowed=True, reason="", available=self.catalog(agent_id), installed=self.list(agent_id))

    def resolve(self, identities, agent_id, *, files=None):
        if (not isinstance(identities, list) or len(identities) > 3 or any(not isinstance(item, str) for item in identities)
                or len(set(identities)) != len(identities)):
            raise TownError(400, "本次聊天最多选择三个不重复的本机技能")
        if files is not None and (not isinstance(files, dict) or set(files) != set(identities)):
            raise TownError(400, "技能资源选择与技能版本不一致")
        result, total = [], 0
        for identity in identities:
            item = self.read(identity, agent_id)
            selected = ["SKILL.md"] if files is None else files[identity]
            if not isinstance(selected, list) or not 1 <= len(selected) <= 16 or any(not isinstance(path, str) for path in selected) or len(set(selected)) != len(selected):
                raise TownError(400, "每个技能请选择 1 至 16 个不重复文字资源")
            available = {entry["path"]: entry for entry in item["files"]}
            resources = []
            for relative in sorted(selected):
                self._relative(relative)
                if relative not in available:
                    raise TownError(404, "技能中没有此资源或资源属于未导入的私密依赖")
                raw = self._read_file(self._target(agent_id, item["name"], item["hash"]) / relative, self.workspace_root)
                if len(raw) != available[relative]["bytes"] or digest(raw) != available[relative]["sha256"]:
                    raise TownError(409, "技能资源指纹已变化，需重新导入新版本")
                try:
                    content = raw.decode("utf-8-sig")
                except UnicodeError:
                    raise TownError(400, "此技能资源不是可用于聊天的 UTF-8 文字") from None
                if "\x00" in content:
                    raise TownError(400, "二进制技能资源不能加入聊天")
                total += len(raw)
                if total > LOCAL_SKILL_CONTEXT_BYTES:
                    raise TownError(413, "本次技能文字超过 48 KiB，请减少技能或明确选择所需资源")
                resources.append(dict(path=relative, sha256=digest(raw), text=content))
            result.append(dict(id=identity, name=item["name"], title=item["title"], hash=item["hash"],
                               status=item["status"], warnings=item["warnings"], missing_references=item["missing_references"],
                               resources=resources, available_resources=list(available)))
        return result

    @contextmanager
    def _connection(self, db=None):
        if db is not None:
            yield db
        else:
            with self.app.db() as connection:
                yield connection

    def message_selection(self, message_id, agent_id, db=None):
        with self._connection(db) as connection:
            row = connection.execute("SELECT agent_id,skill_ids,files,hashes FROM v2_message_local_skills WHERE message_id=?", (message_id,)).fetchone()
        if row is None:
            return dict(ids=[], files={}, hashes={})
        if row["agent_id"] != agent_id:
            raise TownError(403, "聊天技能选择不属于当前居民")
        return dict(ids=json.loads(row["skill_ids"]), files=json.loads(row["files"]), hashes=json.loads(row["hashes"]))

    def message_ids(self, message_id, agent_id, db=None):
        return self.message_selection(message_id, agent_id, db)["ids"]

    @staticmethod
    def selection(guides):
        return dict(files={item["id"]: [resource["path"] for resource in item["resources"]] for item in guides},
                    hashes={item["id"]: dict(bundle_hash=item["hash"], resources={resource["path"]: resource["sha256"]
                            for resource in item["resources"]}) for item in guides})

    def attach_message(self, message_id, agent_id, identities, db=None, *, files=None):
        selection = self.selection(self.resolve(identities, agent_id, files=files))
        with self._connection(db) as connection:
            message = connection.execute("SELECT agent_id,role FROM messages WHERE id=?", (message_id,)).fetchone()
            if message is None or message["agent_id"] != agent_id or message["role"] != "user":
                raise TownError(403, "技能只能绑定当前居民的明确用户消息")
            previous = connection.execute("SELECT agent_id,skill_ids,files,hashes FROM v2_message_local_skills WHERE message_id=?", (message_id,)).fetchone()
            if previous:
                if (previous["agent_id"] != agent_id or json.loads(previous["skill_ids"]) != identities
                        or json.loads(previous["files"]) != selection["files"] or json.loads(previous["hashes"]) != selection["hashes"]):
                    raise TownError(409, "这条消息已绑定其他技能选择")
            else:
                connection.execute("INSERT INTO v2_message_local_skills VALUES (?,?,?,?,?)",
                                   (message_id, agent_id, pack(identities), pack(selection["files"]), pack(selection["hashes"])))

    def for_message(self, message_id, agent_id):
        saved = self.message_selection(message_id, agent_id)
        guides = self.resolve(saved["ids"], agent_id, files=saved["files"])
        if self.selection(guides)["hashes"] != saved["hashes"]:
            raise TownError(409, "本轮选择的技能资源指纹已变化，请重新选择技能发送")
        return guides
