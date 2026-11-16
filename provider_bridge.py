"""User-configurable model bridge with verified text, streaming and image support."""
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from credential_store import CredentialStore, CredentialError
from model_bridge import CodexBridge
from provider_features import FeatureProfiles, clean_images, add_images, input_bound, text_body
from provider_streaming import read_stream

MAX_RESPONSE = 1_000_000


class SettingsError(ValueError):
    pass


def _protocol_options(config):
    protocol = config.get("protocol", "openai")
    if protocol not in ("openai", "anthropic"):
        raise SettingsError("请选择支持的模型接口协议。")
    auth_type = config.get("auth_type", "api_key" if protocol == "anthropic" else "bearer")
    if auth_type not in ("api_key", "bearer") or protocol == "openai" and auth_type != "bearer":
        raise SettingsError("认证方式与所选模型接口协议不匹配。")
    return protocol, auth_type


def normalize_base_url(value, protocol="openai"):
    if protocol not in ("openai", "anthropic"):
        raise SettingsError("请选择支持的模型接口协议。")
    if not isinstance(value, str) or len(value) > 1000:
        raise SettingsError("请输入有效的模型服务地址。")
    value = value.strip()
    if not value or any(ord(ch) < 33 or ord(ch) > 126 for ch in value) or "\\" in value:
        raise SettingsError("模型服务地址需要是有效的网址。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SettingsError("模型服务地址格式无效。") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SettingsError("模型服务地址不能包含账号、密码、查询参数或片段。")
    if not parsed.hostname or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname):
        raise SettingsError("模型服务地址的主机名无效。")
    if port is not None and not 1 <= port <= 65535:
        raise SettingsError("模型服务端口无效。")
    if parsed.scheme not in ("https", "http"):
        raise SettingsError("模型服务需要使用 HTTPS；本机模型可使用 HTTP。")
    if parsed.scheme == "http" and parsed.hostname.lower() not in ("localhost", "127.0.0.1"):
        raise SettingsError("只有 localhost 或 127.0.0.1 的本机模型可使用 HTTP。")
    path = parsed.path.rstrip("/")
    if protocol == "anthropic":
        if path.endswith("/v1/messages"):
            path = path[:-len("/v1/messages")]
        elif path.endswith("/v1"):
            path = path[:-len("/v1")]
    elif path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, "", ""))


def request_url(config):
    protocol, unused = _protocol_options(config)
    base = normalize_base_url(config.get("base_url", ""), protocol)
    return base + ("/v1/messages" if protocol == "anthropic" else "/chat/completions")


def endpoint_identity(config):
    """Keep old OpenAI grants stable while binding new protocols and credentials."""
    protocol, auth_type = _protocol_options(config)
    base = config.get("base_url", "")
    model = config.get("model", "")
    if not isinstance(model, str):
        raise SettingsError("模型名称格式无效。")
    identity = {"base_url": normalize_base_url(base, protocol) if base else "", "model": model.strip()}
    if protocol == "anthropic":
        identity.update(protocol=protocol, auth_type=auth_type)
    return identity


def _anthropic_usage(raw):
    if not isinstance(raw, dict):
        return None
    values = [raw.get("input_tokens"), raw.get("output_tokens"),
              raw.get("cache_creation_input_tokens", 0), raw.get("cache_read_input_tokens", 0)]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    prompt, completion = values[0] + values[2] + values[3], values[1]
    if prompt + completion > 1000000000:
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _response_usage_violation(usage, payload, output_cap, *, missing=False):
    """Check the actual response against the limits of the bytes we sent.

    Keep this separate from settlement: even a violating response may contain
    reported usage that still needs to be charged to the shared ledger.
    """
    if missing:
        return "missing_usage"
    if not isinstance(usage, dict):
        return "invalid_usage"
    counts = [usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
    if any(type(value) is not int or value < 0 for value in counts):
        return "invalid_usage"
    prompt, completion, total = counts
    if total != prompt + completion or total > 1000000000:
        return "invalid_usage"
    if type(output_cap) is not int or output_cap < 1:
        return "invalid_output_limit"
    if prompt > input_bound(payload):
        return "input_overrun"
    if completion > output_cap:
        return "output_overrun"
    return None


def _openai_text(result):
    if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
        raise ValueError("invalid_chat_response")
    choice = result["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("invalid_chat_message")
    reason = choice.get("finish_reason")
    if reason == "length":
        raise RuntimeError("模型回复达到输出上限，最终文字可能不完整；推理模型的推理也会占用输出额度，请提高单次最大输出 Token 或缩小问题后重试。")
    if reason in ("tool_calls", "function_call"):
        raise RuntimeError("模型要求调用工具，当前文本连接不执行工具；请改为直接文字回复。")
    if reason == "content_filter":
        raise RuntimeError("模型服务因内容过滤中止了回复，请调整问题后重试。")
    message = choice["message"]
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    if (not isinstance(content, str) or not content.strip()) and isinstance(reasoning, str) and reasoning.strip():
        raise RuntimeError("模型仅返回了推理内容，尚未生成最终文字回复；请提高单次最大输出 Token 或缩小问题后重试。")
    if "content" not in message:
        raise ValueError("missing_chat_content")
    return content


def _anthropic_text(result, output_limit=None):
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        raise ValueError("invalid_messages_response")
    reason = result.get("stop_reason")
    if reason == "max_tokens":
        limit = "（%d Token）" % output_limit if type(output_limit) is int and output_limit > 0 else ""
        raise RuntimeError("模型回复达到本次输出上限" + limit + "，推理也计入该额度，结果未完整生成；请提高本次输出额度或缩小任务后重试。")
    if reason == "model_context_window_exceeded":
        raise RuntimeError("模型服务的上下文容量已满，结果未完整生成；请减少本次资料或分批处理，提高输出额度无法扩大模型上下文。")
    if reason == "tool_use":
        raise RuntimeError("模型要求调用工具，当前文本连接不执行工具；请改为直接文字回复。")
    if reason not in ("end_turn", "stop_sequence"):
        raise RuntimeError("模型未完成直接文字回复，请检查模型能力或调整问题后重试。")
    texts = []
    for block in result["content"]:
        if not isinstance(block, dict):
            raise ValueError("invalid_messages_content")
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                raise ValueError("invalid_messages_text")
            if block["text"].strip():
                texts.append(block["text"])
    return "\n\n".join(texts)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never let an API key follow an untrusted Location header.
        return None


class ProviderBridge:
    def __init__(self, workdir, data_dir, credentials=None, opener_factory=None):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "provider.json"
        self.credentials = credentials or CredentialStore(self.data_dir)
        self.codex = CodexBridge(workdir=Path(workdir))
        self._lock = threading.RLock()
        self._pending = set()
        self._closed = False
        self.timeout = 150
        self.socket_timeout = 20
        self._opener_factory = opener_factory
        self._security = None
        self._budget = None
        self._config_error = False
        self._config = self._load()
        from provider_verification import LocalBudgetProfiles
        self._local_budget_profiles = LocalBudgetProfiles(self.data_dir)
        self._feature_profiles = FeatureProfiles(self.data_dir)

    def set_security_office(self, security):
        """Attach the local audit service; it never receives authentication headers."""
        self._security = security

    def set_budget(self, budget):
        self._budget = budget

    # Verified strict-budget profiles. Each entry is a claim that, for this exact
    # endpoint and model set, a recorded probe established all four properties the
    # reservation `len(payload) + 512 + max_tokens` depends on:
    #   (a) max_tokens is honoured literally, so completion_tokens <= max_tokens;
    #   (b) usage returns integer prompt/completion/total with total == prompt + completion;
    #   (c) any hidden reasoning is counted inside completion_tokens, not on top of it;
    #   (d) the tokenizer keeps prompt_tokens <= the complete request's UTF-8 bytes.
    # `socket_timeout` is per profile because a non-streaming reasoning reply sends
    # its headers early and the body only after the whole answer exists: at a 4096
    # output cap a reasoning model routinely needs 15-40s, far past the 20s that
    # suits a short non-reasoning reply.
    # An arbitrary compatible server or model name does not inherit either claim.
    VERIFIED_PROFILES = (
        dict(profile="openai-text-bpe-v1",
             base_url="https://api.openai.com/v1",
             models=frozenset({"gpt-4o-mini", "gpt-4o-mini-2024-07-18", "gpt-4o",
                               "gpt-4o-2024-08-06", "gpt-4o-2024-11-20"}),
             socket_timeout=20),
        dict(profile="deepseek-reasoning-v1",
             base_url="https://api.deepseek.com",
             models=frozenset({"deepseek-flash", "deepseek-v4-pro"}),
             socket_timeout=120),
    )

    @staticmethod
    def budget_capability(config):
        mode = config.get("mode", "api")
        base_url, model = config.get("base_url"), config.get("model")
        if mode == "api" and config.get("protocol", "openai") == "openai" and config.get("auth_type", "bearer") == "bearer":
            for entry in ProviderBridge.VERIFIED_PROFILES:
                if base_url == entry["base_url"] and model in entry["models"]:
                    return dict(profile=entry["profile"], enforce_supported=True,
                                input_bound="complete-request-utf8-bytes+512",
                                output_parameter="max_tokens",
                                socket_timeout=entry["socket_timeout"])
        return dict(profile="unverified-compatible", enforce_supported=False,
                    input_bound=None, output_parameter=None, socket_timeout=None)

    def _load(self):
        fallback = {"mode": "codex" if self.codex.executable else "api", "base_url": "", "model": "", "protocol": "openai", "auth_type": "bearer"}
        if not self.path.is_file():
            return fallback
        try:
            if self.path.stat().st_size > 10000:
                raise ValueError()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._validate(raw)
        except (OSError, ValueError, TypeError):
            self._config_error = True
            return {"mode": "api", "base_url": "", "model": "", "protocol": "openai", "auth_type": "bearer"}

    def resolved_budget_capability(self, config):
        """Local probe evidence is independent from native network/source grants."""
        builtin = self.budget_capability(config)
        result = builtin if builtin["enforce_supported"] else self._local_budget_profiles.capability(config) or builtin
        return dict(result, **self._feature_profiles.capability(config))

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or set(data) - {"mode", "base_url", "model", "api_key", "protocol", "auth_type"}:
            raise SettingsError("模型设置包含不支持的字段。")
        if not {"mode", "base_url", "model"}.issubset(data):
            raise SettingsError("请提供连接方式、服务地址和模型名称。")
        mode = data["mode"]
        if mode not in ("api", "codex"):
            raise SettingsError("请选择有效的连接方式。")
        if not isinstance(data["base_url"], str):
            raise SettingsError("请输入有效的模型服务地址。")
        protocol, auth_type = _protocol_options(data)
        base_url = normalize_base_url(data["base_url"], protocol) if data["base_url"] else ""
        model = data["model"]
        if not isinstance(model, str) or len(model) > 200 or any(ord(ch) < 32 for ch in model):
            raise SettingsError("模型名称格式无效。")
        model = model.strip()
        if mode == "api" and (not base_url or not model):
            raise SettingsError("请填写服务地址和服务商提供的模型名称。")
        if "api_key" in data:
            key = data["api_key"]
            if not isinstance(key, str) or len(key) > 8192 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
                raise SettingsError("密钥格式无效，请确认没有空格或换行。")
            if key and not base_url:
                raise SettingsError("保存密钥前请先填写模型服务地址。")
        return {"mode": mode, "base_url": base_url, "model": model, "protocol": protocol, "auth_type": auth_type}

    def settings(self):
        with self._lock:
            result = dict(self._config)
            result["budget_capability"] = self.resolved_budget_capability(self._config)
            try:
                result["has_api_key"] = bool(self.credentials.get(result["base_url"]))
                key_error = ""
            except CredentialError as exc:
                result["has_api_key"] = False
                key_error = str(exc)
            result.update(
                key_storage=self.credentials.storage,
                can_store_api_key=self.credentials.supports_persistence,
                configured=result["mode"] == "codex" or bool(result["base_url"] and result["model"]),
                storage_message=key_error or (
                    "当前使用 STARHAVEN_API_KEY 环境变量中的密钥；界面不会显示密钥。" if os.environ.get("STARHAVEN_API_KEY", "").strip() else
                    "密钥保存在 macOS 钥匙串中，不会写入项目文件。" if self.credentials.storage == "keychain" else
                    "密钥保存在 Windows 凭据管理器中，不会写入项目文件。" if self.credentials.storage == "credential-manager" else
                    "此系统使用 STARHAVEN_API_KEY 环境变量，不在文件中保存密钥；本地模型可不填密钥。"),
            )
            if self._config_error:
                result["storage_message"] = "模型设置文件无法读取，请重新保存设置。"
            return result

    def save_settings(self, data):
        config = self._validate(data)
        with self._lock:
            if self._closed:
                raise SettingsError("小镇正在关闭，请稍后再试。")
            if self._pending:
                raise SettingsError("仍有模型连接在结束，请稍后再保存设置。")
            if "api_key" in data and config["base_url"]:
                try:
                    self.credentials.set(config["base_url"], data["api_key"])
                except CredentialError as exc:
                    raise SettingsError(str(exc)) from exc
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(self.data_dir), prefix=".provider-", delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        os.chmod(str(temporary), 0o600)
                    except OSError:
                        pass
                    json.dump(config, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(str(temporary), str(self.path))
            except OSError as exc:
                raise SettingsError("无法保存模型设置，请检查本机存储空间和文件权限。") from exc
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            self._config = config
            self._config_error = False
            return self.settings()

    def probe(self):
        settings = self.settings()
        if settings["mode"] == "codex":
            return self.codex.probe()
        return {
            "available": settings["configured"], "authenticated": False,
            "label": "自选模型服务", "status": "configured" if settings["configured"] else "unavailable",
            "message": ("已保存模型连接；发送消息即可检查实际可用性。" if settings["configured"] else "欢迎来到小镇，请先在设置中连接你的模型服务。"),
        }

    @staticmethod
    def _abort_response(context):
        # A socket shutdown wakes a blocking read without waiting for response.close's lock.
        response = context.get("response")
        try:
            sock = response.fp.raw._sock
            sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass

    def _perform_request(self, config, key, prompt, context):
        response = None
        audit = None
        status_code = None
        try:
            protocol, auth_type = _protocol_options(config)
            options = context.get("options", {})
            capability = self.resolved_budget_capability(config)
            images = clean_images(options.get("images", []))
            if images and not capability.get("supports_vision"):
                raise RuntimeError("当前模型尚未通过看图验证。资料已保存，请配置支持视觉的模型并验证后继续；图片尚未被理解。")
            use_stream = bool(options.get("stream") and capability.get("supports_streaming"))
            context["feature_requirements"] = dict(streaming=use_stream, vision=bool(images))
            system = "你是星栖小镇中的中文角色对话助手。仅生成文本回复，没有工具、外部执行或工作调度能力。遵守传入的角色规则，不能虚构已执行的工作。"
            body = {"model": config["model"], "stream": use_stream,
                    "messages": [{"role": "user", "content": prompt}]}
            if self._budget is not None:
                limits = context.get("request_limits")
                if limits is None:
                    resolver = getattr(self._budget, "request_limits", None)
                    limits = resolver(context.get("options", {})) if callable(resolver) else self._budget.config()
                    context["request_limits"] = limits
                body["max_tokens"] = limits["max_output_tokens"]
                if "output_tokens" in options:
                    if type(options["output_tokens"]) is not int or options["output_tokens"] < 1:
                        raise RuntimeError("本步骤输出额度无效。")
                    body["max_tokens"] = min(body["max_tokens"], options["output_tokens"])
            if protocol == "anthropic":
                body["system"] = system
                body.setdefault("max_tokens", 1024)
            else:
                body["messages"].insert(0, {"role": "system", "content": system})
                if use_stream:
                    body["stream_options"] = {"include_usage": True}
            body = add_images(body, images, protocol)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if self._budget is not None:
                self._budget.admit(config, payload, context)
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if use_stream else "application/json", "User-Agent": "StarhavenTown/1.1"}
            if protocol == "anthropic":
                headers["anthropic-version"] = "2023-06-01"
            if key:
                if any(ord(ch) < 33 or ord(ch) > 126 for ch in key) or len(key) > 8192:
                    raise RuntimeError("密钥格式无效，请重新设置。")
                if auth_type == "api_key":
                    headers["x-api-key"] = key
                else:
                    headers["Authorization"] = "Bearer " + key
            request = urllib.request.Request(request_url(config), data=payload, headers=headers, method="POST")
            opener = self._opener_factory() if self._opener_factory else urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
            if context["cancel"].is_set():
                raise RuntimeError("已取消这次回复。")
            if self._security is not None:
                try:
                    audit = self._security.network_check(
                        url=request.full_url, method="POST", purpose="model",
                        agent_id=context.get("agent_id", "unassigned"),
                        allowed_hosts=(urlsplit(config["base_url"]).hostname,),
                        payload_text=json.dumps(text_body(body), ensure_ascii=False) if images else payload.decode("utf-8"),
                        credential_in_payload=bool(key and (key in prompt or key in config["model"])),
                        allow_http_loopback=True)
                except Exception:
                    raise RuntimeError("稽查署暂时无法记录请求，已暂停发送，请检查治安档案。") from None
                if not isinstance(audit, dict) or type(audit.get("allowed")) is not bool:
                    audit = None
                    raise RuntimeError("稽查署返回了无效的检查结果，已暂停发送。")
                if audit["allowed"] is not True:
                    raise RuntimeError("稽查署已暂停本次请求：" + audit.get("reason", "请求未通过规则检查。"))
            if context["cancel"].is_set() or self._closed:
                raise RuntimeError("已取消这次回复。")
            before_dispatch = context.get("options", {}).get("before_dispatch")
            if callable(before_dispatch):
                before_dispatch()
            if self._budget is not None:
                self._budget.dispatch(context)
            progress = options.get("on_progress")
            if callable(progress):
                progress(dict(phase="waiting", text_chars=0))
            socket_timeout = self.resolved_budget_capability(config)["socket_timeout"] or self.socket_timeout
            if not (context.get("options", {}).get("controlled") and context.get("options", {}).get("workbench")):
                socket_timeout = min(socket_timeout, 120)
            response = opener.open(request, timeout=socket_timeout)
            status_code = getattr(response, "status", None)
            context["response"] = response
            if context["cancel"].is_set():
                raise RuntimeError("已取消这次回复。")
            stream_metadata = None
            if use_stream:
                content_type = response.headers.get('Content-Type', '') if getattr(response, 'headers', None) is not None else ''
                if 'text/event-stream' not in content_type.lower():
                    raise RuntimeError("模型服务没有返回已约定的流式数据，请重新验证流式兼容性。")
                result, stream_metadata = read_stream(response, protocol, context["cancel"], progress)
            else:
                raw = bytearray()
                while len(raw) <= MAX_RESPONSE:
                    if context["cancel"].is_set():
                        raise RuntimeError("已取消这次回复。")
                    chunk = response.read(min(65536, MAX_RESPONSE + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise RuntimeError("模型回复过长，请缩小问题范围后重试。")
            if context["cancel"].is_set() or self._closed:
                raise RuntimeError("已取消这次回复。")
            # A timeout after headers is still a transport failure, not evidence
            # that a complete provider response omitted its usage receipt.
            context["response_received"] = True
            if not use_stream:
                result = json.loads(raw.decode("utf-8"))
            context["response_metadata"] = self.response_metadata(result, protocol, body.get("max_tokens"))
            if stream_metadata:
                context["response_metadata"].update(stream_metadata)
            usage = result.get("usage") if isinstance(result, dict) else None
            context["usage"] = _anthropic_usage(usage) if protocol == "anthropic" else usage
            if context.get("enforce") or context.get("options", {}).get("controlled"):
                violation = _response_usage_violation(context["usage"], payload, body.get("max_tokens"), missing=usage is None)
                if violation:
                    context["budget_violation"] = violation
                    reason = {
                        "missing_usage": "模型服务未返回用量，无法确认本次调用符合预算上限",
                        "invalid_usage": "模型服务返回的用量无效或总量不一致，无法确认本次调用符合预算上限",
                        "invalid_output_limit": "本次请求没有可核验的输出上限",
                        "input_overrun": "模型服务报告的输入用量超过本次请求的已验证上限",
                        "output_overrun": "模型服务报告的输出用量超过本次请求设置的上限",
                    }[violation]
                    raise RuntimeError(reason + "；本次结果未作为受控成果接收，请检查模型预算适配。")
            content = _anthropic_text(result, body.get("max_tokens")) if protocol == "anthropic" else _openai_text(result)
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("模型服务已返回响应，但未提供最终文字回复；请检查模型输出设置后重试。")
            if len(content) > 100000:
                raise RuntimeError("模型回复过长，请缩小问题范围后重试。")
            if context["cancel"].is_set() or self._closed:
                raise RuntimeError("已取消这次回复。")
            current_features = self._feature_profiles.capability(config)
            if any(required and not current_features.get("supports_" + feature)
                   for feature, required in context["feature_requirements"].items()):
                raise RuntimeError("模型功能验证已变化，本次结果未采用，请重新验证后继续。")
            context["reply"] = content.strip().replace(key, "[密钥已隐藏]") if key else content.strip()
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            if exc.code in (301, 302, 303, 307, 308):
                context["error"] = "模型地址发生重定向，已停止发送。请填写服务商提供的最终 API 地址。"
            elif exc.code == 401:
                context["error"] = "模型服务认证失败（HTTP 401），请检查密钥与所选认证方式。"
            elif exc.code == 403:
                context["error"] = "模型服务拒绝访问（HTTP 403），请检查模型权限或网关访问限制。"
            elif exc.code == 404:
                context["error"] = "模型接口或模型不存在（HTTP 404），请检查协议、服务地址和模型名称。"
            elif exc.code == 429:
                context["error"] = "模型服务暂时限流或额度不足，请检查服务账户后重试。"
            elif exc.code == 504:
                context["error"] = "模型网关超时（HTTP 504），服务提前结束了请求；已完成步骤已保留，可继续当前步骤或缩小本步资料。"
            elif exc.code in (502, 503):
                context["error"] = "模型服务暂时不可用（HTTP %d），已完成步骤已保留，请稍后继续。" % exc.code
            else:
                context["error"] = "模型服务暂时无法完成请求，请检查服务地址和模型名称。"
            exc.close()
        except (urllib.error.URLError, OSError, TimeoutError):
            context["error"] = "模型连接超时或不可用，请检查服务地址、网络和本地模型是否启动。"
        except (ValueError, KeyError, IndexError, TypeError):
            context["error"] = "模型服务返回了无法识别的内容，请确认所选协议与服务接口一致。"
        except RuntimeError as exc:
            context["error"] = str(exc)
        except Exception:
            context["error"] = "模型连接暂时遇到问题，请检查配置后重试。"
        finally:
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            if audit is not None and audit.get("allowed") and self._security is not None:
                try:
                    self._security.network_result(audit, ok="reply" in context, status_code=status_code,
                                                  error="请求未完成" if "reply" not in context else None)
                except Exception:
                    context.pop("reply", None)
                    context["error"] = "请求结果未能写入稽查档案，请检查治安系统。"
            if self._budget is not None:
                try:
                    self._budget.settle(context)
                    if not context.get("call_id"):
                        self._budget.blocked(config, context)
                except Exception:
                    context.pop("reply", None)
                    context["error"] = "用量结算未完成，预留额度继续保留，请检查本地存储。"
                if context.get("budget_violation"):
                    try:
                        if config.get("protocol") == "anthropic":
                            self._local_budget_profiles.revoke(config, context["budget_violation"])
                        self._feature_profiles.revoke(config)
                    except Exception:
                        context.pop("reply", None)
                        context["error"] = "本机用量验证撤销记录未完成，受控调用保持暂停，请检查本地存储。"
            context["done"].set()
            with self._lock:
                self._pending.discard(context["thread"])

    def generate(self, prompt, cancel_event):
        return self.generate_for_agent(prompt, cancel_event, "unassigned")

    def generate_for_agent(self, prompt, cancel_event, agent_id):
        return self.generate_result(prompt, cancel_event, agent_id)["text"]

    @staticmethod
    def response_metadata(result, protocol, output_cap):
        """Only bounded counts and stop enums; no final text or private reasoning."""
        known = {"stop", "length", "tool_calls", "function_call", "content_filter", "end_turn",
                 "stop_sequence", "max_tokens", "tool_use", "model_context_window_exceeded", "pause_turn", "refusal"}
        stop, text_chars, thinking_chars = None, 0, 0
        if isinstance(result, dict):
            if protocol == "anthropic":
                stop = result.get("stop_reason")
                blocks = result.get("content")
                for block in blocks if isinstance(blocks, list) else []:
                    if not isinstance(block, dict):
                        continue
                    text_value = block.get("text") if block.get("type") == "text" else None
                    thinking_value = block.get("thinking")
                    text_chars += len(text_value) if isinstance(text_value, str) else 0
                    thinking_chars += len(thinking_value) if isinstance(thinking_value, str) else 0
            else:
                choices = result.get("choices")
                choice = choices[0] if isinstance(choices, list) and choices else {}
                if isinstance(choice, dict):
                    stop = choice.get("finish_reason")
                    message = choice.get("message")
                    if isinstance(message, dict):
                        for name, target in (("content", "text"), ("reasoning_content", "thinking")):
                            value = message.get(name)
                            if isinstance(value, str):
                                if target == "text": text_chars = len(value)
                                else: thinking_chars = len(value)
        return dict(protocol=protocol, stop_reason=stop if isinstance(stop, str) and stop in known else "other",
                    requested_output_tokens=output_cap if type(output_cap) is int else 0,
                    text_chars=text_chars, thinking_chars=thinking_chars)

    def request_timeout(self, config, options):
        if options.get("controlled") and options.get("workbench"):
            provider_timeout = self.resolved_budget_capability(config).get("socket_timeout") or self.socket_timeout
            return max(self.timeout, provider_timeout + 30)
        return self.timeout

    def generate_result(self, prompt, cancel_event, agent_id, options=None):
        options = options or {}
        with self._lock:
            if self._closed or cancel_event.is_set():
                raise RuntimeError("已取消这次回复。")
            config = dict(self._config)
            if config["mode"] == "codex":
                if self._budget is not None:
                    external_id = self._budget.external(options, agent_id)
                else:
                    external_id = None
                use_codex = True
            else:
                use_codex = False
                if not config["base_url"] or not config["model"]:
                    raise RuntimeError("请先在设置中填写模型服务地址和模型名称。")
                if len(self._pending) >= 2:
                    raise RuntimeError("先前的模型连接仍在结束，请稍后再试。")
                try:
                    key = self.credentials.get(config["base_url"])
                except CredentialError as exc:
                    raise RuntimeError(str(exc)) from exc
                context = {"cancel": threading.Event(), "done": threading.Event(), "agent_id": agent_id, "options": options}
                resolver = getattr(self._budget, "request_limits", None)
                if callable(resolver):
                    context["request_limits"] = resolver(options)
                worker = threading.Thread(target=self._perform_request, args=(config, key, prompt, context), daemon=True, name="town-model-request")
                context["thread"] = worker
                worker.town_context = context
                self._pending.add(worker)
                worker.start()
        if use_codex:
            if self._security is not None:
                try:
                    self._security.external_notice(agent_id=agent_id, purpose="codex",
                        message="本次对话使用本机 Codex；小镇无法核验其实际网络目的地、请求与重定向，仅记录外部调用边界。")
                except Exception:
                    raise RuntimeError("稽查署暂时无法记录外部调用，已暂停对话，请检查治安档案。") from None
            return dict(text=self.codex.generate(prompt, cancel_event), usage=None, call_id=external_id, provider="codex", usage_quality="unmetered")
        deadline = time.monotonic() + self.request_timeout(config, options)
        while not context["done"].wait(.05):
            if cancel_event.is_set() or context["cancel"].is_set() or self._closed or time.monotonic() >= deadline:
                was_cancelled = cancel_event.is_set() or context["cancel"].is_set() or self._closed
                context["cancel"].set()
                self._abort_response(context)
                if was_cancelled:
                    raise RuntimeError("已取消这次回复。")
                raise RuntimeError("这次模型回复等待较久，请稍后重试。")
        if cancel_event.is_set() or context["cancel"].is_set() or self._closed:
            raise RuntimeError("已取消这次回复。")
        if context.get("error"):
            raise RuntimeError(context["error"])
        return dict(text=context["reply"], usage=context.get("usage"), call_id=context.get("call_id"), provider="api", model=config["model"],
                    usage_quality="reported" if self._budget and self._budget.usage(context.get("usage")) else "unknown")

    def cancel_for_agent(self, agent=None):
        with self._lock:
            for worker in self._pending:
                context = worker.town_context
                if agent is None or context["agent_id"] == agent:
                    context["cancel"].set()
                    self._abort_response(context)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._pending)
            for worker in pending:
                worker.town_context["cancel"].set()
                self._abort_response(worker.town_context)
        self.codex.close()
        deadline = time.monotonic() + 2
        for worker in pending:
            worker.join(max(0, deadline - time.monotonic()))
