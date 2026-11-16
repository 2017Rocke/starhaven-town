"""Local development work records, separate from chat and repository execution.

Only this service writes workflow state. Injected runners return drafts, never
acceptance or deployment facts. Public methods are owner-facing; resident model
contexts are built from explicit project role bindings below.
"""
import json
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

from town_security import linked
from town_store import TownError, digest, now, pack, text, uid

ROLES = ("product", "developer", "tester", "commander")
JOB_ROLES = {"analyze": "product", "plan_tests": "tester", "develop": "developer"}
ACTIVE = ("queued", "running")


class DevWorkbench:
    def __init__(self, app, runner=None, start_scheduler=True):
        self.app, self.runner = app, runner
        self.root = Path(app.data_dir) / "dev-workbench"
        self.db_path = self.root / "workbench.sqlite3"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = False
        self.cancel_events = {}
        self.scheduler = None
        self._check_paths()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('schema','1');
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,members TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,
                    revision INTEGER NOT NULL,active_revision INTEGER,status TEXT NOT NULL,
                    run_status TEXT NOT NULL,blocked_reason TEXT NOT NULL,next_owner TEXT NOT NULL,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(
                    task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,
                    requirements TEXT NOT NULL,status TEXT NOT NULL,analysis TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,confirmed_at TEXT,PRIMARY KEY(task_id,revision));
                CREATE TABLE IF NOT EXISTS questions(
                    id TEXT PRIMARY KEY,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
                    text TEXT NOT NULL,blocking INTEGER NOT NULL,answer TEXT,created_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'product',visibility TEXT NOT NULL DEFAULT 'shared',
                    FOREIGN KEY(task_id,revision) REFERENCES revisions(task_id,revision));
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY,task_id TEXT NOT NULL,revision INTEGER NOT NULL,kind TEXT NOT NULL,
                    role TEXT NOT NULL,agent_id TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL,
                    created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
                    FOREIGN KEY(task_id,revision) REFERENCES revisions(task_id,revision));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_kind ON jobs(task_id,revision,kind)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS artifacts(
                    id TEXT PRIMARY KEY,task_id TEXT NOT NULL,revision INTEGER NOT NULL,job_id TEXT NOT NULL,
                    kind TEXT NOT NULL,visibility TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,
                    effective INTEGER NOT NULL,created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id,revision) REFERENCES revisions(task_id,revision));
                CREATE TABLE IF NOT EXISTS discussions(
                    id TEXT PRIMARY KEY,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
                    role TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id,revision) REFERENCES revisions(task_id,revision));
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
                    kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
            """)
            if db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != "1":
                raise TownError(503, "研发工作台数据版本不受支持")
            if 'attachment_ids' not in {row[1] for row in db.execute('PRAGMA table_info(revisions)')}:
                db.execute("ALTER TABLE revisions ADD COLUMN attachment_ids TEXT NOT NULL DEFAULT '[]'")
            if 'interaction_session_id' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN interaction_session_id TEXT')
            db.execute("UPDATE jobs SET status='interrupted',error=?,finished_at=? WHERE status='running'",
                       ("应用退出或重启，执行中断；请核对后重试", now()))
            db.execute("UPDATE tasks SET run_status='interrupted',blocked_reason=? WHERE run_status='running'",
                       ("应用退出或重启，执行中断；请核对后重试",))
        if start_scheduler:
            self.start()

    def start(self):
        """Start once, after callers bind the service on the owning application."""
        with self.lock:
            if self.closed or self.scheduler is not None:
                return False
            self.scheduler = threading.Thread(target=self._loop, name="dev-workbench", daemon=True)
            try:
                self.scheduler.start()
            except Exception:
                self.scheduler = None
                raise
            return True

    def _check_paths(self):
        for path in (Path(self.app.data_dir), self.root, self.db_path, *(Path(str(self.db_path) + x) for x in ("-wal", "-shm", "-journal"))):
            if path.is_symlink() or path.exists() and (linked(path) or (path.is_file() and path.stat().st_nlink > 1)):
                raise TownError(503, "研发工作台目录与数据库不能是链接")
            if path.exists() and path not in (self.root, Path(self.app.data_dir)) and not stat.S_ISREG(path.stat().st_mode):
                raise TownError(503, "研发工作台数据库必须是普通文件")

    @contextmanager
    def db(self):
        with self.lock:
            self._check_paths()
            connection = sqlite3.connect(str(self.db_path), timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _residents(self):
        with self.app.db() as db:
            return [dict(row) for row in db.execute("SELECT id,name FROM agents ORDER BY name,id")]

    def capabilities(self):
        return {"requirements": True, "test_planning": True, "development": self.runner is not None,
                "deployment": False, "acceptance": False}

    def _project(self, db, identity):
        row = db.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone()
        if not row:
            raise TownError(404, "研发项目不存在")
        item = dict(row)
        item["members"] = json.loads(item["members"])
        return item

    def _row(self, db, identity):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (identity,)).fetchone()
        if not row:
            raise TownError(404, "研发需求不存在")
        return dict(row)

    def _summary(self, db, row):
        item = dict(row)
        item["current_role"] = item["next_owner"]
        item["job_counts"] = {r[0]: r[1] for r in db.execute(
            "SELECT status,COUNT(*) FROM jobs WHERE task_id=? GROUP BY status", (item["id"],))}
        return item

    def _task(self, db, identity):
        item = self._summary(db, self._row(db, identity))
        item["revisions"] = [dict(r) for r in db.execute(
            "SELECT * FROM revisions WHERE task_id=? ORDER BY revision DESC", (identity,))]
        for revision in item["revisions"]:
            revision["analysis"] = json.loads(revision["analysis"])
            revision['attachment_ids'] = json.loads(revision['attachment_ids'])
        item["requirements"] = item["revisions"][0]["requirements"]
        item['attachment_ids'] = item['revisions'][0]['attachment_ids']
        for table in ("questions", "jobs", "artifacts", "discussions", "events"):
            item[table] = [dict(r) for r in db.execute(
                "SELECT * FROM " + table + " WHERE task_id=? ORDER BY " + ("id" if table == "events" else "created_at"),
                (identity,))]
        for artifact in item["artifacts"]:
            artifact["content"] = json.loads(artifact["content"])
            artifact["effective"] = bool(artifact["effective"])
        for event in item["events"]:
            event["payload"] = json.loads(event["payload"])
        for question in item["questions"]:
            question["blocking"] = bool(question["blocking"])
        item["members"] = self._project(db, item["project_id"])["members"]
        item["capabilities"] = self.capabilities()
        item['interaction'] = None
        office = getattr(self.app, 'interactions', None)
        if office:
            current_job = next((job for job in reversed(item['jobs']) if job['kind'] == 'analyze' and job['revision'] == item['revision'] and job.get('interaction_session_id')), None)
            if current_job:
                item['interaction'] = office.detail(current_job['interaction_session_id'], current_job['agent_id'])
        return item

    def analysis_current(self, task_id, revision, job_id):
        with self.db() as db:
            task = self._row(db, task_id)
            return (not self.closed and task['status'] != 'cancelled' and task['revision'] == revision and
                db.execute("SELECT 1 FROM jobs WHERE id=? AND task_id=? AND revision=? AND kind='analyze' AND status='running'",
                           (job_id, task_id, revision)).fetchone() is not None)

    def bind_interaction(self, job_id, session_id):
        with self.db() as db:
            changed = db.execute("UPDATE jobs SET interaction_session_id=? WHERE id=? AND kind='analyze' AND status='running'", (session_id, job_id)).rowcount
            if not changed:
                raise TownError(409, '需求分析已停止，不能绑定旧结果')

    def _attachments(self, values, project):
        if not isinstance(values, list) or len(values) > 32 or any(not isinstance(value, str) or not value or len(value) > 200 for value in values):
            raise TownError(400, '需求附件标识无效')
        ids = list(dict.fromkeys(values))
        for identity in ids:
            document = self.app.documents.read(identity, project['members']['product'], project_id=project['id'])
            if document.get('scope', {}).get('classification') != 'requirement' or document.get('scope', {}).get('project_id') != project['id']:
                raise TownError(403, '共享研发需求只可引用已明确归入本项目的需求附件，私人资料不能自动转成团队成果')
        return ids

    def project(self, identity):
        with self.db() as db:
            return self._project(db, identity)

    def is_current(self, task_id, revision, job_id=None):
        with self.db() as db:
            task = self._row(db, task_id)
            if self.closed or task["status"] == "cancelled" or task["active_revision"] != revision:
                return False
            if job_id is None:
                return True
            return db.execute("SELECT 1 FROM jobs WHERE id=? AND task_id=? AND revision=? AND kind='develop' AND status='running'",
                              (job_id, task_id, revision)).fetchone() is not None

    def _applicable(self, db, task_id, revision, job_id, run_id):
        task = self._row(db, task_id)
        if (self.closed or task["status"] == "cancelled" or
                task["active_revision"] != revision or task["revision"] != revision):
            return False
        latest = db.execute("SELECT id,status FROM jobs WHERE task_id=? AND kind='develop' ORDER BY created_at DESC,rowid DESC LIMIT 1", (task_id,)).fetchone()
        if not latest or latest["id"] != job_id or latest["status"] != "completed":
            return False
        artifact = db.execute("SELECT content FROM artifacts WHERE task_id=? AND revision=? AND job_id=? AND kind='development_candidate' AND effective=1",
                              (task_id, revision, job_id)).fetchone()
        if not artifact or not isinstance(run_id, str) or not run_id:
            return False
        content = json.loads(artifact[0])
        if content.get("run_id") != run_id or content.get("status") != "review":
            return False
        return db.execute("SELECT 1 FROM questions WHERE task_id=? AND revision=? AND blocking=1 AND answer IS NULL",
                          (task_id, revision)).fetchone() is None

    def can_apply(self, task_id, revision, job_id, run_id):
        with self.db() as db:
            return self._applicable(db, task_id, revision, job_id, run_id)

    def mark_applied(self, task_id, revision, job_id, run_id):
        """Trusted file-service callback after applying a reviewed, verified change.

        The caller holds workbench.lock across final can_apply, file writes and
        this callback so feedback/new work cannot invalidate a write in flight.
        This records local application only, never a commit or a test result.
        """
        with self.db() as db:
            if not self._applicable(db, task_id, revision, job_id, run_id):
                raise TownError(409, "开发候选或需求已变化，不能记录本次应用")
            existing = db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='development.applied' AND payload=?",
                                  (task_id, pack({"job_id": job_id, "run_id": run_id}))).fetchone()
            if not existing:
                db.execute("UPDATE tasks SET status='awaiting_commit',run_status='completed',blocked_reason=?,next_owner='owner',updated_at=? WHERE id=?",
                           ("本地变更已应用，等待用户检查并自行提交；尚未完成部署与独立验收", now(), task_id))
                self._event(db, task_id, revision, "development.applied", {"job_id": job_id, "run_id": run_id})
            return self._task(db, task_id)

    def task(self, identity):
        """Owner detail, including private developer artifacts. Never a resident context."""
        with self.db() as db:
            return self._task(db, identity)

    def workspace(self):
        residents = self._residents()
        with self.db() as db:
            projects = [self._project(db, r[0]) for r in db.execute("SELECT id FROM projects ORDER BY created_at")]
            tasks = [self._summary(db, r) for r in db.execute("SELECT * FROM tasks ORDER BY updated_at DESC")]
        return {"enabled": bool(projects), "projects": projects, "tasks": tasks,
                "residents": residents, "roles": list(ROLES), "capabilities": self.capabilities()}

    def _request(self, db, data, scope):
        if not isinstance(data, dict):
            raise TownError(400, "请求必须是对象")
        request_id = text(data.get("request_id"), "请求标识", 200)
        fingerprint = digest([scope, data])
        row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise TownError(409, "请求标识已用于其他操作")
            return request_id, fingerprint, json.loads(row["result"])
        return request_id, fingerprint, None

    @staticmethod
    def _remember(db, request_id, fingerprint, result):
        db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, pack(result)))
        return result

    @staticmethod
    def _event(db, task, revision, kind, payload=None):
        db.execute("INSERT INTO events(task_id,revision,kind,payload,created_at) VALUES(?,?,?,?,?)",
                   (task, revision, kind, pack(payload or {}), now()))

    @staticmethod
    def _discuss(db, task, revision, role, kind, content):
        db.execute("INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
                   (uid(), task, revision, role, kind, content, now()))

    def create_project(self, data):
        residents = {r["id"] for r in self._residents()}
        with self.db() as db:
            request_id, fingerprint, result = self._request(db, data, "project")
            if result is not None:
                return result
            title = text(data.get("title"), "项目名称", 300)
            members = data.get("members")
            if not isinstance(members, dict) or set(members) != set(ROLES):
                raise TownError(400, "请明确选择产品、开发、测试与领航居民")
            if any(not isinstance(value, str) or value not in residents for value in members.values()):
                raise TownError(400, "项目成员必须是现有居民的稳定标识")
            if len({members[role] for role in ("product", "developer", "tester")}) != 3:
                raise TownError(400, "产品、开发和测试必须由不同居民担任")
            identity = uid()
            db.execute("INSERT INTO projects VALUES(?,?,?,?)", (identity, title, pack(members), now()))
            return self._remember(db, request_id, fingerprint, self._project(db, identity))

    def create_task(self, data):
        with self.db() as db:
            request_id, fingerprint, result = self._request(db, data, "task")
            if result is not None:
                return result
            project = self._project(db, text(data.get("project_id"), "项目标识", 200))
            title = text(data.get("title"), "需求名称", 300)
            requirements = text(data.get("requirements"), "需求正文", 60000)
            attachments = self._attachments(data.get('attachment_ids', []), project)
            identity, stamp = uid(), now()
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (identity, project["id"], title, 1, None, "draft", "idle", "", "product", stamp, stamp))
            db.execute("INSERT INTO revisions(task_id,revision,requirements,status,created_at,attachment_ids) VALUES(?,?,?,?,?,?)",
                       (identity, 1, requirements, "draft", stamp, pack(attachments)))
            self._event(db, identity, 1, "requirement.created")
            return self._remember(db, request_id, fingerprint, self._task(db, identity))

    def _queue(self, db, task, kind, revision):
        existing = db.execute("SELECT id FROM jobs WHERE task_id=? AND revision=? AND kind=? AND status IN ('queued','running')",
                              (task["id"], revision, kind)).fetchone()
        if existing:
            return existing[0]
        project, role = self._project(db, task["project_id"]), JOB_ROLES[kind]
        if project["members"][role] not in {r["id"] for r in self._residents()}:
            raise TownError(409, "该职责绑定的居民已不存在，请重新配置项目")
        identity = uid()
        if kind == "develop":
            db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND kind='development_candidate'", (task["id"],))
            db.execute("UPDATE tasks SET status='developing' WHERE id=?", (task["id"],))
        db.execute("INSERT INTO jobs(id,task_id,revision,kind,role,agent_id,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (identity, task["id"], revision, kind, role, project["members"][role], "queued", "", now()))
        db.execute("UPDATE tasks SET run_status='queued',blocked_reason='',next_owner=?,updated_at=? WHERE id=?",
                   (role, now(), task["id"]))
        self._event(db, task["id"], revision, "job.queued", {"job_id": identity, "role": role, "kind": kind})
        self.wake.set()
        return identity

    def action(self, identity, data):
        with self.db() as db:
            request_id, fingerprint, result = self._request(db, data, "action:" + identity)
            if result is not None:
                return result
            task = self._row(db, identity)
            revision = data.get("revision")
            if type(revision) is not int or revision != task["revision"]:
                raise TownError(409, "需求版本已变化，请刷新后重试；当前草稿不会被覆盖")
            action = data.get("action")
            if task["status"] == "cancelled":
                raise TownError(409, "需求已取消，不能继续执行")
            if action == "revise":
                requirements = text(data.get("requirements"), "需求正文", 60000)
                previous_attachments = json.loads(db.execute('SELECT attachment_ids FROM revisions WHERE task_id=? AND revision=?', (identity, revision)).fetchone()[0])
                attachments = self._attachments(data.get('attachment_ids', previous_attachments), self._project(db, task['project_id']))
                revision += 1
                db.execute("INSERT INTO revisions(task_id,revision,requirements,status,created_at,attachment_ids) VALUES(?,?,?,?,?,?)",
                           (identity, revision, requirements, "draft", now(), pack(attachments)))
                db.execute("UPDATE revisions SET status='superseded' WHERE task_id=? AND revision=? AND status!='active'",
                           (identity, task["revision"]))
                db.execute("UPDATE tasks SET revision=?,updated_at=? WHERE id=?", (revision, now(), identity))
                self._invalidate(db, identity, "kind='analyze'", "新草稿取代旧需求分析")
                if task["active_revision"] is None:
                    db.execute("UPDATE tasks SET status='draft',run_status='idle',blocked_reason='',next_owner='product' WHERE id=?", (identity,))
                self._event(db, identity, revision, "requirement.revised")
            elif action == "answer":
                answer = text(data.get("answer"), "澄清答复", 20000)
                question = db.execute("SELECT * FROM questions WHERE id=? AND task_id=? AND revision=?",
                                      (data.get("question_id"), identity, revision)).fetchone()
                if not question:
                    raise TownError(404, "当前需求版本中不存在该问题")
                if question["answer"]:
                    raise TownError(409, "问题已回答；修改业务规则请创建新需求版本")
                db.execute("UPDATE questions SET answer=? WHERE id=?", (answer, question["id"]))
                self._discuss(db, identity, revision, "owner", "answer", answer)
                self._event(db, identity, revision, "question.answered", {"question_id": question["id"]})
                analysis = json.loads(db.execute('SELECT analysis FROM revisions WHERE task_id=? AND revision=?', (identity, revision)).fetchone()[0])
                if question['role'] == 'product' and analysis.get('interaction_session_id'):
                    self._invalidate(db, identity, "kind='analyze'", '新增澄清答复，需要重新核对分析')
                    db.execute("UPDATE revisions SET status='draft' WHERE task_id=? AND revision=?", (identity, revision))
                    db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND revision=? AND kind='requirement_analysis'", (identity, revision))
                    self._queue(db, task, 'analyze', revision)
            elif action == "confirm":
                draft = db.execute("SELECT * FROM revisions WHERE task_id=? AND revision=?", (identity, revision)).fetchone()
                if draft["status"] != "review":
                    raise TownError(409, "请先完成当前草稿的需求分析，再确认生效")
                if db.execute("SELECT 1 FROM questions WHERE task_id=? AND revision=? AND blocking=1 AND answer IS NULL",
                              (identity, revision)).fetchone():
                    raise TownError(409, "仍有必须回答的澄清问题")
                self._invalidate(db, identity, "revision!=" + str(revision), "新的生效需求版本已取代旧作业")
                db.execute("UPDATE revisions SET status='superseded' WHERE task_id=? AND status='active'", (identity,))
                db.execute("UPDATE revisions SET status='active',confirmed_at=? WHERE task_id=? AND revision=?", (now(), identity, revision))
                db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND revision!=?", (identity, revision))
                db.execute("UPDATE tasks SET active_revision=?,status='developing',run_status='blocked',blocked_reason=?,next_owner='developer' WHERE id=?",
                           (revision, "需求已确认，等待显式开始开发；部署和独立验收尚未接入", identity))
                self._event(db, identity, revision, "requirement.activated", {"recipients": ["developer", "tester"]})
            elif action in JOB_ROLES:
                selected = revision if action == "analyze" else task["active_revision"]
                if not selected:
                    raise TownError(409, "请先确认生效需求")
                if action == "analyze":
                    state = db.execute("SELECT status FROM revisions WHERE task_id=? AND revision=?", (identity, selected)).fetchone()[0]
                    if state in ("active", "review"):
                        raise TownError(409, "当前需求已完成分析，请回答澄清后确认，或创建新草稿")
                if action == "develop":
                    if task["active_revision"] != revision:
                        raise TownError(409, "请先确认最新需求草稿，再开始新的开发作业")
                    if db.execute("SELECT 1 FROM questions WHERE task_id=? AND revision=? AND blocking=1 AND answer IS NULL", (identity, selected)).fetchone():
                        raise TownError(409, "仍有必须回答的澄清问题")
                self._queue(db, task, action, selected)
            elif action == "feedback":
                content = text(data.get("content"), "返工反馈", 20000)
                if not task["active_revision"]:
                    raise TownError(409, "请先确认生效需求")
                self._discuss(db, identity, task["active_revision"], "owner", "feedback", content)
                self._invalidate(db, identity, "kind='develop'", "新增用户反馈，旧开发作业停止生效")
                db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND kind='development_candidate'", (identity,))
                db.execute("UPDATE tasks SET status='rework_required',run_status='blocked',blocked_reason=?,next_owner='developer' WHERE id=?",
                           ("用户提出返工反馈，等待重新开发；尚无独立验收结论", identity))
                self._event(db, identity, task["active_revision"], "rework.requested")
            elif action == "cancel":
                self._invalidate(db, identity, "1=1", "用户取消需求", status="cancelled")
                db.execute("UPDATE tasks SET status='cancelled',run_status='cancelled',blocked_reason='',next_owner='owner' WHERE id=?", (identity,))
                self._event(db, identity, revision, "task.cancelled")
            elif action == 'pause':
                jobs = db.execute("SELECT * FROM jobs WHERE task_id=? AND revision=? AND kind='analyze' AND status IN ('queued','running')", (identity, revision)).fetchall()
                if not jobs:
                    raise TownError(409, '当前没有正在进行的需求分析')
                for job in jobs:
                    cancel = self.cancel_events.get(job['id'])
                    if cancel:
                        cancel.set()
                    if job['interaction_session_id'] and getattr(self.app, 'interactions', None):
                        self.app.interactions.pause_managed(job['interaction_session_id'])
                    db.execute("UPDATE jobs SET status='paused',error=? WHERE id=?", ('镇长已暂停，已完成子步骤保留', job['id']))
                db.execute("UPDATE tasks SET run_status='paused',blocked_reason=?,updated_at=? WHERE id=?", ('镇长已暂停需求分析，可继续未完成步骤', now(), identity))
                self._event(db, identity, revision, 'analysis.paused')
            elif action == 'resume':
                job = db.execute("SELECT * FROM jobs WHERE task_id=? AND revision=? AND kind='analyze' AND status IN ('paused','interrupted','failed','blocked') ORDER BY created_at DESC,rowid DESC LIMIT 1", (identity, revision)).fetchone()
                if not job:
                    raise TownError(409, '没有可继续的当前需求分析')
                self._queue(db, task, 'analyze', revision)
            elif action == "retry":
                job = db.execute("SELECT * FROM jobs WHERE id=? AND task_id=?", (data.get("job_id"), identity)).fetchone()
                if not job or job["status"] not in ("failed", "interrupted", "blocked"):
                    raise TownError(409, "仅可重试当前需求的失败、中断或阻塞作业")
                expected = revision if job["kind"] == "analyze" else task["active_revision"]
                if job["revision"] != expected:
                    raise TownError(409, "旧需求版本作业不能重试")
                self._queue(db, task, job["kind"], job["revision"])
            else:
                raise TownError(400, "不支持的研发动作；聊天文本不能修改执行或验收状态")
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), identity))
            return self._remember(db, request_id, fingerprint, self._task(db, identity))

    def _invalidate(self, db, identity, condition, reason, status="superseded"):
        jobs = db.execute("SELECT id,interaction_session_id FROM jobs WHERE task_id=? AND status IN ('queued','running','paused') AND " + condition, (identity,)).fetchall()
        for row in jobs:
            cancel = self.cancel_events.get(row[0])
            if cancel:
                cancel.set()
            if row['interaction_session_id'] and getattr(self.app, 'interactions', None):
                self.app.interactions.pause_managed(row['interaction_session_id'])
        db.execute("UPDATE jobs SET status=?,error=?,finished_at=? WHERE task_id=? AND status IN ('queued','running','paused') AND " + condition,
                   (status, reason, now(), identity))

    def revoke_source(self, project_id):
        """Stop source work while keeping requirements and historical evidence."""
        reason = "镇长已断开源码授权；请重新连接后显式开始新一批开发"
        with self.db() as db:
            self._project(db, project_id)
            for task in db.execute("SELECT * FROM tasks WHERE project_id=?", (project_id,)).fetchall():
                active = db.execute("SELECT 1 FROM jobs WHERE task_id=? AND kind='develop' AND status IN ('queued','running')", (task['id'],)).fetchone()
                self._invalidate(db, task['id'], "kind='develop'", reason, status="cancelled")
                db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND kind='development_candidate'", (task['id'],))
                if active or task['status'] in ('developing', 'review', 'rework_required'):
                    db.execute("UPDATE tasks SET run_status='blocked',blocked_reason=?,next_owner='owner',updated_at=? WHERE id=?",
                               (reason, now(), task['id']))
                self._event(db, task['id'], task['revision'], "source.disconnected")

    def _context(self, db, job):
        task = self._row(db, job["task_id"])
        revision = db.execute("SELECT * FROM revisions WHERE task_id=? AND revision=?", (task["id"], job["revision"])).fetchone()
        context = {"job_id": job["id"], "project_id": task["project_id"], "task_id": task["id"],
                   "revision": job["revision"], "role": job["role"], "agent_id": job["agent_id"],
                   "title": task["title"], "requirements": revision["requirements"],
                   'attachment_ids': json.loads(revision['attachment_ids']),
                   "analysis": json.loads(revision["analysis"]),
                   "answers": [{"question": r["text"], "answer": r["answer"]} for r in db.execute(
                       "SELECT text,answer FROM questions WHERE task_id=? AND revision=? AND answer IS NOT NULL AND (visibility='shared' OR role=?)", (task["id"], job["revision"], job["role"]))],
                   "shared_artifacts": [{"kind": r["kind"], "content": json.loads(r["content"])} for r in db.execute(
                       "SELECT kind,content FROM artifacts WHERE task_id=? AND revision=? AND visibility='shared' AND effective=1", (task["id"], job["revision"]))]}
        if job["role"] == "developer":
            context["private_artifacts"] = [{"kind": r["kind"], "content": json.loads(r["content"])} for r in db.execute(
                "SELECT kind,content FROM artifacts WHERE task_id=? AND revision=? AND visibility='private'", (task["id"], job["revision"]))]
            context["feedback"] = [r[0] for r in db.execute(
                "SELECT content FROM discussions WHERE task_id=? AND revision=? AND kind='feedback'", (task["id"], job["revision"]))]
        return context

    def role_context(self, identity, role, agent_id):
        """Internal resident projection; identity must match a saved role binding."""
        with self.db() as db:
            task = self._row(db, identity)
            members = self._project(db, task["project_id"])["members"]
            if role not in ROLES or members[role] != agent_id:
                raise TownError(403, "居民没有该项目职责权限")
            if role == "commander":
                summary = self._summary(db, task)
                # No free-form errors, discussion, source candidates, or requirements.
                return {key: summary[key] for key in ("id", "project_id", "revision", "active_revision", "status", "run_status", "next_owner", "job_counts")}
            kind = next(kind for kind, bound_role in JOB_ROLES.items() if bound_role == role)
            revision = task["revision"] if role == "product" else task["active_revision"] or task["revision"]
            return self._context(db, {"id": None, "task_id": identity, "revision": revision,
                                      "role": role, "agent_id": agent_id, "kind": kind})

    def _loop(self):
        while not self.closed:
            self.wake.clear()
            if not self.run_once():
                self.wake.wait(30)

    def run_once(self):
        """Claim one durable job; useful for deterministic tests without real models."""
        with self.db() as db:
            if self.closed:
                return False
            job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not job:
                return False
            job = dict(job)
            cancel = threading.Event()
            self.cancel_events[job["id"]] = cancel
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (now(), job["id"]))
            db.execute("UPDATE tasks SET run_status='running',next_owner=?,updated_at=? WHERE id=?", (job["role"], now(), job["task_id"]))
            context = self._context(db, job)
        try:
            result = self.runner(job["kind"], context, cancel) if self.runner else {
                "status": "blocked", "reason": "尚未配置研发模型或受控执行连接器"}
            self._finish(job, result)
        except Exception as exc:
            self._fail(job, exc)
        finally:
            with self.lock:
                self.cancel_events.pop(job["id"], None)
        return True

    @staticmethod
    def _validate_result(kind, result):
        if not isinstance(result, dict) or len(pack(result).encode("utf-8")) > 256000:
            raise TownError(400, "作业结果不是有效的结构化内容或过大")
        if result.get("status") == "blocked":
            return {"status": "blocked", "reason": text(result.get("reason"), "阻塞原因", 3000)}
        if kind == "develop":
            return result  # Private candidate only: model prose grants no workflow fact.
        summary = text(result.get("summary"), "作业摘要", 20000)
        if kind == "analyze":
            questions = result.get("questions", [])
            if not isinstance(questions, list) or len(questions) > 50:
                raise TownError(400, "澄清问题格式无效")
            normalized = []
            for question in questions:
                if isinstance(question, str):
                    question = {"text": question}
                if not isinstance(question, dict) or type(question.get("blocking", True)) is not bool:
                    raise TownError(400, "澄清问题格式无效")
                normalized.append({"text": text(question.get("text"), "澄清问题", 6000), "blocking": question.get("blocking", True)})
            clean = {"summary": summary, "questions": normalized}
            for name in ("scope", "acceptance_criteria"):
                values = result.get(name, [])
                if not isinstance(values, list) or len(values) > 100:
                    raise TownError(400, "需求分析条目格式无效")
                clean[name] = [text(value, "需求分析条目", 6000) for value in values]
            for name in ('facts', 'inferences', 'requirement_items', 'evidence'):
                if name in result:
                    values = result[name]
                    if not isinstance(values, list) or len(values) > 512:
                        raise TownError(400, '分析来源记录格式无效')
                    clean[name] = values
            for name in ('interaction_session_id', 'call_ids'):
                if name in result:
                    clean[name] = result[name]
            return clean
        cases = result.get("cases")
        if not isinstance(cases, list) or not cases or len(cases) > 100:
            raise TownError(400, "测试计划需包含有效用例")
        clean = []
        for case in cases:
            if not isinstance(case, dict):
                raise TownError(400, "测试用例格式无效")
            steps = case.get("steps")
            if isinstance(steps, str):
                steps = [steps]
            if not isinstance(steps, list) or not steps or len(steps) > 100:
                raise TownError(400, "测试步骤格式无效")
            clean.append({"title": text(case.get("title"), "用例名称", 1000),
                          "steps": [text(step, "测试步骤", 6000) for step in steps],
                          "expected": text(case.get("expected"), "预期结果", 6000),
                          "priority": text(case.get("priority", "normal"), "用例优先级", 100)})
        return {"summary": summary, "cases": clean, "execution_status": "not_run"}

    def _finish(self, job, result):
        result = self._validate_result(job["kind"], result)
        with self.db() as db:
            current = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            task = self._row(db, job["task_id"])
            expected = task["revision"] if job["kind"] == "analyze" else task["active_revision"]
            if self.closed or current != "running" or task["status"] == "cancelled" or job["revision"] != expected:
                return
            if result.get("status") == "blocked":
                db.execute("UPDATE jobs SET status='blocked',error=?,finished_at=? WHERE id=?", (result["reason"], now(), job["id"]))
                # Developer errors can contain private paths, so task projections use a fixed reason.
                reason = "受控开发执行受阻，请在用户详情查看作业记录" if job["role"] == "developer" else "作业受阻，请在用户详情查看原因"
                db.execute("UPDATE tasks SET run_status='blocked',blocked_reason=?,updated_at=? WHERE id=?", (reason, now(), task["id"]))
                self._event(db, task["id"], job["revision"], "job.blocked", {"job_id": job["id"]})
                return
            kind = {"analyze": "requirement_analysis", "plan_tests": "test_plan", "develop": "development_candidate"}[job["kind"]]
            visibility = "private" if job["role"] == "developer" else "shared"
            db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (uid(), task["id"], job["revision"], job["id"], kind, visibility, pack(result), digest(result), 1, now()))
            db.execute("UPDATE jobs SET status='completed',finished_at=? WHERE id=?", (now(), job["id"]))
            if job["kind"] == "analyze":
                db.execute("DELETE FROM questions WHERE task_id=? AND revision=? AND role='product' AND answer IS NULL", (task['id'], job['revision']))
                db.execute("UPDATE artifacts SET effective=0 WHERE task_id=? AND revision=? AND kind='requirement_analysis' AND job_id!=?", (task['id'], job['revision'], job['id']))
                db.execute("UPDATE revisions SET analysis=?,status='review' WHERE task_id=? AND revision=?", (pack(result), task["id"], job["revision"]))
                for question in result["questions"]:
                    db.execute("INSERT INTO questions(id,task_id,revision,text,blocking,answer,created_at) VALUES(?,?,?,?,?,?,?)",
                               (uid(), task["id"], job["revision"], question["text"], int(question["blocking"]), None, now()))
                if task["active_revision"] is None:
                    db.execute("UPDATE tasks SET status='awaiting_requirement_confirmation',run_status='completed',blocked_reason='',next_owner='owner' WHERE id=?", (task["id"],))
                else:
                    db.execute("UPDATE tasks SET run_status='completed' WHERE id=?", (task["id"],))
                self._discuss(db, task["id"], job["revision"], "product", "analysis", result["summary"])
            else:
                if job["kind"] == "develop":
                    questions = result.get("questions", [])
                    if not isinstance(questions, list) or len(questions) > 50:
                        raise TownError(400, "开发澄清问题格式无效")
                    if result.get("question"):
                        questions = [*questions, result["question"]]
                    for question in questions:
                        if isinstance(question, dict):
                            question = question.get("text")
                        question = text(question, "开发澄清问题", 6000)
                        db.execute("INSERT INTO questions(id,task_id,revision,text,blocking,answer,created_at,role,visibility) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (uid(), task["id"], job["revision"], question, 1, None, now(), "developer", "private"))
                reason = ("开发候选成果已保存，等待用户审查；尚未证明自测、部署或验收通过" if job["kind"] == "develop"
                          else "测试用例已整理，尚未执行；部署与受限验收连接器尚未接入")
                db.execute("UPDATE tasks SET run_status='completed',blocked_reason=?,next_owner='owner' WHERE id=?", (reason, task["id"]))
                if job["kind"] == "plan_tests":
                    self._discuss(db, task["id"], job["revision"], "tester", "test_plan", result["summary"])
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task["id"]))
            self._event(db, task["id"], job["revision"], "job.completed", {"job_id": job["id"], "kind": job["kind"]})

    def _fail(self, job, exc):
        with self.db() as db:
            if self.closed:
                return
            changed = db.execute("UPDATE jobs SET status='failed',error=?,finished_at=? WHERE id=? AND status='running'",
                                 (str(exc)[:3000], now(), job["id"])).rowcount
            if changed:
                db.execute("UPDATE tasks SET run_status='failed',blocked_reason=?,updated_at=? WHERE id=?",
                           ("作业失败，请在用户详情查看原因并重试", now(), job["task_id"]))
                self._event(db, job["task_id"], job["revision"], "job.failed", {"job_id": job["id"]})

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for cancel in self.cancel_events.values():
                cancel.set()
            self.wake.set()
            with self.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',error=?,finished_at=? WHERE status='running'", ("应用关闭，执行中断", now()))
                db.execute("UPDATE tasks SET run_status='interrupted',blocked_reason=? WHERE run_status='running'", ("应用关闭，执行中断",))
        if self.scheduler and self.scheduler is not threading.current_thread():
            self.scheduler.join(timeout=1)
