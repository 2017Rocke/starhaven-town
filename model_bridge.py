"""Use the locally authenticated Codex CLI for text-only character conversations."""
from pathlib import Path
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time


class CodexBridge:
    # Do not give a casual character conversation control of apps or a shell.
    DISABLED_FEATURES = (
        "apps", "plugins", "hooks", "shell_tool", "unified_exec",
        "browser_use", "browser_use_external", "browser_use_full_cdp_access",
        "computer_use", "code_mode_host", "multi_agent", "multi_agent_v2",
        "in_app_browser", "in_app_local_automation", "sleep_tool",
        "image_generation", "skill_search", "memories", "view_image",
    )

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.executable = shutil.which("codex")
        if not self.executable:
            for candidate in ("/usr/local/bin/codex", "/opt/homebrew/bin/codex"):
                if Path(candidate).is_file():
                    self.executable = candidate
                    break
        self._probe_lock = threading.Lock()
        self._probe_at = 0.0
        self._probe_result = None
        self._config_lock = threading.Lock()
        self._config_at = 0.0
        self._extra_args = []
        self._process_lock = threading.Lock()
        self._processes = set()
        self._closed = False
        self.timeout = 150

    def probe(self):
        with self._probe_lock:
            if self._probe_result and time.monotonic() - self._probe_at < 30:
                return dict(self._probe_result)
            result = {
                "available": bool(self.executable), "authenticated": False,
                "label": "Codex 本机对话", "status": "unavailable",
                "message": "还没有找到本机 Codex 客户端。",
            }
            if self.executable:
                try:
                    proc = subprocess.run(
                        [self.executable, "login", "status"], capture_output=True,
                        text=True, timeout=10, cwd=str(self.workdir),
                    )
                    # Never send login output (which can contain masked credentials) to the UI.
                    logged_in = proc.returncode == 0 and "logged in" in (
                        proc.stdout + proc.stderr
                    ).lower()
                    result.update(
                        authenticated=logged_in,
                        status="configured" if logged_in else "client_ready",
                        message=("沿用本机已配置的模型服务；实际可用性以发送结果为准。"
                                 if logged_in else "已找到本机 Codex，将使用你现有的模型服务配置；发送消息即可检查连接。"),
                    )
                except (OSError, subprocess.TimeoutExpired):
                    result.update(status="unavailable", message="暂时无法检查本机对话连接。")
            self._probe_at = time.monotonic()
            self._probe_result = result
            return dict(result)

    def _conversation_args(self):
        with self._config_lock:
            if self._extra_args and time.monotonic() - self._config_at < 60:
                return list(self._extra_args)
            try:
                feature_run = subprocess.run(
                    [self.executable, "features", "list"], capture_output=True,
                    text=True, timeout=12, cwd=str(self.workdir),
                )
                if feature_run.returncode:
                    raise ValueError("CLI configuration inspection failed")
                features = {line.split()[0] for line in feature_run.stdout.splitlines() if line.strip()}
                if not {"shell_tool", "apps", "plugins", "hooks"}.issubset(features):
                    raise ValueError("Unsupported text-only runtime")
                args = []
                for feature in self.DISABLED_FEATURES:
                    if feature in features:
                        args.extend(["--disable", feature])
                # Inspect the same feature set used for the actual chat. Plugin-provided
                # servers disappear when plugins are disabled and cannot be overridden
                # as empty user-config tables (the CLI rejects those without a transport).
                mcp_run = subprocess.run(
                    [self.executable] + args + ["mcp", "list", "--json"],
                    capture_output=True, text=True, timeout=12, cwd=str(self.workdir),
                )
                if mcp_run.returncode:
                    raise ValueError("MCP configuration inspection failed")
                servers = json.loads(mcp_run.stdout)
                if not isinstance(servers, list):
                    raise ValueError("Unexpected MCP inventory")
                for server in servers:
                    name = server.get("name")
                    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                        raise ValueError("Invalid MCP server entry")
                    # The CLI override path uses bare dotted keys, not TOML quoted keys.
                    args.extend(["-c", "mcp_servers." + name + ".enabled=false"])
                self._extra_args = args
                self._config_at = time.monotonic()
                return list(args)
            except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError) as exc:
                raise RuntimeError("暂时无法建立纯对话连接，请检查本机 Codex 配置后重试。") from exc

    @staticmethod
    def _stop(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)

    @staticmethod
    def _friendly_error(stderr):
        value = stderr.lower()
        if any(word in value for word in ("unauthorized", "authentication", "401", "api key")):
            return "模型服务未通过身份验证，请检查本机 Codex 的登录或服务配置。"
        if any(word in value for word in ("429", "rate limit", "quota", "insufficient_quota")):
            return "模型服务暂时达到使用限制，请稍后再试。"
        if any(word in value for word in ("403", "forbidden", "blocked")):
            return "模型服务拒绝了本次连接，请检查服务的访问权限。"
        if any(word in value for word in ("connection", "network", "resolve", "timed out", "stream disconnected")):
            return "暂时连不上模型服务，聊天记录已保留，可以稍后重试。"
        return "这次对话没有完成，聊天记录已保留。请检查本机 Codex 服务后重试。"

    def close(self):
        with self._process_lock:
            self._closed = True
            processes = list(self._processes)
        for proc in processes:
            self._stop(proc)

    def generate(self, prompt: str, cancel_event: threading.Event) -> str:
        if not self.executable:
            raise RuntimeError("还没有找到本机 Codex 客户端。")
        if cancel_event.is_set():
            raise RuntimeError("对话已停止。")
        args = self._conversation_args()
        if cancel_event.is_set():
            raise RuntimeError("对话已停止。")
        with tempfile.TemporaryDirectory(prefix="reply-", dir=str(self.workdir)) as temp:
            answer_file = Path(temp) / "answer.txt"
            command = [
                self.executable, "-a", "never", "exec", "--ephemeral", "--json",
                "--sandbox", "read-only", "--skip-git-repo-check", "--color", "never",
                "-C", str(self.workdir), "-o", str(answer_file),
                "-c", 'model_reasoning_effort="low"', "-c", 'web_search="disabled"',
            ] + args + ["-"]
            instruction = (
                "你正在本地像素小镇中进行纯文字角色对话。只根据给定的角色档案、记忆、"
                "真实工作摘要与聊天历史回答，不调用工具，不访问额外文件，不执行任务或外部动作。"
                "角色设定是表达风格，不改变事实与权限。不能声称已经调度、更新、检查或发布了"
                "未发生的工作；需要行动时说明建议与下一步。用自然中文直接回复用户。\n\n"
            ) + prompt
            env = os.environ.copy()
            # Ephemeral web chats must not attach themselves to the desktop's active turn.
            for key in ("CODEX_THREAD_ID", "CODEX_TURN_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
                env.pop(key, None)
            proc = None
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as request:
                try:
                    request.write(instruction.encode("utf-8"))
                    request.seek(0)
                    with self._process_lock:
                        if self._closed or cancel_event.is_set():
                            raise RuntimeError("对话已停止。")
                        proc = subprocess.Popen(
                            command, stdin=request, stdout=out, stderr=err,
                            cwd=str(self.workdir), env=env, start_new_session=True,
                        )
                        self._processes.add(proc)
                    deadline = time.monotonic() + self.timeout
                    while proc.poll() is None:
                        if cancel_event.wait(0.15):
                            self._stop(proc)
                            raise RuntimeError("对话已停止。")
                        if time.monotonic() > deadline:
                            self._stop(proc)
                            raise RuntimeError("这次回复等待时间较长，已停止等待。聊天记录已保留，请稍后重试。")
                    if cancel_event.is_set():
                        raise RuntimeError("对话已停止。")
                    if proc.returncode != 0:
                        err.seek(0)
                        stderr = err.read(256000).decode("utf-8", errors="replace")
                        raise RuntimeError(self._friendly_error(stderr))
                    answer = answer_file.read_text().strip() if answer_file.exists() else ""
                    if not answer:
                        raise RuntimeError("模型没有返回可显示的回复，请重试。")
                    return answer
                except (OSError, BrokenPipeError) as exc:
                    raise RuntimeError("无法启动本机对话连接，请检查 Codex 客户端。") from exc
                finally:
                    if proc is not None:
                        self._stop(proc)
                        with self._process_lock:
                            self._processes.discard(proc)
