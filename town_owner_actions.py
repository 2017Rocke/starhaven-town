"""One-use, state-bound native owner confirmations. Browser requests are intents only."""
import hmac
import json
import time
import datetime as dt

from town_store import TownError, digest, event, now, pack, text, uid, setting, get_setting, day


class OwnerActions:
    def __init__(self, app, secret=None):
        self.app = app
        self.secret = secret
        with app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE v2_intents SET status='expired' WHERE status='pending'")

    def authenticate(self, value):
        if not self.secret or not isinstance(value, str) or not hmac.compare_digest(self.secret, value):
            raise TownError(403, "该操作只能由原生窗口确认")

    def normalize(self, db, action, payload):
        if not isinstance(payload, dict):
            raise TownError(400, "确认参数无效")
        if action == "policy":
            value = self.app.budget.validate(payload, db)
            return value
        if action == "artifact-policy":
            if set(payload) != {"artifact_id", "readers", "destinations", "deliverable"}:
                raise TownError(400, "资料权限字段不完整")
            row = self.app.artifacts.row(db, payload["artifact_id"])
            self.app.artifacts.content(row)
            readers, destinations = payload["readers"], payload["destinations"]
            if not isinstance(readers, list) or len(readers) > 33 or any(not isinstance(v, str) for v in readers):
                raise TownError(400, "资料读者无效")
            for reader in readers:
                self.app._agent(db, reader)
            if not isinstance(destinations, list) or len(destinations) > 9 or any(not isinstance(v, str) for v in destinations):
                raise TownError(400, "外发目的地无效")
            allowed = {"local", *(x["base_url"] for x in self.app.budget.config(db)["endpoints"])}
            if not set(destinations) <= allowed or type(payload["deliverable"]) is not bool:
                raise TownError(400, "资料只能授权给已确认的模型端点")
            return dict(artifact_id=row["id"], readers=sorted(set(readers)), destinations=sorted(set(destinations)), deliverable=payload["deliverable"])
        if action == "accept":
            if set(payload) != {"artifact_id", "note"}:
                raise TownError(400, "请提供成果与验收依据")
            row = self.app.artifacts.row(db, payload["artifact_id"])
            self.app.artifacts.content(row)
            if row["demo"] or row["source_type"] == "delivery":
                raise TownError(400, "演示与交付副本不能作为居民原创成果验收")
            return dict(artifact_id=row["id"], note=text(payload["note"], "验收依据", 2000))
        if action == "contract":
            if not {"agent_id", "requirements"} <= set(payload) or set(payload) - {"agent_id", "requirements", "weekdays", "active"}:
                raise TownError(400, "产出约定字段无效")
            self.app._agent(db, payload["agent_id"])
            weekdays, active = payload.get("weekdays", [1, 2, 3, 4, 5, 6, 7]), payload.get("active", True)
            if type(active) is not bool or not isinstance(weekdays, list) or not weekdays or any(type(v) is not int or not 1 <= v <= 7 for v in weekdays):
                raise TownError(400, "约定的开关或工作日无效")
            return dict(agent_id=payload["agent_id"], requirements=self.app.skills.validate_contract(payload["requirements"]), weekdays=sorted(set(weekdays)), active=active)
        if action == "enable-skill":
            if set(payload) != {"skill_id"}:
                raise TownError(400, "技能启用字段无效")
            row = self.app.skills.row(db, payload["skill_id"])
            self.app.skills.closure(db, [row["id"]], row["agent_id"], require_active=False)
            for dep in json.loads(row["dependencies"]):
                if self.app.skills.row(db, dep)["status"] != "active":
                    raise TownError(409, "请先确认启用依赖技能")
            count = db.execute("SELECT COUNT(*) FROM v2_skills WHERE agent_id=? AND status='active' AND id!=?", (row["agent_id"], row["id"])).fetchone()[0]
            if count >= 5:
                raise TownError(409, "每位居民最多启用五个技能版本")
            return dict(skill_id=row["id"])
        raise TownError(400, "不支持的原生确认动作")

    def snapshot(self, db, action, payload):
        state = dict(policy=self.app.budget.config(db), access_revision=get_setting(db, "access_revision", 0))
        if "artifact_id" in payload:
            row = self.app.artifacts.row(db, payload["artifact_id"])
            state["artifact"] = row
            state["content"] = self.app.artifacts.content(row)
            accepted = db.execute("SELECT * FROM v2_acceptances WHERE artifact_id=?", (row["id"],)).fetchone()
            state["accepted"] = dict(accepted) if accepted else None
        if action == "enable-skill":
            skill = self.app.skills.row(db, payload["skill_id"])
            state["skill"] = skill
            state["references"] = [dict(r, content=self.app.artifacts.content(r)) for r in self.app.skills.closure(db, [skill["id"]], skill["agent_id"], require_active=False)]
            state["active"] = [r[0] for r in db.execute("SELECT id FROM v2_skills WHERE agent_id=? AND status='active' ORDER BY id", (skill["agent_id"],))]
        if action == "contract":
            state["contracts"] = [dict(r) for r in db.execute("SELECT * FROM v2_contracts WHERE agent_id=? AND status='active'", (payload["agent_id"],))]
        return state

    def issue(self, data):
        if set(data) != {"action", "payload"}:
            raise TownError(400, "确认请求字段无效")
        if not self.secret:
            raise TownError(409, "请使用桌面原生窗口确认授权；浏览器模式可查看与暂停")
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            action = data["action"]
            payload = self.normalize(db, action, data["payload"])
            snapshot = self.snapshot(db, action, payload)
            state_hash = digest(snapshot)
            identity = uid()
            expires = time.time() + 300
            fingerprint = digest([identity, action, payload, state_hash, expires])
            db.execute("INSERT INTO v2_intents VALUES (?,?,?,?,?,?,?)", (identity, action, pack(payload), state_hash, fingerprint, expires, "pending"))
            event(db, "owner_intent", identity, {"action": action, "digest": fingerprint})
            return dict(id=identity, expires=expires)

    def pending(self, db, identity):
        row = db.execute("SELECT * FROM v2_intents WHERE id=?", (identity,)).fetchone()
        if not row or row["status"] != "pending" or row["expires"] < time.time():
            raise TownError(409, "确认请求已过期或已使用")
        payload = json.loads(row["payload"])
        # Revalidate every semantic condition and the exact target version at confirmation.
        self.normalize(db, row["action"], payload if row["action"] != "policy" else {k: v for k, v in payload.items() if k not in ("revision", "paused")})
        snapshot = self.snapshot(db, row["action"], payload)
        if digest(snapshot) != row["state_hash"]:
            raise TownError(409, "目标内容或权限已改变，请重新发起确认")
        return row, payload, snapshot

    def preview(self, identity):
        with self.app.db() as db:
            row, payload, snapshot = self.pending(db, identity)
        labels = {"policy": "培养计划与网络预算", "artifact-policy": "资料读取、交付与外发权限", "accept": "成果验收", "contract": "居民产出约定", "enable-skill": "启用参考技能"}
        return dict(id=identity, digest=row["digest"], title=labels[row["action"]],
                    summary=json.dumps(dict(action=labels[row["action"]], settings=payload, evidence=snapshot), ensure_ascii=False, indent=2))

    def confirm(self, data):
        if set(data) != {"id", "digest", "approved"} or type(data["approved"]) is not bool:
            raise TownError(400, "原生确认参数无效")
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row, payload, unused = self.pending(db, data["id"])
            if not hmac.compare_digest(row["digest"], str(data["digest"])):
                raise TownError(409, "确认指纹不一致")
            if data["approved"]:
                self.apply(db, row["action"], payload)
            status = "approved" if data["approved"] else "rejected"
            db.execute("UPDATE v2_intents SET status=? WHERE id=?", (status, row["id"]))
            event(db, "owner_decision", row["id"], {"action": row["action"], "status": status, "digest": row["digest"]})
        return dict(status=status)

    def apply(self, db, action, payload):
        if action == "policy":
            self.app.budget.apply(db, payload)
            setting(db, "revoked_residents", [r for r in get_setting(db, "revoked_residents", []) if r not in payload["residents"]])
        elif action == "artifact-policy":
            db.execute("UPDATE v2_artifacts SET policy=? WHERE id=?", (pack({k: v for k, v in payload.items() if k != "artifact_id"}), payload["artifact_id"]))
        elif action == "accept":
            db.execute("INSERT OR IGNORE INTO v2_acceptances VALUES (?,'native-owner',?,?)", (payload["artifact_id"], payload["note"], now()))
            db.execute("UPDATE v2_tasks SET status='accepted',updated_at=? WHERE artifact_id=? AND status='review'", (now(), payload["artifact_id"]))
        elif action == "contract":
            effective = (dt.date.fromisoformat(day()) + dt.timedelta(days=1)).isoformat()
            db.execute("INSERT INTO v2_contracts(id,agent_id,requirements,status,created_at,effective_day,weekdays) VALUES (?,?,?,?,?,?,?)", (uid(), payload["agent_id"], pack(payload["requirements"]), "active" if payload["active"] else "disabled", now(), effective, pack(payload["weekdays"])))
        elif action == "enable-skill":
            db.execute("UPDATE v2_skills SET status='active' WHERE id=?", (payload["skill_id"],))
        setting(db, "access_revision", get_setting(db, "access_revision", 0) + 1)
