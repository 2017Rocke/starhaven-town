"""User-configurable, text-only model bridge using Python's standard library."""
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

MAX_RESPONSE = 1_000_000


class SettingsError(ValueError):
    pass


def normalize_base_url(value):
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
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, "", ""))


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
        self._config_error = False
        self._config = self._load()

    def _load(self):
        fallback = {"mode": "codex" if self.codex.executable else "api", "base_url": "", "model": ""}
        if not self.path.is_file():
            return fallback
        try:
            if self.path.stat().st_size > 10000:
                raise ValueError()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._validate(raw)
        except (OSError, ValueError, TypeError):
            self._config_error = True
            return {"mode": "api", "base_url": "", "model": ""}

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or set(data) - {"mode", "base_url", "model", "api_key"}:
            raise SettingsError("模型设置包含不支持的字段。")
        if not {"mode", "base_url", "model"}.issubset(data):
            raise SettingsError("请提供连接方式、服务地址和模型名称。")
        mode = data["mode"]
        if mode not in ("api", "codex"):
            raise SettingsError("请选择有效的连接方式。")
        if not isinstance(data["base_url"], str):
            raise SettingsError("请输入有效的模型服务地址。")
        base_url = normalize_base_url(data["base_url"]) if data["base_url"] else ""
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
        return {"mode": mode, "base_url": base_url, "model": model}

    def settings(self):
        with self._lock:
            result = dict(self._config)
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
                    os.chmod(str(temporary), 0o600)
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
        try:
            payload = json.dumps({
                "model": config["model"], "stream": False,
                "messages": [
                    {"role": "system", "content": "你是星栖小镇中的中文角色对话助手。仅生成文本回复，没有工具、外部执行或工作调度能力。遵守传入的角色规则，不能虚构已执行的工作。"},
                    {"role": "user", "content": prompt},
                ],
            }, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "StarhavenTown/1.1"}
            if key:
                if any(ord(ch) < 33 or ord(ch) > 126 for ch in key) or len(key) > 8192:
                    raise RuntimeError("密钥格式无效，请重新设置。")
                headers["Authorization"] = "Bearer " + key
            request = urllib.request.Request(config["base_url"] + "/chat/completions", data=payload, headers=headers, method="POST")
            opener = self._opener_factory() if self._opener_factory else urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
            if context["cancel"].is_set():
                raise RuntimeError("已取消这次回复。")
            response = opener.open(request, timeout=self.socket_timeout)
            context["response"] = response
            if context["cancel"].is_set():
                raise RuntimeError("已取消这次回复。")
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
            result = json.loads(raw.decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("模型没有返回文字回复，请确认该模型支持对话。")
            if len(content) > 100000:
                raise RuntimeError("模型回复过长，请缩小问题范围后重试。")
            context["reply"] = content.strip().replace(key, "[密钥已隐藏]") if key else content.strip()
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                context["error"] = "模型地址发生重定向，已停止发送。请填写服务商提供的最终 API 地址。"
            elif exc.code in (401, 403):
                context["error"] = "模型服务拒绝了连接，请检查密钥与模型访问权限。"
            elif exc.code == 429:
                context["error"] = "模型服务暂时限流或额度不足，请检查服务账户后重试。"
            else:
                context["error"] = "模型服务暂时无法完成请求，请检查服务地址和模型名称。"
            exc.close()
        except (urllib.error.URLError, OSError, TimeoutError):
            context["error"] = "模型连接超时或不可用，请检查服务地址、网络和本地模型是否启动。"
        except (ValueError, KeyError, IndexError, TypeError):
            context["error"] = "模型服务返回了无法识别的内容，请确认支持 OpenAI 兼容对话接口。"
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
            context["done"].set()
            with self._lock:
                self._pending.discard(context["thread"])

    def generate(self, prompt, cancel_event):
        with self._lock:
            if self._closed or cancel_event.is_set():
                raise RuntimeError("已取消这次回复。")
            config = dict(self._config)
            if config["mode"] == "codex":
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
                context = {"cancel": threading.Event(), "done": threading.Event()}
                worker = threading.Thread(target=self._perform_request, args=(config, key, prompt, context), daemon=True, name="town-model-request")
                context["thread"] = worker
                worker.town_context = context
                self._pending.add(worker)
                worker.start()
        if use_codex:
            return self.codex.generate(prompt, cancel_event)
        deadline = time.monotonic() + self.timeout
        while not context["done"].wait(.05):
            if cancel_event.is_set() or self._closed or time.monotonic() >= deadline:
                context["cancel"].set()
                self._abort_response(context)
                if cancel_event.is_set() or self._closed:
                    raise RuntimeError("已取消这次回复。")
                raise RuntimeError("这次模型回复等待较久，请稍后重试。")
        if cancel_event.is_set() or self._closed:
            raise RuntimeError("已取消这次回复。")
        if context.get("error"):
            raise RuntimeError(context["error"])
        return context["reply"]

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
