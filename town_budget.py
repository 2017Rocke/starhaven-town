"""Single resident ledger for chats and training, with conservative reservations."""
import json
from urllib.parse import urlsplit

from town_store import TownError, day, event, get_setting, integer, now, pack, setting, text, uid, verify_events

DEFAULT = dict(revision=0, enabled=False, paused=True, strict=False, direction="提升岗位基础能力，先练习再独立验收",
               endpoints=[], residents=[], daily_tokens=100000, daily_calls=12, daily_tasks=1,
               max_output_tokens=1024, max_attempts=1, request_total_cap=32000, lifetime_tokens=3000000)


class Budget:
    def __init__(self, app):
        self.app = app
        # An interrupted dispatch may have reached the provider. Never refund it blindly.
        with app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM v2_calls WHERE status='reserved'").fetchall():
                db.execute("UPDATE v2_calls SET charged=?,status=?,settled_at=? WHERE id=?",
                           (row["reserved"] if row["sent"] else 0, "unknown" if row["sent"] else "released", now(), row["id"]))
                event(db, "call_recovered", row["id"], {"sent": bool(row["sent"]), "reserved": row["reserved"]})

    def config(self, db=None):
        if db is not None:
            return dict(DEFAULT, **get_setting(db, "growth_policy", {}))
        with self.app.db() as conn:
            return self.config(conn)

    def balances(self, db):
        policy, today = self.config(db), day()
        rows = db.execute("""
            SELECT agent_id,
                SUM(CASE WHEN day=? AND status='reserved' THEN reserved ELSE 0 END) AS reserved_today,
                SUM(CASE WHEN day=? AND status!='reserved' THEN charged ELSE 0 END) AS charged_today,
                SUM(CASE WHEN day=? AND status NOT IN ('released','blocked') THEN 1 ELSE 0 END) AS calls_today,
                SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END) AS lifetime,
                SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END) AS unknown_calls,
                SUM(CASE WHEN status='unmetered' THEN 1 ELSE 0 END) AS unmetered_calls
            FROM v2_calls GROUP BY agent_id
        """, (today, today, today))
        totals = {r["agent_id"]: dict(r) for r in rows}
        result = []
        for agent in db.execute("SELECT id FROM agents ORDER BY created_at"):
            total = totals.get(agent["id"], dict(agent_id=agent["id"], reserved_today=0,
                charged_today=0, calls_today=0, lifetime=0, unknown_calls=0, unmetered_calls=0))
            total.update(day=today,
                daily_remaining=max(0, policy["daily_tokens"] - total["charged_today"] - total["reserved_today"]),
                calls_remaining=max(0, policy["daily_calls"] - total["calls_today"]),
                lifetime_remaining=max(0, policy["lifetime_tokens"] - total["lifetime"]))
            result.append(total)
        return result

    def validate(self, data, db):
        if set(data) - (set(DEFAULT) - {"revision", "paused"}):
            raise TownError(400, "培养与网络设置包含未知字段")
        result = dict(DEFAULT)
        result.update(data)
        for flag in ("enabled", "strict"):
            if type(result[flag]) is not bool:
                raise TownError(400, "开关必须是布尔值")
        result["direction"] = text(result["direction"], "培养方向", 2000)
        for key, low, high in (("daily_tokens", 1000, 5000000), ("daily_calls", 1, 100),
                               ("daily_tasks", 1, 3), ("max_output_tokens", 128, 8192), ("max_attempts", 1, 2)):
            integer(result[key], key, low, high)
        integer(result["request_total_cap"], "单次总量", 1000, 200000)
        integer(result["lifetime_tokens"], "累计额度", 1000, 1000000000)
        if not isinstance(result["residents"], list) or len(result["residents"]) > 33 or any(not isinstance(v, str) for v in result["residents"]):
            raise TownError(400, "请选择允许训练的居民")
        result["residents"] = sorted(set(result["residents"]))
        for identity in result["residents"]:
            self.app._agent(db, identity)
        if not isinstance(result["endpoints"], list) or len(result["endpoints"]) > 8:
            raise TownError(400, "模型端点最多 8 个")
        from provider_bridge import normalize_base_url
        endpoints = []
        for endpoint in result["endpoints"]:
            if not isinstance(endpoint, dict) or set(endpoint) != {"base_url", "model"}:
                raise TownError(400, "端点需明确服务地址与模型")
            try:
                base_url = normalize_base_url(endpoint["base_url"])
            except ValueError as exc:
                raise TownError(400, str(exc)) from None
            endpoints.append(dict(base_url=base_url, model=text(endpoint["model"], "模型", 200)))
        result["endpoints"] = endpoints
        if result["enabled"] and (not result["strict"] or not endpoints or not result["residents"]):
            raise TownError(400, "自动训练需要严格网络模式、明确模型端点和居民范围")
        if result["enabled"]:
            from provider_bridge import ProviderBridge
            if any(not ProviderBridge.budget_capability(endpoint)["enforce_supported"] for endpoint in endpoints):
                raise TownError(400, "自动训练端点需具备已验证的预算适配档案")
        result["paused"] = False
        return result

    def apply(self, db, config):
        config = dict(config, revision=self.config(db)["revision"] + 1)
        setting(db, "growth_policy", config)
        event(db, "policy_approved", "growth", config)
        return config

    def pause(self):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            config = self.config(db)
            config.update(paused=True, revision=config["revision"] + 1)
            setting(db, "growth_policy", config)
            event(db, "growth_paused", "growth", {})
        self.app.growth.cancel_running()
        cancel = getattr(self.app.bridge, "cancel_for_agent", None)
        if callable(cancel):
            cancel()
        return config

    def revoke(self, agent):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.app._agent(db, agent)
            config = self.config(db)
            config["residents"] = [v for v in config["residents"] if v != agent]
            config["revision"] += 1
            revoked = get_setting(db, "revoked_residents", [])
            setting(db, "revoked_residents", sorted(set(revoked + [agent])))
            setting(db, "growth_policy", config)
            event(db, "resident_revoked", agent, {})
        cancel = getattr(self.app.bridge, "cancel_for_agent", None)
        if callable(cancel):
            cancel(agent)
        return {}

    def check_network(self, url, purpose):
        config = self.config()
        if not config["strict"]:
            return
        if purpose == "github":
            # Public read-only collection retains the fixed inspection-office domain list.
            return
        if purpose != "model" or not any(url == item["base_url"] + "/chat/completions" for item in config["endpoints"]):
            raise TownError(403, "严格网络模式：该端点未经原生确认授权")

    def admit(self, config, payload, context):
        identity = uid()
        agent = context["agent_id"]
        options = context.get("options", {})
        controlled = bool(options.get("controlled"))
        admitted_day = day()
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self.config(db)
            if agent in get_setting(db, "revoked_residents", []):
                raise TownError(403, "该居民已暂停，恢复需要原生确认")
            enforce = controlled or policy["strict"]
            if enforce:
                from provider_bridge import ProviderBridge
                if not ProviderBridge.budget_capability(config)["enforce_supported"]:
                    raise TownError(403, "当前模型端点尚无经过验证的预算适配档案，只支持普通聊天审计；受控训练请使用已支持的 API 模型")
                if not verify_events(db):
                    raise TownError(503, "成长档案校验失败，已阻止受控调用")
                if policy["paused"] or agent not in policy["residents"]:
                    raise TownError(403, "稽查署：居民调用权限已暂停或未授权")
                if controlled and not policy["enabled"]:
                    raise TownError(403, "成长署自动训练尚未启用")
                if {"base_url": config["base_url"], "model": config["model"]} not in policy["endpoints"]:
                    raise TownError(403, "稽查署：模型或端点未经授权")
                if options.get("policy_revision", policy["revision"]) != policy["revision"]:
                    raise TownError(409, "培养计划授权已变化，请重新制定计划")
                contract = self.app.skills.contract_status(db, agent)
                if not controlled and contract["configured"] and not contract["satisfied"]:
                    raise TownError(403, "尚未满足已确认的产出约定；可继续基础训练")
            for source in options.get("sources", []):
                self.app.artifacts.require(db, source, agent, config["base_url"])
            self.app.skills.closure(db, options.get("skill_versions", []), agent)
            # UTF-8 bytes + framing is a deliberately conservative tokenizer-independent
            # estimate. It is not a claim about the provider's tokenizer or billing.
            output_cap = json.loads(payload).get("max_tokens")
            if type(output_cap) is not int or not 1 <= output_cap <= policy["max_output_tokens"]:
                raise TownError(409, "模型输出上限已变化，请重新发送")
            reserved = len(payload) + 512 + output_cap
            totals = db.execute("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END),0), COUNT(*) FROM v2_calls WHERE agent_id=? AND day=? AND status NOT IN ('released','blocked')", (agent, admitted_day)).fetchone()
            lifetime = db.execute("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END),0) FROM v2_calls WHERE agent_id=?", (agent,)).fetchone()[0]
            if enforce and (reserved > policy["request_total_cap"] or lifetime + reserved > policy["lifetime_tokens"] or totals[0] + reserved > policy["daily_tokens"] or totals[1] >= policy["daily_calls"]):
                raise TownError(429, "今日居民共享预算不足，聊天与训练共用额度")
            db.execute("INSERT INTO v2_calls(id,agent_id,day,purpose,endpoint,model,reserved,status,revision,created_at) VALUES (?,?,?,?,?,?,?,'reserved',?,?)",
                       (identity, agent, admitted_day, "training" if controlled else "chat", config["base_url"], config["model"], reserved, policy["revision"], now()))
            event(db, "call_reserved", identity, {"agent_id": agent, "reserved": reserved, "enforce": enforce})
            context["access_revision"] = get_setting(db, "access_revision", 0)
        context["call_id"] = identity
        context["enforce"] = enforce
        return identity

    def dispatch(self, context):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM v2_calls WHERE id=?", (context["call_id"],)).fetchone()
            policy = self.config(db)
            if row is None or row["status"] != "reserved" or context["cancel"].is_set():
                raise TownError(409, "调用已撤销")
            if row["agent_id"] in get_setting(db, "revoked_residents", []):
                raise TownError(403, "发送前该居民已暂停")
            if context["enforce"] and (policy["paused"] or row["revision"] != policy["revision"]):
                raise TownError(403, "发送前权限已撤销")
            if context["access_revision"] != get_setting(db, "access_revision", 0):
                raise TownError(409, "发送前资料或技能授权已变化")
            for source in context.get("options", {}).get("sources", []):
                self.app.artifacts.require(db, source, row["agent_id"], row["endpoint"])
            self.app.skills.closure(db, context.get("options", {}).get("skill_versions", []), row["agent_id"])
            # Last authoritative gate before opener.open; revocation after this point is
            # best effort. Persist 'sent' first so a crash never refunds an uncertain call.
            db.execute("UPDATE v2_calls SET sent=1 WHERE id=?", (row["id"],))
            event(db, "call_dispatched", row["id"], {})

    @staticmethod
    def usage(raw):
        if not isinstance(raw, dict):
            return None
        prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
        if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
            return None
        total = raw.get("total_tokens", prompt + completion)
        if type(total) is not int or total < prompt + completion or total > 1000000000:
            return None
        return dict(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)

    def settle(self, context):
        if not context.get("call_id"):
            return
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM v2_calls WHERE id=?", (context["call_id"],)).fetchone()
            if row["status"] != "reserved":
                usage = self.usage(context.get("usage"))
                if row["status"] == "unknown" and usage:
                    charged = usage["total_tokens"]
                    db.execute("UPDATE v2_calls SET charged=?,status='settled',usage=?,settled_at=? WHERE id=?", (charged, pack(usage), now(), row["id"]))
                    event(db, "usage_correction", row["id"], {"previous": row["charged"], "charged": charged, "day": row["day"], "source": "provider-worker"})
                    if charged > row["reserved"]:
                        policy = self.config(db)
                        policy.update(paused=True, revision=policy["revision"] + 1)
                        setting(db, "growth_policy", policy)
                return
            usage = self.usage(context.get("usage"))
            charged = usage["total_tokens"] if usage else row["reserved"] if row["sent"] else 0
            status = "settled" if usage else "unknown" if row["sent"] else "released"
            db.execute("UPDATE v2_calls SET charged=?,status=?,usage=?,settled_at=? WHERE id=?", (charged, status, pack(usage), now(), row["id"]))
            event(db, "call_settled", row["id"], {"charged": charged, "status": status, "day": row["day"]})
            if charged > row["reserved"]:
                policy = self.config(db)
                policy.update(paused=True, revision=policy["revision"] + 1)
                setting(db, "growth_policy", policy)
                event(db, "budget_overrun", row["id"], {"charged": charged, "reserved": row["reserved"]})

    def external(self, options, agent):
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self.config(db)
            if policy["strict"] or options.get("controlled") or options.get("sources"):
                raise TownError(403, "Codex 外部进程无法核验网络与用量；严格模式、资料引用和训练需使用 API 模式")
            if agent in get_setting(db, "revoked_residents", []):
                raise TownError(403, "该居民已暂停，恢复需要原生确认")
            identity = uid()
            db.execute("INSERT INTO v2_calls(id,agent_id,day,purpose,endpoint,model,reserved,status,sent,revision,created_at) VALUES (?,?,?,'chat','external-process','codex',0,'unmetered',1,?,?)", (identity, agent, day(), self.config(db)["revision"], now()))
            event(db, "unmetered_external_call", identity, {"agent_id": agent, "usage_known": False})
            return identity

    def blocked(self, config, context):
        if context.get("call_id"):
            return
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            event(db, "call_blocked", context["agent_id"], {"purpose": "training" if context.get("options", {}).get("controlled") else "chat", "reason": context.get("error", "调用未获准"), "model": config.get("model")})
