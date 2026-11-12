"""Public GitHub discovery and pinned source snapshots; never run downloaded code.

This module is self-contained and uses only Python's standard library. Every
remote README, AGENTS file and script is source material, never an instruction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import html
import io
import json
from pathlib import Path, PurePosixPath
import queue
import re
import sys
import tarfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib

from scout_scan import ScanCancelled, redact_text, scan_repository

MAX_API_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_FILES = 6000
MAX_MEMBERS = 12000
STRATEGY = "按主题寻找近 180 天有更新的公开项目，按累计 Star 排序；不是日增 Star 榜。排除 fork、归档、资料合集、未确认开源许可与历史已采集项目。"
CATEGORIES = (
    ("AI 与智能体", "topic:llm"),
    ("开发工具", "topic:developer-tools"),
    ("自托管应用", "topic:self-hosted"),
    ("效率工具", "topic:productivity"),
    ("知识管理", "topic:knowledge-management"),
)
ALLOWED_LICENSES = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
    "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0", "ISC", "Unlicense", "0BSD", "Zlib",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later", "BSL-1.0", "EPL-2.0",
})
EXCLUDED_NAMES = ("awesome", "tutorial", "course", "interview", "roadmap", "resources",
                  "cheatsheet", "build-your-own", "free-programming", "public-apis")
WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)
_NETWORK_SLOTS = threading.BoundedSemaphore(4)


class ScoutError(RuntimeError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class ScoutCancelled(ScoutError):
    def __init__(self):
        super().__init__("cancelled", "采集已取消。")


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ScoutCancelled()


def _progress(callback, stage, message, **fields):
    if callback is not None:
        callback(dict(stage=stage, message=message, **fields))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _allowed_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in {"api.github.com", "codeload.github.com"}
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and not parsed.fragment and not any(ord(c) < 33 or ord(c) == 127 for c in url))
    except (ValueError, TypeError):
        return False


class PublicGitHubClient:
    """Fixed-host anonymous GETs with bounded memory and cancellable waiting.

    Proxy environment variables are deliberately ignored. No Git credential,
    browser session, authorization header or local file is sent to GitHub.
    """
    def __init__(self, *, opener=None, socket_timeout=8, request_deadline=18, archive_deadline=60, security=None):
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.socket_timeout = socket_timeout
        self.request_deadline = request_deadline
        self.archive_deadline = archive_deadline
        self.security = security

    def deadline_for(self, url):
        return self.archive_deadline if urllib.parse.urlsplit(url).hostname == "codeload.github.com" else self.request_deadline

    def fetch(self, url, *, max_bytes=MAX_API_BYTES, cancel_event=None):
        if not _allowed_url(url):
            if self.security is not None:
                try:
                    self.security.network_check(url=url, purpose="github", agent_id="scout")
                except Exception:
                    raise ScoutError("audit_unavailable", "稽查署暂时无法记录请求，已暂停采集。") from None
            raise ScoutError("invalid_source", "只允许从 GitHub 官方公开接口与源码下载域名读取资料。")
        _check_cancel(cancel_event)
        if not _NETWORK_SLOTS.acquire(blocking=False):
            raise ScoutError("network_busy", "已有网络请求正在结束，请稍后重试。", retryable=True)
        result = queue.Queue(maxsize=1)
        abandoned = threading.Event()

        def publish(ok, value):
            try:
                result.put_nowait((ok, value))
            except queue.Full:
                pass

        def transfer():
            audit, status_code = None, None
            ok, value = False, ScoutCancelled()
            try:
                request = urllib.request.Request(url, headers={
                    "User-Agent": "StarhavenTown-PublicScout/1.0",
                    "Accept": "application/vnd.github+json",
                    "Accept-Encoding": "identity",
                }, method="GET")
                if self.security is not None:
                    try:
                        audit = self.security.network_check(url=url, purpose="github", agent_id="scout")
                    except Exception:
                        raise ScoutError("audit_unavailable", "稽查署暂时无法记录请求，已暂停采集。") from None
                    if not isinstance(audit, dict) or type(audit.get("allowed")) is not bool:
                        audit = None
                        raise ScoutError("audit_unavailable", "稽查署返回了无效的检查结果，已暂停采集。")
                    if audit["allowed"] is not True:
                        raise ScoutError("security_blocked", "稽查署已暂停采集：" + audit.get("reason", "请求未通过规则检查。"))
                _check_cancel(cancel_event)
                if abandoned.is_set():
                    raise ScoutCancelled()
                with self.opener.open(request, timeout=self.socket_timeout) as response:
                    status_code = getattr(response, "status", None)
                    if response.geturl() != url:
                        raise ScoutError("redirect_blocked", "来源发生重定向，未继续下载。")
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > max_bytes:
                        raise ScoutError("response_too_large", "来源内容超过本机采集大小上限。")
                    chunks, size = [], 0
                    while not abandoned.is_set():
                        if cancel_event is not None and cancel_event.is_set():
                            raise ScoutCancelled()
                        block = response.read(min(65536, max_bytes + 1 - size))
                        if not block:
                            break
                        size += len(block)
                        if size > max_bytes:
                            raise ScoutError("response_too_large", "来源内容超过本机采集大小上限。")
                        chunks.append(block)
                    if abandoned.is_set():
                        return
                    data = b"".join(chunks)
                ok, value = True, data
            except urllib.error.HTTPError as exc:
                status_code = exc.code
                if exc.code in (403, 429):
                    error = ScoutError("rate_limited", "GitHub 暂时限制匿名请求，请稍后重试。", retryable=True)
                elif exc.code == 404:
                    error = ScoutError("not_found", "公开项目或固定源码版本不存在，可能已删除、改名或转为私有。")
                elif 300 <= exc.code < 400:
                    error = ScoutError("redirect_blocked", "来源发生重定向，未继续下载。")
                else:
                    error = ScoutError("source_unavailable", "GitHub 暂时无法提供这份公开资料。", retryable=True)
                value = error
                exc.close()
            except ScoutError as exc:
                value = exc
            except Exception:
                # Never reflect URLs, headers, remote error bodies or proxy secrets.
                value = ScoutError("network_error", "读取 GitHub 公开资料失败，请检查网络后重试。", retryable=True)
            finally:
                if audit is not None and audit.get("allowed") and self.security is not None:
                    try:
                        self.security.network_result(audit, ok=ok, status_code=status_code,
                                                     error=None if ok else "公开资料请求未完成")
                    except Exception:
                        ok, value = False, ScoutError("audit_unavailable", "请求结果未能写入稽查档案，请检查治安系统。")
                _NETWORK_SLOTS.release()
            if not abandoned.is_set():
                publish(ok, value)

        threading.Thread(target=transfer, name="starhaven-public-fetch", daemon=True).start()
        deadline = time.monotonic() + self.deadline_for(url)
        try:
            while time.monotonic() < deadline:
                _check_cancel(cancel_event)
                try:
                    ok, value = result.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if not ok:
                    raise value
                return value
            raise ScoutError("network_timeout", "GitHub 公开资料请求超时，请稍后重试。", retryable=True)
        finally:
            abandoned.set()


def _api(client, path, cancel_event):
    payload = client.fetch("https://api.github.com/" + path, max_bytes=MAX_API_BYTES, cancel_event=cancel_event)
    if not isinstance(payload, bytes) or len(payload) > MAX_API_BYTES:
        raise ScoutError("invalid_metadata", "GitHub 元数据大小或格式无效。")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise ScoutError("invalid_metadata", "GitHub 返回的项目资料格式无效。") from None
    if not isinstance(value, dict):
        raise ScoutError("invalid_metadata", "GitHub 返回的项目资料格式无效。")
    return value


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ScoutError("invalid_repository", "项目地址需要是 GitHub 的 owner/repository 格式。")
    if value.split("/")[1] in (".", ".."):
        raise ScoutError("invalid_repository", "项目名称无效。")
    return value


def _plain(value, limit=800):
    if not isinstance(value, str):
        return ""
    value = "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    return redact_text(value[:limit])


def _metadata(item, category=None):
    name = _name(item.get("full_name"))
    repo_id = item.get("id")
    if type(repo_id) is not int or repo_id <= 0:
        raise ScoutError("invalid_metadata", "GitHub 项目缺少有效仓库标识。")
    if item.get("private") is not False or item.get("visibility", "public") != "public":
        raise ScoutError("not_public", "仅采集公开项目。")
    if item.get("fork") or item.get("archived") or item.get("disabled"):
        raise ScoutError("excluded_project", "已排除 fork、归档或停用项目。")
    if any(fragment in name.split("/")[1].lower() for fragment in EXCLUDED_NAMES):
        raise ScoutError("excluded_project", "资料合集、教程与资源清单不在当前采集范围。")
    license_info = item.get("license") or {}
    spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    if not isinstance(spdx, str) or spdx not in ALLOWED_LICENSES:
        raise ScoutError("unconfirmed_license", "项目许可证尚未确认为支持的开源许可，已跳过。")
    stars = item.get("stargazers_count")
    if type(stars) is not int or stars < 0:
        raise ScoutError("invalid_metadata", "GitHub 项目缺少有效累计 Star 数量。")
    owner_name = name.split("/")[0]
    owner = item.get("owner") or {}
    if not isinstance(owner, dict) or str(owner.get("login", "")).lower() != owner_name.lower():
        raise ScoutError("invalid_metadata", "GitHub 项目所有者资料不一致。")
    topics = [_plain(x, 70) for x in item.get("topics", [])[:30] if isinstance(x, str)] if isinstance(item.get("topics", []), list) else []
    inferred = next((label for label, topic in CATEGORIES if topic[6:] in topics), "其他开源应用")
    return {"repo_id": repo_id, "full_name": name, "repo_url": "https://github.com/" + name,
            "owner": {"name": owner_name, "url": "https://github.com/" + owner_name,
                      "type": owner.get("type") if owner.get("type") in ("User", "Organization") else "Unknown"},
            "description": _plain(item.get("description"), 1200), "category": category or inferred,
            "categories": [category or inferred], "stars": stars, "language": _plain(item.get("language"), 50),
            "license": spdx, "license_name": _plain(license_info.get("name"), 160),
            "pushed_at": _plain(item.get("pushed_at"), 40),
            "default_branch": item.get("default_branch") if isinstance(item.get("default_branch"), str) else "",
            "topics": topics}


def discover_repositories(limit=3, *, seen_repo_ids=(), seen_full_names=(), cancel_event=None,
                          progress=None, client=None, config=None):
    """Discover candidates with explicit per-source failures and honest ranking."""
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ScoutError("invalid_limit", "每批采集数量需要在 1 到 10 之间。")
    config = config or {}
    if not isinstance(config, dict):
        raise ScoutError("invalid_config", "采集设置格式无效。")
    days = config.get("active_days", 180)
    min_stars = config.get("min_stars", 1000)
    if type(days) is not int or not 1 <= days <= 365 or type(min_stars) is not int or not 0 <= min_stars <= 1000000:
        raise ScoutError("invalid_config", "活跃天数或最低 Star 设置无效。")
    client = client or PublicGitHubClient()
    seen_ids = {str(x) for x in seen_repo_ids}
    seen_names = {str(x).lower() for x in seen_full_names}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    indexed, failures, sources = {}, [], []
    for category, topic in CATEGORIES:
        _check_cancel(cancel_event)
        _progress(progress, "discover", "正在寻找" + category + "项目。", category=category)
        query = f"{topic} pushed:>={cutoff} stars:>={min_stars} archived:false fork:false is:public"
        params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 30})
        try:
            result = _api(client, "search/repositories?" + params, cancel_event)
            if not isinstance(result.get("items"), list):
                raise ScoutError("invalid_metadata", "GitHub 搜索结果格式无效。")
            incomplete = result.get("incomplete_results") is True
            sources.append({"category": category, "query": query, "url": "https://api.github.com/search/repositories?" + params,
                            "incomplete": incomplete, "result_count": len(result["items"])})
            if incomplete:
                failures.append({"category": category, "code": "incomplete_search", "message": "GitHub 表示此主题的搜索结果不完整。"})
            for item in result["items"][:100]:
                if not isinstance(item, dict):
                    continue
                try:
                    candidate = _metadata(item, category)
                except ScoutError:
                    continue
                repo_id = str(candidate["repo_id"])
                if repo_id in seen_ids or candidate["full_name"].lower() in seen_names:
                    continue
                try:
                    pushed = datetime.fromisoformat(candidate["pushed_at"].replace("Z", "+00:00"))
                    if pushed.tzinfo is None:
                        continue
                except (ValueError, TypeError):
                    continue
                if candidate["stars"] < min_stars or pushed.date().isoformat() < cutoff:
                    continue
                if repo_id in indexed:
                    if category not in indexed[repo_id]["categories"]:
                        indexed[repo_id]["categories"].append(category)
                    continue
                indexed[repo_id] = candidate
        except ScoutCancelled:
            raise
        except ScoutError as exc:
            failures.append({"category": category, "code": exc.code, "message": exc.message})
    ranked = sorted(indexed.values(), key=lambda x: (-x["stars"], x["full_name"].lower()))
    selected, chosen = [], set()
    for category, _ in CATEGORIES:
        for candidate in ranked:
            if candidate["repo_id"] not in chosen and category in candidate["categories"] and len(selected) < limit:
                selected.append(candidate); chosen.add(candidate["repo_id"]); break
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate["repo_id"] not in chosen:
            selected.append(candidate); chosen.add(candidate["repo_id"])
    reserves = [candidate for candidate in ranked if candidate["repo_id"] not in chosen][:max(0, limit * 3 - len(selected))]
    return {"candidates": selected, "reserve_candidates": reserves, "failures": failures, "sources": sources,
            "strategy": STRATEGY.replace("180", str(days)), "partial": bool(failures),
            "status": "failed" if not sources else "partial" if failures else "ok" if selected else "empty"}


def _relative_path(name):
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ScoutError("unsafe_archive", "源码压缩包包含不安全路径，已停止解压。")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or len(path.parts) > 24:
        raise ScoutError("unsafe_archive", "源码压缩包包含越界路径，已停止解压。")
    if any(part in (".", "..") or ":" in part for part in name.rstrip("/").split("/")):
        raise ScoutError("unsafe_archive", "源码压缩包包含不安全路径，已停止解压。")
    if len(path.parts) < 2:
        return None
    parts = path.parts[1:]
    if any(part in (".", "..") or ":" in part for part in parts):
        raise ScoutError("unsafe_archive", "源码压缩包包含不安全路径，已停止解压。")
    try:
        encoded_parts = [part.encode("utf-8") for part in parts]
    except UnicodeEncodeError:
        return None
    if any(part.endswith((".", " ")) or WINDOWS_RESERVED.match(part) or any(c in part for c in '<>"|?*')
           or len(encoded) > 180 for part, encoded in zip(parts, encoded_parts)) or sum(map(len, encoded_parts)) + len(parts) - 1 > 220:
        return None
    return PurePosixPath(*parts).as_posix()


def extract_source(payload, destination, *, cancel_event=None):
    """Extract bounded regular files only, with portable case/path checks."""
    _check_cancel(cancel_event)
    if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE_BYTES:
        raise ScoutError("archive_too_large", "源码下载超过本机采集大小上限。")
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ScoutError("invalid_destination", "采集需要一个独立的空目录。")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
            expanded = archive.read(MAX_EXPANDED_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise ScoutError("invalid_archive", "下载的源码压缩包无法读取。") from None
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ScoutError("archive_too_large", "源码解压体积超过本机上限。")
    _check_cancel(cancel_event)
    files, skipped, seen, roots = [], {}, {}, set()
    size = 0
    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1
    try:
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for index, member in enumerate(archive):
                _check_cancel(cancel_event)
                if index >= MAX_MEMBERS:
                    skip("archive_entry_limit"); break
                relative = _relative_path(member.name)
                roots.add(PurePosixPath(member.name).parts[0])
                if len(roots) != 1:
                    raise ScoutError("unsafe_archive", "源码压缩包包含多个不一致的根目录。")
                if member.issparse() or member.size < 0:
                    raise ScoutError("unsafe_archive", "不支持稀疏归档文件，已停止解压。")
                if relative:
                    parts = PurePosixPath(relative).parts
                    for count in range(1, len(parts) + 1):
                        prefix = PurePosixPath(*parts[:count]).as_posix()
                        normalized = unicodedata.normalize("NFC", prefix).casefold()
                        kind = "directory" if count < len(parts) or member.isdir() else "file"
                        previous = seen.get(normalized)
                        if previous is not None and (previous != (prefix, kind) or kind != "directory"):
                            raise ScoutError("unsafe_archive", "源码路径存在大小写或重复冲突，已停止解压。")
                        seen[normalized] = (prefix, kind)
                if not member.isfile():
                    if not member.isdir():
                        skip("links_or_special_files")
                    continue
                if not relative:
                    if len(PurePosixPath(member.name).parts) < 2:
                        raise ScoutError("unsafe_archive", "源码归档文件缺少项目根目录。")
                    skip("nonportable_path"); continue
                if len(files) >= MAX_FILES:
                    skip("file_count_limit"); break
                if member.size > MAX_FILE_BYTES:
                    skip("file_size_limit"); continue
                target = destination.joinpath(*PurePosixPath(relative).parts)
                # Windows 10 systems may not enable extended path support.
                # Leave room for the enclosing job/material directory rename.
                if sys.platform == "win32" and len(str(target.absolute())) > 240:
                    skip("windows_path_length_limit"); continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # A private, initially empty tree; the archive cannot create links.
                    if destination.resolve() not in target.resolve().parents:
                        raise ScoutError("unsafe_archive", "源码路径越过采集目录。")
                    stream = archive.extractfile(member)
                    if stream is None:
                        skip("unreadable_file"); continue
                    raw = stream.read(member.size + 1)
                    if len(raw) != member.size:
                        raise ScoutError("invalid_archive", "源码文件长度与归档不一致。")
                    with target.open("xb") as output:
                        output.write(raw)
                    size += len(raw); files.append(relative)
                    if raw.startswith(b"version https://git-lfs.github.com/spec/v1"):
                        skip("lfs_pointer_only")
                    if relative == ".gitmodules":
                        skip("submodules_not_fetched")
                except OSError:
                    raise ScoutError("extract_failed", "无法保存源码文件，请检查磁盘空间和目录权限。") from None
    except tarfile.TarError:
        raise ScoutError("invalid_archive", "下载的源码归档格式无效。") from None
    if not files:
        raise ScoutError("empty_source", "此项目没有取回可保存的源码文件。")
    return {"status": "partial" if skipped else "ok", "files": files,
            "file_count": len(files), "bytes_written": size, "skipped": skipped}


def _readme(destination, files):
    choices = sorted((name for name in files if "/" not in name and name.lower() in
                      {"readme", "readme.md", "readme.rst", "readme.txt", "readme.markdown"}), key=str.lower)
    if not choices:
        return None, ""
    relative = choices[0]
    with (destination / relative).open("rb") as source:
        raw = source.read(24000)
    text = raw.decode("utf-8-sig", errors="replace")
    # Display as quoted plain text, never as HTML or executable project guidance.
    text = re.sub(r"<[^>]{0,1000}>", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return relative, _plain(html.unescape(text), 6000)


def collect_repository(candidate, destination, *, cancel_event=None, progress=None, client=None):
    """Fetch a verified public repo and one immutable commit into an empty tree."""
    _check_cancel(cancel_event)
    candidate = {"full_name": candidate} if isinstance(candidate, str) else candidate
    if not isinstance(candidate, dict):
        raise ScoutError("invalid_repository", "请选择有效的公开项目。")
    name = _name(candidate.get("full_name"))
    client = client or PublicGitHubClient()
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise ScoutError("invalid_destination", "采集需要一个独立的空目录。")
    _progress(progress, "metadata", "正在核对公开项目和许可证。", full_name=name)
    metadata = _metadata(_api(client, "repos/" + name, cancel_event), candidate.get("category"))
    if candidate.get("repo_id") is not None and str(candidate["repo_id"]) != str(metadata["repo_id"]):
        raise ScoutError("repository_changed", "仓库身份与发现时不一致，请重新寻找候选。")
    name = metadata["full_name"]
    branch = metadata["default_branch"]
    if not branch or len(branch) > 200 or any(ord(c) < 33 or ord(c) == 127 for c in branch):
        raise ScoutError("invalid_metadata", "项目缺少默认分支资料。")
    commit = _api(client, "repos/" + name + "/commits/" + urllib.parse.quote(branch, safe=""), cancel_event)
    sha = commit.get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ScoutError("invalid_commit", "未取得有效的固定源码版本。")
    try:
        commit_date = _plain(commit["commit"]["committer"]["date"], 40)
    except (KeyError, TypeError):
        commit_date = ""
    source_url = "https://codeload.github.com/" + name + "/tar.gz/" + sha
    _progress(progress, "download", "正在取回固定版本源码。", full_name=name, commit_sha=sha)
    payload = client.fetch(source_url, max_bytes=MAX_ARCHIVE_BYTES, cancel_event=cancel_event)
    _check_cancel(cancel_event)
    destination.mkdir(parents=True, exist_ok=True)
    _progress(progress, "extract", "正在保存源码；不会安装或执行项目。", full_name=name)
    extraction = extract_source(payload, destination, cancel_event=cancel_event)
    _progress(progress, "scan", "正在标注静态代码复核线索。", full_name=name)
    try:
        scan = scan_repository(destination, cancel_event=cancel_event, progress=progress)
    except ScanCancelled:
        raise ScoutCancelled() from None
    except (ValueError, OSError):
        raise ScoutError("scan_failed", "源码已取回，但静态扫描未完成；本次不标记为完成。") from None
    for finding in scan["findings"]:
        finding["url"] = "https://github.com/" + name + "/blob/" + sha + "/" + urllib.parse.quote(finding["path"], safe="/") + "#L" + str(finding["line"])
    readme_path, excerpt = _readme(destination, extraction["files"])
    _check_cancel(cancel_event)
    metadata.update(commit_sha=sha, sha=sha, commit_date=commit_date, source_url=source_url,
                    archive_sha256=hashlib.sha256(payload).hexdigest(), source_status=extraction["status"],
                    file_count=extraction["file_count"], files=extraction["files"], extraction=extraction,
                    readme_path=readme_path, readme_excerpt=excerpt, scan=scan,
                    collected_at=datetime.now(timezone.utc).isoformat(),
                    provenance={"metadata_url": "https://api.github.com/repos/" + name,
                                "commit_url": "https://github.com/" + name + "/commit/" + sha,
                                "source_url": source_url, "selection_strategy": STRATEGY})
    return metadata
