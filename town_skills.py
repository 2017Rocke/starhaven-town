"""Reviewed reference skills and versioned production contracts; never executes text."""
import json
import re
import datetime as dt

from town_security import KNOWN_SECRET
from town_store import TownError, digest, event, integer, now, pack, uid, setting, get_setting, day, TZ
from town_artifacts import KINDS
from town_security import CATEGORIES


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
