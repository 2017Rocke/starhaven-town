#!/usr/bin/env python3
"""Bounded, non-executing source review. Findings are review leads, never a verdict."""

from __future__ import annotations

from bisect import bisect_left
import heapq
import os
from pathlib import Path
import re
import stat
from typing import Any


MAX_FILE_BYTES = 512_000
MAX_TOTAL_BYTES = 12_000_000
MAX_FILES = 6_000
MAX_FINDINGS = 160
CONTEXT_LINES = 12
EXCLUDED_DIRS = frozenset({
    ".git", "node_modules", "vendor", ".venv", "venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache",
})
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".bz2", ".xz", ".7z", ".tar", ".jar", ".class", ".pyc",
    ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2", ".ttf", ".mp4",
    ".mp3", ".sqlite", ".db", ".wasm", ".bin",
})


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Deliberately simple patterns: these do not parse or execute source code.
RULES = (
    ("credential_paths", "medium", "敏感凭据或个人数据路径", _rx(
        r"(?:\.ssh\b|\.aws[/\\]credentials|\.git-credentials\b|"
        r"\.npmrc\b|id_rsa\b|id_ed25519\b|Login Data\b|Login Keychain\b|"
        r"login\.keychain|Cookies\b|Local State\b|security\s+find-(?:generic|internet)-password)"
    ), "可能涉及凭据或浏览器数据；需确认用途、授权和数据流，字符串本身不证明读取行为。"),
    ("environment_access", "info", "环境变量读取能力", _rx(
        r"\b(?:os\.environ|(?:os\.)?getenv\s*\(|process\.env\b|std::env::|"
        r"Environment\.GetEnvironmentVariable|ENV\s*\[|env::var\s*\()|\$env:|"
        r"\$\{?(?:AWS_[A-Z_]+|GITHUB_TOKEN|GH_TOKEN|SSH_AUTH_SOCK|OPENAI_API_KEY)\b"
    ), "环境变量通常用于正常配置，也可能包含密钥；检查具体读取项及后续用途。"),
    ("telemetry", "info", "遥测、分析或错误上报线索", _rx(
        r"\b(?:telemetry|sentry_sdk|sentry\.init|posthog|mixpanel|amplitude|"
        r"segment\.track|analytics\.track|opentelemetry)\b"
    ), "遥测并不等于恶意；核实收集字段、目标域名、默认开关及用户同意机制。"),
    ("network_io", "info", "网络请求或发送能力", _rx(
        r"\b(?:(?:requests|httpx|axios)\.(?:get|post|put|patch|request|send)\s*\(|"
        r"fetch\s*\(|https?\.request\s*\(|urllib\.request\.|"
        r"(?:socket|sock)\.(?:connect|send|sendall|sendto)\s*\(|"
        r"curl\s|wget\s|Invoke-WebRequest\b|Invoke-RestMethod\b|WebSocket\s*\()"
    ), "发现联网相关代码；需要结合发送内容和目标确认，联网能力本身不是信息泄露证据。"),
    ("shell_execution", "medium", "系统命令或子进程执行能力", _rx(
        r"\b(?:subprocess\.(?:run|Popen|call|check_call|check_output)|"
        r"os\.(?:system|popen)|child_process\b|execSync\s*\(|spawnSync\s*\(|"
        r"shell\s*=\s*True|Runtime\.getRuntime\(\)\.exec|ProcessBuilder\s*\(|"
        r"Start-Process\b)"
    ), "检查命令来源、输入转义和执行时机；正常安装或构建同样可能使用该能力。"),
    ("download_execute", "high", "下载内容直接交给解释器执行", _rx(
        r"(?:\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b|"
        r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:python\d*|perl|ruby|node)\b|"
        r"\b(?:iex|Invoke-Expression)\s*\(?\s*(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b|"
        r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*\|\s*(?:iex|Invoke-Expression)\b)"
    ), "下载后直接执行扩大供应链风险；核实下载地址、内容固定方式、签名和执行权限。"),
    ("dynamic_evaluation", "medium", "动态代码执行能力", _rx(
        r"\b(?:eval|exec)\s*\(|\bnew\s+Function\s*\(|\bInvoke-Expression\b"
    ), "动态执行可能是正常功能；重点检查输入是否来自网络或不可信用户。"),
    ("install_hook", "medium", "安装生命周期钩子", _rx(
        r'"(?:preinstall|install|postinstall|prepare)"\s*:|\bcmdclass\s*=|'
        r"\[project\.entry-points\.|\bsetup_requires\s*="
    ), "安装过程可能触发代码或下载；需追踪被调用脚本。入口点声明本身不代表已执行。"),
    ("container_privilege", "high", "容器特权或宿主机访问配置", _rx(
        r"--privileged\b|\bprivileged\s*:\s*true\b|/var/run/docker\.sock|"
        r"--pid[=\s]+host\b|--network[=\s]+host\b|\bhostPID\s*:\s*true\b|"
        r"\bcap_add\s*:|\bSYS_ADMIN\b"
    ), "该配置可能扩大宿主机访问范围；核实是否必需、是否仅为示例及部署隔离边界。"),
    ("tls_disabled", "high", "TLS 证书校验可能被关闭", _rx(
        r"\bverify\s*=\s*False\b|\brejectUnauthorized\s*:\s*false\b|"
        r"\bCERT_NONE\b|\bInsecureSkipVerify\s*:\s*true\b|"
        r"\bNODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0\b|"
        r"\bcurl\b[^\n]*(?:\s--insecure\b|\s-[A-Za-z]*k[A-Za-z]*(?:\s|$))"
    ), "关闭证书校验可能允许中间人攻击；确认实际运行条件以及是否仅限测试。"),
    ("persistence", "medium", "持久启动或系统服务相关线索", _rx(
        r"\bcrontab\b|/etc/cron\b|LaunchAgents[/\\]|LaunchDaemons[/\\]|"
        r"\blaunchctl\s+(?:load|bootstrap|enable)\b|"
        r"\bsystemctl\s+enable\b|CurrentVersion[\\/]+Run\b|"
        r"\bschtasks\b[^\n]*/create\b|\.config/autostart/|authorized_keys\b"
    ), "后台服务和自启动可用于正常功能，也可用于持久化；核实安装授权和卸载路径。"),
)
CI_RULE = _rx(
    r"\bpermissions\s*:\s*write-all\b|\b(?:contents|packages|actions|id-token|"
    r"pull-requests|security-events|deployments)\s*:\s*write\b|\bpull_request_target\b"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b[\w.-]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key|"
    r"private[_-]?key)[\w.-]*\b['\"]?\s*[:=]\s*)(['\"])([^'\"\r\n]*)(\2)"
)
UNQUOTED_SECRET = re.compile(
    r"(?i)(\b[\w.-]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key)"
    r"[\w.-]*\b\s*[:=]\s*)(?!['\"])([^\s,;}]+)"
)
KNOWN_SECRET = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,}|"
    r"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{16,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)


def redact_text(line: str) -> str:
    """Best effort: avoid reproducing likely credential values in reports."""
    line = SECRET_ASSIGNMENT.sub(lambda m: m[1] + m[2] + "[REDACTED]" + m[4], line)
    line = UNQUOTED_SECRET.sub(lambda m: m[1] + "[REDACTED]", line)
    line = KNOWN_SECRET.sub("[REDACTED]", line)
    line = re.sub(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", line)
    line = re.sub(r"(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", line)
    # Long opaque literals may be credentials even when the variable is innocuous.
    line = re.sub(r"(['\"])[A-Za-z0-9_+/=-]{32,}\1", "'[REDACTED]'", line)
    line = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)", "[REDACTED PRIVATE KEY]", line)
    return line


def redact_snippet(line: str) -> str:
    line = " ".join(redact_text(line).split())
    return line if len(line) <= 240 else line[:239] + "…"


class ScanCancelled(RuntimeError):
    pass


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled("采集已取消。")



def _priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    text = path.as_posix().lower()
    if name in {"package.json", "setup.py", "pyproject.toml", "setup.cfg", "cargo.toml", "build.rs"}:
        rank = 0
    elif ".github/workflows/" in text or "install" in name or name.startswith("docker"):
        rank = 1
    elif any(word in text for word in ("telemetry", "security", "credential", "auth", "network")):
        rank = 2
    elif name.endswith(("lock", "lock.json")):
        rank = 4
    else:
        rank = 3
    return rank, text


def scan_repository(root: Path, *, cancel_event=None, progress=None) -> dict[str, Any]:
    """Read a bounded source tree without importing, building, or running it.

    ``files_seen`` is the number of enumerated file entries (including skipped
    symlinks), not a claim about the entire checkout. Directory exclusions are
    counted separately because the files beneath them are never enumerated.
    """
    _check_cancel(cancel_event)
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("root must be an existing directory, not a symlink")
    root = root.resolve()
    coverage: dict[str, Any] = {
        "files_seen": 0, "files_scanned": 0, "bytes_scanned": 0, "bytes_read": 0,
        "skipped_by_reason": {}, "directories_skipped_by_reason": {},
        "enumeration_complete": True, "findings_truncated": False,
        "findings_matched": 0, "findings_suppressed": 0,
        "limits": {"file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES,
                   "files": MAX_FILES, "findings": MAX_FINDINGS},
    }
    # The weakest retained finding is at the heap root. Ties keep the earliest
    # match in deterministic file/rule order; later high-priority signals can
    # displace earlier informational noise without stopping the source scan.
    finding_heap: list[tuple[int, int, dict[str, Any]]] = []
    candidates: list[Path] = []

    def skipped(reason: str, directory: bool = False) -> None:
        key = "directories_skipped_by_reason" if directory else "skipped_by_reason"
        coverage[key][reason] = coverage[key].get(reason, 0) + 1

    def walk_error(_: OSError) -> None:
        skipped("unreadable", directory=True)
        coverage["enumeration_complete"] = False

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        _check_cancel(cancel_event)
        dirs.sort()
        kept = []
        for dirname in dirs:
            path = Path(current) / dirname
            if path.is_symlink():
                skipped("symlink", directory=True)
            elif dirname.lower() in EXCLUDED_DIRS:
                skipped("excluded_directory", directory=True)
            else:
                kept.append(dirname)
        dirs[:] = kept
        for filename in sorted(files):
            if coverage["files_seen"] >= MAX_FILES:
                coverage["enumeration_complete"] = False
                break
            coverage["files_seen"] += 1
            candidates.append(Path(current) / filename)
        if not coverage["enumeration_complete"] and coverage["files_seen"] >= MAX_FILES:
            break

    def add(rule_id: str, severity: str, title: str, relative: str,
            line: int, snippet: str, reason: str) -> None:
        sequence = coverage["findings_matched"]
        coverage["findings_matched"] += 1
        rank = {"info": 0, "low": 1, "medium": 2, "high": 3}.get(severity, 0)
        quality = (rank, -sequence)
        if MAX_FINDINGS <= 0 or (len(finding_heap) >= MAX_FINDINGS and quality <= finding_heap[0][:2]):
            return
        if Path(relative).suffix.lower() in {".md", ".rst", ".txt"}:
            reason += " 此命中位于文档或文本，可能仅为使用示例。"
        finding = {"rule_id": rule_id, "severity": severity, "title": title,
                   "path": relative, "line": line,
                   "snippet": redact_snippet(snippet), "reason": reason}
        entry = (rank, -sequence, finding)
        if len(finding_heap) < MAX_FINDINGS:
            heapq.heappush(finding_heap, entry)
        else:
            heapq.heapreplace(finding_heap, entry)

    for path in sorted(candidates, key=lambda p: _priority(p.relative_to(root))):
        _check_cancel(cancel_event)
        relative = path.relative_to(root).as_posix()
        try:
            # The collector never creates links; retain this guard for callers
            # scanning a separately supplied tree or a tree changed concurrently.
            if any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
                skipped("symlink")
                continue
            if root not in path.resolve().parents:
                skipped("outside_root")
                continue
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                skipped("symlink")
                continue
            if not stat.S_ISREG(metadata.st_mode):
                skipped("non_regular_file")
                continue
            if path.suffix.lower() in BINARY_SUFFIXES:
                skipped("binary_extension")
                continue
            if metadata.st_size > MAX_FILE_BYTES:
                skipped("file_size_limit")
                continue
            if coverage["bytes_read"] + metadata.st_size > MAX_TOTAL_BYTES:
                skipped("total_size_limit")
                continue
            # O_NOFOLLOW also prevents replacing the final path with a symlink
            # between lstat and open. No imported source code is ever executed.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    skipped("non_regular_file")
                    continue
                remaining = MAX_TOTAL_BYTES - coverage["bytes_read"]
                raw = stream.read(min(MAX_FILE_BYTES + 1, remaining))
                actual_size = os.fstat(stream.fileno()).st_size
            coverage["bytes_read"] += len(raw)
            if len(raw) > MAX_FILE_BYTES:
                skipped("file_size_limit")
                continue
            if actual_size > len(raw):
                skipped("total_size_limit")
                continue
            if b"\x00" in raw:
                skipped("binary_content")
                continue
            try:
                source = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                skipped("non_utf8_or_binary")
                continue
        except OSError:
            skipped("unreadable")
            continue
        coverage["files_scanned"] += 1
        coverage["bytes_scanned"] += len(raw)
        lines = source.splitlines()
        sensitive_lines: list[int] = []
        network_lines: list[int] = []
        for number, line in enumerate(lines, 1):
            if number % 100 == 0:
                _check_cancel(cancel_event)
            if len(line) > 8192:
                skipped("line_size_limit")
                continue
            for rule_id, severity, title, pattern, reason in RULES:
                if pattern.search(line):
                    add(rule_id, severity, title, relative, number, line, reason)
                    if rule_id in {"credential_paths", "environment_access"}:
                        sensitive_lines.append(number)
                    if rule_id == "network_io":
                        network_lines.append(number)
            if relative.startswith(".github/workflows/") and CI_RULE.search(line):
                add("ci_permissions", "medium", "CI 写权限或高权限事件", relative,
                    number, line, "核实权限是否最小化，尤其检查不可信拉取请求是否能接触密钥或执行代码。")
        # Proximity is only a triage heuristic, not data-flow proof. One lead per
        # file bounds duplicate noise while preserving the exact nearby lines.
        nearby = None
        for sensitive in sensitive_lines:
            index = bisect_left(network_lines, sensitive - CONTEXT_LINES)
            if index < len(network_lines) and network_lines[index] <= sensitive + CONTEXT_LINES:
                nearby = sensitive, network_lines[index]
                break
        if nearby:
            sensitive, network = nearby
            add("sensitive_network_proximity", "medium", "敏感数据线索与网络调用相邻",
                relative, sensitive, lines[sensitive - 1],
                f"敏感访问线索第 {sensitive} 行与联网线索第 {network} 行相距不超过 {CONTEXT_LINES} 行。"
                "这只是复核线索，尚未证明数据被发送；需要人工追踪变量、调用链和请求目标。")

    findings = [entry[2] for entry in sorted(finding_heap, key=lambda entry: (-entry[0], -entry[1]))]
    coverage["findings_suppressed"] = coverage["findings_matched"] - len(findings)
    coverage["findings_truncated"] = coverage["findings_suppressed"] > 0
    limitations = [
        "这是基于文本模式的静态初筛，不会安装依赖、构建、导入或执行项目代码。",
        "未命中规则不代表安全；命中联网、环境变量、遥测或命令执行也不等于恶意或信息泄露。",
        "无法证明完整数据流；混淆、动态生成代码、条件分支、依赖包、子模块、二进制及运行时下载内容可能未覆盖。",
        "仅扫描 UTF-8 文本，跳过符号链接、依赖目录和指定二进制后缀；目录排除项未统计其内部文件数。",
        "files_seen 仅指已枚举文件项；bytes_scanned 只统计完整读入并进行规则扫描的文本字节。",
        "bytes_read 包含已读入但因二进制或编码原因跳过的内容，读取总量同样受 total_bytes 上限约束。",
        "高风险标记表示优先复核程度，不是恶意判定；文档示例、注释和测试代码也可能命中。",
        "片段已尽力遮盖疑似密钥，但自动脱敏并非完整秘密识别；分享原始报告前仍需审阅。",
    ]
    if not coverage["enumeration_complete"]:
        limitations.append("文件枚举未完成：达到枚举上限或遇到无法读取的目录，实际项目文件数可能更多。")
    if coverage["skipped_by_reason"] or coverage["directories_skipped_by_reason"]:
        limitations.append("扫描覆盖不完整；请查看 coverage 中逐类跳过计数与扫描上限。")
    if coverage["findings_truncated"]:
        limitations.append("发现项超过保留上限，按 high、medium、low、info 优先保留，同级按确定性扫描顺序保留；"
                           "其余命中仅计数，不中止后续文件扫描。扫描仍受文件数、读取字节等覆盖上限约束。")
    level = "high" if any(item["severity"] == "high" for item in findings) else "medium" if any(item["severity"] == "medium" for item in findings) else "info"
    if not coverage["files_scanned"]:
        level = "unknown"
    labels = {"high": "有高优先级复核线索", "medium": "有待复核能力线索", "info": "仅完成静态初筛", "unknown": "暂无可用扫描证据"}
    return {"schema_version": 1, "level": level, "label": labels[level],
            "coverage": coverage, "findings": findings, "limitations": limitations}


# Compatibility alias for callers reviewing a source tree directly.
scan_tree = scan_repository
