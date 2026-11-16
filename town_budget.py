"""Isolated development and growth ledgers, with conservative reservations."""
import json
from urllib.parse import urlsplit

from town_store import TownError, day, event, get_setting, integer, now, pack, setting, text, uid, verify_events

DEFAULT = dict(revision=0, enabled=False, paused=True, strict=False, direction="提升岗位基础能力，先练习再独立验收",
               endpoints=[], residents=[], daily_tokens=100000, daily_calls=12, daily_tasks=1,
               max_output_tokens=1024, max_attempts=1, request_total_cap=32000, lifetime_tokens=3000000)
DEVELOPMENT_DEFAULT = dict(revision=0, relaxed=False, max_output_tokens=32768)
DEVELOPMENT_OUTPUT_LIMITS = (4096, 8192, 16384, 32768)


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

    def development_config(self, db=None):
        if db is not None:
            return dict(DEVELOPMENT_DEFAULT, **get_setting(db, "development_budget", {}))
        with self.app.db() as conn:
            return self.development_config(conn)

    @staticmethod
    def validate_development(data):
        if not isinstance(data, dict) or set(data) != {"relaxed", "max_output_tokens"}:
            raise TownError(400, "研发额度设置必须包含开关与输出上限，不允许其他字段")
        result = {key: value for key, value in DEVELOPMENT_DEFAULT.items() if key != "revision"}
        result.update(data)
        if type(result["relaxed"]) is not bool:
            raise TownError(400, "研发额度开关必须是布尔值")
        if (type(result["max_output_tokens"]) is not int
                or result["max_output_tokens"] not in DEVELOPMENT_OUTPUT_LIMITS):
            raise TownError(400, "研发输出上限请选择 4096、8192、16384 或 32768")
        return result

    def apply_development(self, db, data):
        config = self.validate_development(data)
        config["revision"] = self.development_config(db)["revision"] + 1
        setting(db, "development_budget", config)
        event(db, "development_budget_approved", "development", config)
        return config

    @staticmethod
    def _request_limits(options, policy, development_config):
        development = bool(options.get("controlled") and options.get("workbench"))
        relaxed = development and development_config["relaxed"]
        return dict(max_output_tokens=development_config["max_output_tokens"] if relaxed else policy["max_output_tokens"],
                    development=development, relaxed=relaxed,
                    development_revision=development_config["revision"] if development else None)

    def request_limits(self, options):
        with self.app.db() as db:
            return self._request_limits(options, self.config(db), self.development_config(db))

    def _balances(self, db, development=False):
        policy, today = self.config(db), day()
        development_config = self.development_config(db)
        relaxed = development and development_config["relaxed"]
        # The purpose is immutable on each ledger row. Historical development
        # calls stay in their own pool when relaxation is enabled or disabled.
        scope = "purpose='development'" if development else "purpose!='development'"
        rows = db.execute("""
            SELECT agent_id,
                SUM(CASE WHEN day=? AND status='reserved' THEN reserved ELSE 0 END) AS reserved_today,
                SUM(CASE WHEN day=? AND status!='reserved' THEN charged ELSE 0 END) AS charged_today,
                SUM(CASE WHEN day=? AND status NOT IN ('released','blocked') THEN 1 ELSE 0 END) AS calls_today,
                SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END) AS lifetime,
                SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END) AS unknown_calls,
                SUM(CASE WHEN status='unmetered' THEN 1 ELSE 0 END) AS unmetered_calls
            FROM v2_calls WHERE %s GROUP BY agent_id
        """ % scope, (today, today, today))
        totals = {r["agent_id"]: dict(r) for r in rows}
        result = []
        for agent in db.execute("SELECT id FROM agents ORDER BY created_at"):
            total = totals.get(agent["id"], dict(agent_id=agent["id"], reserved_today=0,
                charged_today=0, calls_today=0, lifetime=0, unknown_calls=0, unmetered_calls=0))
            total.update(day=today, budget_scope="development" if development else "chat-training",
                daily_remaining=None if relaxed else max(0, policy["daily_tokens"] - total["charged_today"] - total["reserved_today"]),
                calls_remaining=None if relaxed else max(0, policy["daily_calls"] - total["calls_today"]),
                lifetime_remaining=None if relaxed else max(0, policy["lifetime_tokens"] - total["lifetime"]))
            if development:
                total["relaxed"] = relaxed
            result.append(total)
        return result

    def balances(self, db):
        return self._balances(db)

    def development_balances(self, db=None):
        if db is not None:
            return self._balances(db, development=True)
        with self.app.db() as conn:
            return self.development_balances(conn)

    def capability(self, config):
        resolver = getattr(self.app.bridge, "resolved_budget_capability", None)
        if callable(resolver):
            return resolver(config)
        from provider_bridge import ProviderBridge
        return ProviderBridge.budget_capability(config)

    @staticmethod
    def check_profile_limits(capability, payload_bytes, output_cap, features=None):
        features = features or {}
        for feature in ("streaming", "vision"):
            if features.get(feature) and not capability.get("supports_" + feature):
                raise TownError(403, "当前模型的流式或看图验证已失效，请重新验证后继续")
        max_request_bytes = capability.get("vision_max_request_bytes") if features.get("vision") else capability.get("max_request_bytes")
        if max_request_bytes is not None and payload_bytes > max_request_bytes:
            raise TownError(403, "本次请求超出已验证的输入范围，请缩短内容或重新验证模型用量适配")
        if capability.get("max_output_tokens") is not None and output_cap > capability["max_output_tokens"]:
            raise TownError(403, "本次输出上限超出已验证范围，请降低输出上限或重新验证模型用量适配")

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
        from provider_bridge import endpoint_identity
        endpoints = []
        for endpoint in result["endpoints"]:
            if (not isinstance(endpoint, dict) or not {"base_url", "model"} <= set(endpoint)
                    or set(endpoint) - {"base_url", "model", "protocol", "auth_type"}):
                raise TownError(400, "端点需明确服务地址、模型及所选协议")
            try:
                if not isinstance(endpoint["base_url"], str) or not endpoint["base_url"].strip():
                    raise ValueError("请填写允许调用的模型服务地址")
                normalized = endpoint_identity(dict(endpoint, model=text(endpoint["model"], "模型", 200)))
            except ValueError as exc:
                raise TownError(400, str(exc)) from None
            endpoints.append(normalized)
        result["endpoints"] = endpoints
        if result["enabled"] and (not result["strict"] or not endpoints or not result["residents"]):
            raise TownError(400, "自动训练需要严格网络模式、明确模型端点和居民范围")
        if result["enabled"]:
            if any(not self.capability(endpoint)["enforce_supported"] for endpoint in endpoints):
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
        from provider_bridge import request_url
        if purpose != "model" or not any(url == request_url(item) for item in config["endpoints"]):
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
            request_limits = self._request_limits(options, policy, self.development_config(db))
            development = request_limits["development"]
            prepared_limits = context.get("request_limits", {})
            if development and prepared_limits.get("development_revision", request_limits["development_revision"]) != request_limits["development_revision"]:
                raise TownError(409, "研发额度设置已变化，请重新发送")
            if agent in get_setting(db, "revoked_residents", []):
                raise TownError(403, "该居民已暂停，恢复需要原生确认")
            enforce = controlled or policy["strict"]
            if enforce:
                from provider_bridge import endpoint_identity
                capability = self.capability(config)
                if not capability["enforce_supported"]:
                    raise TownError(403, "当前协议、地址与模型尚无经过验证的用量上限适配档案；可用于兼容模式的普通聊天，受控研发、训练和严格模式需使用已适配组合")
                if not verify_events(db):
                    raise TownError(503, "成长档案校验失败，已阻止受控调用")
                if policy["paused"] or agent not in policy["residents"]:
                    raise TownError(403, "稽查署：居民调用权限已暂停或未授权")
                if controlled and not options.get("workbench") and not options.get("interaction") and not policy["enabled"]:
                    raise TownError(403, "成长署自动训练尚未启用")
                if endpoint_identity(config) not in [endpoint_identity(item) for item in policy["endpoints"]]:
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
            if type(output_cap) is not int or not 1 <= output_cap <= request_limits["max_output_tokens"]:
                raise TownError(409, "模型输出上限已变化，请重新发送")
            if enforce:
                self.check_profile_limits(capability, len(payload), output_cap, context.get("feature_requirements"))
            from provider_bridge import endpoint_identity
            provider_identity = endpoint_identity(config)
            from provider_features import input_bound
            input_tokens = input_bound(payload)
            limits = dict(input_tokens=input_tokens, output_tokens=output_cap,
                          total_tokens=input_tokens + output_cap)
            reserved = limits["total_tokens"]
            scope = "purpose='development'" if development else "purpose!='development'"
            totals = db.execute("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END),0), COUNT(*) FROM v2_calls WHERE agent_id=? AND day=? AND status NOT IN ('released','blocked') AND " + scope, (agent, admitted_day)).fetchone()
            lifetime = db.execute("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE charged END),0) FROM v2_calls WHERE agent_id=? AND " + scope, (agent,)).fetchone()[0]
            # One shared message used to cover four very different limits, so a
            # single oversized request looked like "today's budget is gone" and
            # raising the daily quota could never clear it.
            if enforce and not request_limits["relaxed"]:
                if reserved > policy["request_total_cap"]:
                    raise TownError(429, "本次请求需预留 %d，超过单次上限 %d；请提高单次上限或缩短本轮上下文" % (reserved, policy["request_total_cap"]))
                if lifetime + reserved > policy["lifetime_tokens"]:
                    raise TownError(429, "累计额度不足：已用 %d，上限 %d" % (lifetime, policy["lifetime_tokens"]))
                if totals[0] + reserved > policy["daily_tokens"]:
                    raise TownError(429, "今日额度不足：已用 %d，每日上限 %d" % (totals[0], policy["daily_tokens"]))
                if totals[1] >= policy["daily_calls"]:
                    raise TownError(429, "今日调用次数已用完：%d / %d" % (totals[1], policy["daily_calls"]))
            db.execute("INSERT INTO v2_calls(id,agent_id,day,purpose,endpoint,model,reserved,status,revision,created_at) VALUES (?,?,?,?,?,?,?,'reserved',?,?)",
                       (identity, agent, admitted_day, "development" if development else "interaction" if options.get("interaction") else "training" if controlled else "chat", config["base_url"], config["model"], reserved, policy["revision"], now()))
            event(db, "call_reserved", identity, {"agent_id": agent, "reserved": reserved, "enforce": enforce,
                  "protocol": config.get("protocol", "openai"),
                  "auth_type": config.get("auth_type", "api_key" if config.get("protocol") == "anthropic" else "bearer"),
                  "usage_limits": limits, "provider_identity": provider_identity,
                  "request_limits": request_limits})
            context["access_revision"] = get_setting(db, "access_revision", 0)
        context["call_id"] = identity
        context["enforce"] = enforce
        context["provider_identity"] = provider_identity
        context["provider_config"] = dict(provider_identity, mode=config.get("mode", "api"))
        context["usage_limits"] = limits
        context["payload_bytes"] = len(payload)
        context["request_limits"] = request_limits
        context["development_revision"] = request_limits["development_revision"]
        context["response_received"] = False
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
            if row["purpose"] == "development" and context.get("development_revision") != self.development_config(db)["revision"]:
                raise TownError(409, "发送前研发额度设置已变化，请重新发送")
            if context["enforce"]:
                capability = self.capability(context["provider_config"])
                if not capability["enforce_supported"]:
                    raise TownError(403, "发送前模型用量适配已过期或撤销，请重新验证")
                self.check_profile_limits(capability, context["payload_bytes"], context["usage_limits"]["output_tokens"], context.get("feature_requirements"))
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

    @staticmethod
    def response_violation(context, row, usage):
        if not row["sent"] or not (context.get("enforce") or context.get("options", {}).get("controlled")):
            return None
        known = {"missing_usage", "invalid_usage", "input_overrun", "output_overrun", "invalid_output_limit"}
        supplied = context.get("budget_violation")
        if supplied:
            return supplied if isinstance(supplied, str) and supplied in known else "invalid_usage"
        if not usage:
            # An HTTP/transport failure carries no successful response body to
            # judge. Retain its conservative charge without revoking a profile.
            if context.get("response_received") is False:
                return None
            return "missing_usage" if context.get("usage") is None else "invalid_usage"
        if not isinstance(context.get("usage"), dict) or "total_tokens" not in context["usage"]:
            return "invalid_usage"
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            return "invalid_usage"
        limits = context.get("usage_limits", {})
        if "input_tokens" in limits and usage["prompt_tokens"] > limits["input_tokens"]:
            return "input_overrun"
        if "output_tokens" in limits and usage["completion_tokens"] > limits["output_tokens"]:
            return "output_overrun"
        if usage["total_tokens"] > row["reserved"]:
            return "invalid_usage"
        return None

    def pause_response_violation(self, db, row, context, reason):
        # Never raise here: the usage charge and pause must commit together.
        # The worker observes the error only after this transaction returns.
        context["budget_violation"] = reason
        context.pop("reply", None)
        context["error"] = "模型用量未符合已验证的约定，本次回复未作为成果采用，受控调用已暂停；请重新验证模型用量适配并确认授权"
        if db.execute("SELECT 1 FROM v2_events WHERE kind='budget_contract_violation' AND subject=?", (row["id"],)).fetchone():
            return
        policy = self.config(db)
        policy.update(paused=True, revision=policy["revision"] + 1)
        setting(db, "growth_policy", policy)
        event(db, "budget_contract_violation", row["id"], dict(reason=reason,
            provider_identity=context.get("provider_identity", {}), policy_revision=policy["revision"]))

    def pause_overrun(self, db, row, charged):
        # Ordinary compatible chat retains the original shared-ledger overrun
        # brake even though it does not claim a verified response contract.
        if db.execute("SELECT 1 FROM v2_events WHERE kind='budget_overrun' AND subject=?", (row["id"],)).fetchone():
            return
        policy = self.config(db)
        if not policy["paused"]:
            policy.update(paused=True, revision=policy["revision"] + 1)
            setting(db, "growth_policy", policy)
        event(db, "budget_overrun", row["id"], {"charged": charged, "reserved": row["reserved"]})

    @staticmethod
    def response_metadata(raw):
        if not isinstance(raw, dict) or raw.get("protocol") not in ("openai", "anthropic"):
            return None
        reasons = ("stop", "length", "tool_calls", "function_call", "content_filter",
                   "end_turn", "stop_sequence", "max_tokens", "tool_use",
                   "model_context_window_exceeded", "pause_turn", "refusal", "other")
        reason = raw.get("stop_reason")
        clean = dict(protocol=raw["protocol"], stop_reason=reason if reason in reasons else "other")
        for key in ("requested_output_tokens", "text_chars", "thinking_chars"):
            value = raw.get(key)
            if type(value) is int and 0 <= value <= 1000000000:
                clean[key] = value
        return clean

    def settle(self, context):
        if not context.get("call_id"):
            return
        with self.app.lock, self.app.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM v2_calls WHERE id=?", (context["call_id"],)).fetchone()
            usage = self.usage(context.get("usage"))
            violation = self.response_violation(context, row, usage)
            policy = self.config(db)
            if context.get("enforce") and row["sent"] and (policy["paused"] or row["revision"] != policy["revision"]):
                context.pop("reply", None)
                context["error"] = "受控调用权限已暂停或变化，本次回复未作为成果采用"
            if row["purpose"] == "development" and row["sent"] and context.get("development_revision") != self.development_config(db)["revision"]:
                context.pop("reply", None)
                context["error"] = "研发额度设置已变化，本次回复未作为成果采用，请重新发送"
            if row["status"] != "reserved":
                if row["status"] == "unknown" and usage:
                    charged = usage["total_tokens"]
                    db.execute("UPDATE v2_calls SET charged=?,status='settled',usage=?,settled_at=? WHERE id=?", (charged, pack(usage), now(), row["id"]))
                    event(db, "usage_correction", row["id"], {"previous": row["charged"], "charged": charged, "day": row["day"], "source": "provider-worker"})
                if violation:
                    self.pause_response_violation(db, row, context, violation)
                if row["status"] == "unknown" and usage and usage["total_tokens"] > row["reserved"]:
                    self.pause_overrun(db, row, usage["total_tokens"])
                return
            charged = usage["total_tokens"] if usage else row["reserved"] if row["sent"] else 0
            status = "settled" if usage else "unknown" if row["sent"] else "released"
            metadata = self.response_metadata(context.get("response_metadata"))
            if row["sent"] and metadata:
                event(db, "model_response_observed", row["id"], metadata)
            db.execute("UPDATE v2_calls SET charged=?,status=?,usage=?,settled_at=? WHERE id=?", (charged, status, pack(usage), now(), row["id"]))
            event(db, "call_settled", row["id"], {"charged": charged, "status": status, "day": row["day"]})
            if violation:
                self.pause_response_violation(db, row, context, violation)
                if charged > row["reserved"]:
                    event(db, "budget_overrun", row["id"], {"charged": charged, "reserved": row["reserved"]})
            elif charged > row["reserved"]:
                self.pause_overrun(db, row, charged)

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
            options = context.get("options", {})
            purpose = "development" if options.get("controlled") and options.get("workbench") else "interaction" if options.get("interaction") else "training" if options.get("controlled") else "chat"
            event(db, "call_blocked", context["agent_id"], {"purpose": purpose, "reason": context.get("error", "调用未获准"), "model": config.get("model")})
