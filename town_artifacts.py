"""Immutable, attributable text artifacts; no executable uploads or arbitrary paths."""
import json
import os
import re

from town_security import CATEGORIES, SecurityOffice, linked
from town_store import TownError, digest, event, now, pack, text, uid

KINDS = {"note", "analysis", "specification", "test-report", "exercise", "skill"}


class Artifacts:
    def __init__(self, app):
        self.app = app
        with app.db() as db:
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS v2_artifacts_immutable
                BEFORE UPDATE OF owner,category,kind,title,source_type,source_id,hash,bytes,parents,root,demo,created_at
                ON v2_artifacts BEGIN SELECT RAISE(ABORT,'immutable artifact'); END;
            """)
        self.recover()

    def path(self, row, pending=False):
        if row["category"] not in CATEGORIES or not re.fullmatch(r"[a-f0-9]{32}", row["id"]):
            raise TownError(503, "成果路径记录无效")
        if not SecurityOffice.ensure_workspace(self.app.data_dir, row["owner"]):
            raise TownError(503, "成果工作区不可用或包含链接")
        result = self.app.data_dir / "workspaces" / row["owner"] / row["category"] / (row["id"] + (".pending" if pending else ".md"))
        if result.is_symlink() or result.exists() and (linked(result) or result.stat().st_nlink != 1):
            raise TownError(503, "成果文件不能是链接")
        return result

    def content(self, row):
        if row["status"] not in ("available", "archived"):
            raise TownError(409, "成果尚未发布或已损坏")
        path = self.path(row)
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise TownError(409, "成果完整性校验失败")
        content = path.read_bytes()
        if digest(content) != row["hash"]:
            raise TownError(409, "成果指纹已变化，不能读取或计入验收")
        return content.decode("utf-8")

    @staticmethod
    def row(db, identity):
        row = db.execute("SELECT * FROM v2_artifacts WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise TownError(404, "没有找到这份成果")
        return dict(row)

    def require(self, db, identity, reader, destination=None, delivery=False, seen=None):
        seen = set() if seen is None else seen
        if identity in seen:
            return
        if len(seen) >= 100:
            raise TownError(403, "资料来源链超出检查范围")
        seen.add(identity)
        row = self.row(db, identity)
        policy = json.loads(row["policy"])
        if reader not in policy["readers"]:
            raise TownError(403, "稽查署：这位居民无权读取来源资料")
        if delivery and not policy["deliverable"]:
            raise TownError(403, "稽查署：来源资料不允许交付")
        if destination is not None and destination not in policy["destinations"]:
            raise TownError(403, "稽查署：来源资料不允许发往这个模型端点")
        self.content(row)
        for parent in json.loads(row["parents"]):
            self.require(db, parent, reader, destination, delivery, seen)

    def create(self, *, owner, category, kind, title, content, source_type, source_id,
               parents=(), demo=False, policy=None, request_id=None, root=None):
        if category not in CATEGORIES or kind not in KINDS:
            raise TownError(400, "成果分类或类型无效")
        title = text(title, "标题", 240)
        content = text(content, "成果正文", 300000)
        if len(parents) > 20:
            raise TownError(400, "来源资料过多")
        parents = sorted(set(parents))
        policy = policy or dict(readers=[owner], destinations=["local"], deliverable=False)
        request_id = text(request_id or uid(), "请求编号", 100)
        raw = content.encode("utf-8")
        fingerprint = digest(raw)
        request_hash = digest([owner, category, kind, title, fingerprint, source_type, source_id, parents, root])
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.app._agent(db, owner)
            request = db.execute("SELECT * FROM v2_requests WHERE id=?", (request_id,)).fetchone()
            if request:
                if request["fingerprint"] != request_hash:
                    raise TownError(409, "请求编号已用于另一份成果")
                return self.row(db, request["artifact_id"])
            existing = db.execute("SELECT * FROM v2_artifacts WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise TownError(409, "请求编号已用于另一份成果")
                return dict(existing)
            for identity in parents:
                parent = self.row(db, identity)
                self.content(parent)
                demo = demo or bool(parent["demo"])
            existing = db.execute("SELECT * FROM v2_artifacts WHERE owner=? AND source_type=? AND source_id=? AND category=? AND kind=? AND hash=?",
                                  (owner, source_type, source_id, category, kind, fingerprint)).fetchone()
            if existing:
                db.execute("INSERT INTO v2_requests VALUES (?,?,?)", (request_id, request_hash, existing["id"]))
                return dict(existing)
            identity = uid()
            record = dict(id=identity, owner=owner, category=category, kind=kind, title=title,
                          source_type=source_type, source_id=source_id, hash=fingerprint, bytes=len(raw),
                          parents=pack(parents), root=root or identity, demo=int(demo), policy=pack(policy),
                          status="pending", created_at=now(), request_id=request_id, request_hash=request_hash)
            stage = self.path(record, pending=True)
            with stage.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            db.execute("INSERT INTO v2_artifacts(" + ",".join(record) + ") VALUES (" + ",".join("?" for _ in record) + ")", tuple(record.values()))
            db.execute("INSERT INTO v2_requests VALUES (?,?,?)", (request_id, request_hash, identity))
            event(db, "artifact_staged", identity, {"hash": fingerprint, "source_type": source_type, "source_id": source_id})
        # Crash here is recoverable: the staged file and pending DB record agree.
        self.publish(record)
        with self.app.db() as db:
            return self.row(db, identity)

    def publish(self, row):
        with self.app.lock:
            stage, target = self.path(row, True), self.path(row)
            candidate = target if target.exists() else stage
            if not candidate.exists() or candidate.stat().st_size != row["bytes"] or digest(candidate.read_bytes()) != row["hash"]:
                raise TownError(409, "待发布成果完整性校验失败")
            if candidate == stage:
                stage.replace(target)
            with self.app.db() as db:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute("UPDATE v2_artifacts SET status='available',published_at=? WHERE id=? AND status='pending'", (now(), row["id"])).rowcount
                if changed:
                    event(db, "artifact_published", row["id"], {"hash": row["hash"]})

    def recover(self):
        with self.app.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM v2_artifacts WHERE status='pending'")]
        for row in rows:
            try:
                self.publish(row)
            except (OSError, TownError):
                with self.app.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE v2_artifacts SET status='broken' WHERE id=?", (row["id"],))
                    event(db, "artifact_recovery_failed", row["id"], {})

    def save_reply(self, data):
        if set(data) - {"message_id", "category", "kind", "title", "request_id", "content"}:
            raise TownError(400, "不允许客户端指定作者、来源或指纹")
        with self.app.db() as db:
            row = db.execute("SELECT * FROM messages WHERE id=? AND role='assistant' AND status='complete'", (data.get("message_id"),)).fetchone()
            if not row or not db.execute("SELECT 1 FROM jobs WHERE assistant_message_id=? AND status='completed'", (row["id"],)).fetchone():
                raise TownError(409, "只能保存已经完成的助手回复")
            sources = db.execute("SELECT sources FROM v2_message_sources WHERE message_id=?", (row["id"],)).fetchone()
            parents = json.loads(sources[0]) if sources else []
            for parent in parents:
                self.require(db, parent, row["agent_id"])
        edited = "content" in data and data["content"] != row["content"]
        return self.create(owner=row["agent_id"], category=data.get("category", "results"), kind=data.get("kind", "note"),
                           title=data.get("title", "对话成果"), content=data.get("content", row["content"]),
                           source_type="manual-derived" if edited else "assistant", source_id=row["id"], parents=parents,
                           request_id=data.get("request_id"))

    def import_text(self, data):
        if set(data) - {"owner", "title", "content", "category", "kind", "request_id"}:
            raise TownError(400, "资料导入字段无效")
        return self.create(owner=data.get("owner"), title=data.get("title"), content=data.get("content"),
                           category=data.get("category", "materials"), kind=data.get("kind", "note"),
                           source_type="manual", source_id="user", request_id=data.get("request_id"))

    def deliver(self, data):
        with self.app.db() as db:
            row = self.row(db, data.get("artifact_id"))
            recipient = data.get("recipient")
            self.app._agent(db, recipient)
            self.require(db, row["id"], recipient, delivery=True)
            content = self.content(row)
        return self.create(owner=recipient, category=row["category"], kind=row["kind"], title=row["title"],
                           content=content, source_type="delivery", source_id=row["id"], parents=[row["id"]],
                           root=row["root"], demo=bool(row["demo"]), policy=json.loads(row["policy"]), request_id=data.get("request_id"))

    def revise(self, data):
        with self.app.db() as db:
            row = self.row(db, data.get("artifact_id"))
            self.require(db, row["id"], row["owner"])
        return self.create(owner=row["owner"], category=row["category"], kind=row["kind"], title=data.get("title", row["title"]),
                           content=data.get("content"), source_type="manual-derived", source_id=row["id"], parents=[row["id"]],
                           root=row["root"], policy=json.loads(row["policy"]), request_id=data.get("request_id"))

    def detail(self, identity):
        with self.app.db() as db:
            row = self.row(db, identity)
            row["content"] = self.content(row)
            row["original_author"] = self.row(db, row["root"])["owner"]
            return row

    def withdraw(self, identity):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.row(db, identity)
            db.execute("DELETE FROM v2_acceptances WHERE artifact_id=?", (identity,))
            db.execute("UPDATE v2_tasks SET status='review' WHERE artifact_id=? AND status='accepted'", (identity,))
            event(db, "acceptance_withdrawn", identity, {})
        return {}

    def archive(self, identity):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, identity)
            self.content(row)
            db.execute("UPDATE v2_artifacts SET status='archived' WHERE id=?", (identity,))
            event(db, "artifact_archived", identity, {})
        return {}
