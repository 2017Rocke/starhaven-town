#!/usr/bin/env python3
"""Export or restore a portable town state bundle.

A bundle carries what a second machine needs so training results, artefacts,
collection records and the audit archives do not have to be produced again.
It deliberately never carries credentials: the model API key lives in the OS
credential store, keyed by the data directory path, so it cannot travel with a
file copy and must be entered again on the new machine.
Local model-budget verification records also stay on their original machine;
the destination must verify its own model connection before controlled use.

    python scripts/state_bundle.py export  --data <数据目录> --out <zip>
    python scripts/state_bundle.py inspect <zip>
    python scripts/state_bundle.py restore <zip> --data <数据目录> [--force]

The bundle layout is:

    manifest.json                     counts, hashes, redactions, exclusions
    data/town.sqlite3                 residents, chat, budget, growth records
    data/provider.json                endpoint, model and protocol, no credential
    data/workspaces/<id>/...          artefact bodies (the database stores hashes)
    data/scout-workshop/...           collection index, optionally the materials
    data/security-office/...          append-only audit archives and reports
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "starhaven-town-state/1"

# Never exported: browser cache, logs, transient locks, upgrade snapshots and the
# per-process ready marker. Scout materials are ~65 MB of third-party source and
# are opt-in, because a repository snapshot should stay small and reviewable.
PRIVATE_WORK_DIRS = {"documents", "interactions", "dev-workbench", "local-skills"}
ALWAYS_EXCLUDED_DIRS = {"WebView2", "logs", "backups", "runtime", "__pycache__", *PRIVATE_WORK_DIRS}
LOCAL_BUDGET_VERIFICATION_FILE = "model-budget-verifications.json"
LOCAL_BUDGET_VERIFICATION_TEMP_PREFIX = ".model-budget-verifications-"
BUDGET_REVERIFY_NOTICE = "本机模型预算验证档案不随状态包迁移，请在本机重新验证模型预算适配。"
ALWAYS_EXCLUDED_FILES = {"town.lock", "settings.json", LOCAL_BUDGET_VERIFICATION_FILE, "model-feature-verifications.json"}
RUN_DIRECTORY = re.compile(r"run-[0-9a-f]{32}")

PROVIDER_KEYS = ("mode", "base_url", "model", "protocol", "auth_type")
PROVIDER_ENUMS = {"protocol": ("openai", "anthropic"), "auth_type": ("bearer", "api_key")}

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def summarise(db_path):
    """Read-only counts so a bundle can be checked without opening the town."""
    counts = {}
    try:
        connection = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
        try:
            for table in ("agents", "messages", "v2_tasks", "v2_artifacts", "v2_skills", "v2_calls"):
                try:
                    counts[table] = connection.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
                except sqlite3.DatabaseError:
                    counts[table] = None
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise SystemExit("无法读取小镇数据库：%s" % exc) from None
    return counts


def lock_held(data_dir):
    """Return True when the town is running, reusing the app's own lock."""
    lock_path = Path(data_dir) / "town.lock"
    if not lock_path.is_file():
        return False
    sys.path.insert(0, str(ROOT))
    try:
        from server import _lock_instance  # noqa: E402  (repo root on sys.path)
    except Exception:
        return False
    try:
        handle = lock_path.open("a+")
    except OSError:
        return True
    try:
        try:
            _lock_instance(handle)
        except BlockingIOError:
            return True
        return False
    finally:
        handle.close()


def provider_fields(raw):
    """Allow only portable settings; protocol/auth selectors can never carry secrets.

    Missing selectors stay absent for legacy providers. Explicitly invalid
    selectors invalidate the whole provider, rather than silently changing its
    protocol or inheriting a verified provider's budget permissions.
    The same whitelist is applied to imported bundles, even if another tool
    produced a valid manifest containing extra credential fields.
    """
    if not isinstance(raw, dict):
        return {}, ["provider.json 不是设置对象，已跳过"]
    for key, choices in PROVIDER_ENUMS.items():
        if key in raw and (not isinstance(raw[key], str) or raw[key] not in choices):
            return {}, ["provider.json 接口协议或认证方式无效，已清空模型设置，请重新配置"]
    protocol = raw.get("protocol", "openai")
    auth_type = raw.get("auth_type", "api_key" if protocol == "anthropic" else "bearer")
    if protocol == "openai" and auth_type != "bearer":
        return {}, ["provider.json 接口协议与认证方式不匹配，已清空模型设置，请重新配置"]
    kept = {key: raw[key] for key in PROVIDER_KEYS if key in raw}
    dropped = sorted(set(raw) - set(kept))
    return kept, ["provider.json 丢弃字段：%s" % ", ".join(dropped)] if dropped else []


def scrub_provider(path):
    """Keep endpoint settings, drop anything else that might hold a credential."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, ["provider.json 无法解析，已跳过"]
    return provider_fields(raw)


def scan_secrets(path):
    """High-confidence credential shapes only; a word like 'token' is not a finding."""
    try:
        if path.stat().st_size > 2 << 20:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return sorted({pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)})


def local_budget_verification_path(value):
    """Recognise private records across portable path and filename aliases."""
    # Bundles can come from other tools and operating systems. Account for
    # Windows separators, case-insensitive filesystems and trailing dots/spaces
    # without reading or reproducing any private record contents.
    for part in str(value).replace("\\", "/").split("/"):
        name = part.split(":", 1)[0].rstrip(" .").casefold()
        if name in (LOCAL_BUDGET_VERIFICATION_FILE, "model-feature-verifications.json") or name.startswith(".model-feature-") or (name.startswith(LOCAL_BUDGET_VERIFICATION_TEMP_PREFIX) and name.endswith(".tmp")):
            return True
    return False


def private_work_path(value):
    """Recognize local-only work areas before accepting cross-platform aliases."""
    return any(part.split(":", 1)[0].rstrip(" .").casefold() in PRIVATE_WORK_DIRS
               for part in str(value).replace("\\", "/").split("/"))


def collect(data_dir, include_materials):
    """Yield (archive name, source path) pairs, plus the exclusion notes."""
    base = Path(data_dir).resolve()
    notes = []
    entries = []

    def walk(folder):
        for path in sorted(folder.iterdir()):
            # A differently named link must not reintroduce a private subtree.
            if path.is_symlink() or private_work_path(path.name):
                continue
            if local_budget_verification_path(path.name):
                if BUDGET_REVERIFY_NOTICE not in notes:
                    notes.append(BUDGET_REVERIFY_NOTICE)
                continue
            if path.is_dir():
                if path.name in ALWAYS_EXCLUDED_DIRS or RUN_DIRECTORY.fullmatch(path.name):
                    continue
                if not include_materials and path.name == "materials" and folder.name == "scout-workshop":
                    notes.append("已排除 scout-workshop/materials（约 65 MB 第三方源码，需要时加 --include-materials）")
                    continue
                if path.name == "pending" and not any(path.rglob("*")):
                    continue
                walk(path)
            elif path.is_file():
                if path.name in ALWAYS_EXCLUDED_FILES or path.suffix in (".log", ".lock"):
                    continue
                # A stray -wal/-shm beside the database must never be shipped: it
                # can carry rows the bundle is supposed to leave behind.
                if path.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                entries.append((path.relative_to(base).as_posix(), path))

    if not base.is_dir():
        raise SystemExit("数据目录不存在：%s" % base)
    walk(base)
    return entries, notes


def remove_local_records(connection):
    """Remove local files' indexes and native source grants from a copied DB."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    removed = {}
    for table in ("v2_documents", "v2_local_skills", "v2_message_local_skills"):
        if table in tables:
            removed[table] = connection.execute("DELETE FROM %s" % table).rowcount
    if "v2_meta" in tables:
        removed["development_connections"] = connection.execute(
            "DELETE FROM v2_meta WHERE key GLOB 'dev-connection-*'").rowcount
    if "v2_intents" in tables:
        removed["development_intents"] = connection.execute(
            "DELETE FROM v2_intents WHERE action IN ('dev-connect','dev-apply')").rowcount
    return removed


def build_database(source, destination, strip):
    """Write one self-contained database for the bundle.

    The copy goes through SQLite's backup API so a town that is still running
    cannot leave committed rows behind in an unshipped -wal file. Chat text is
    the most personal part of a town and is not needed to carry training
    results, artefacts, budgets or audit records to another machine, so it can
    be dropped here. The result is checkpointed and switched back to a plain
    rollback journal: a trailing -wal beside the bundle would put removed rows
    back on restore.
    """
    origin = sqlite3.connect("file:%s?mode=ro" % source.as_posix(), uri=True)
    try:
        target = sqlite3.connect(str(destination))
        try:
            origin.backup(target)
            # Imported private work and local skill paths are never portable
            # town state, even when the owner opts to retain ordinary chat.
            removed = remove_local_records(target)
            for table in ("messages", "v2_message_sources"):
                if not strip:
                    break
                try:
                    removed[table] = target.execute("DELETE FROM %s" % table).rowcount
                except sqlite3.DatabaseError:
                    removed[table] = None
            target.commit()
            target.execute("VACUUM")
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
        finally:
            target.close()
    finally:
        origin.close()
    for sidecar in ("-wal", "-shm", "-journal"):
        leftover = Path(str(destination) + sidecar)
        if leftover.exists():
            leftover.unlink()
    return removed if strip else {}


def sanitized_database(payload):
    """Reapply local-only exclusions to databases from externally made bundles."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="town-state-import-") as temporary:
        source = Path(temporary) / "source.sqlite3"
        destination = Path(temporary) / "sanitized.sqlite3"
        source.write_bytes(payload)
        # External snapshots may retain the WAL header without its sidecars.
        # Normalize only this disposable copy before opening the backup read-only.
        connection = sqlite3.connect(source)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        build_database(source, destination, False)
        return destination.read_bytes()


def export_bundle(args):
    data_dir = Path(args.data).resolve()
    if not (data_dir / "town.sqlite3").is_file():
        raise SystemExit("这个目录里没有 town.sqlite3：%s" % data_dir)
    if lock_held(data_dir) and not args.allow_running:
        raise SystemExit("小镇正在运行。请先完全退出应用再导出，避免数据库与成果文件不一致。")

    entries, notes = collect(data_dir, args.include_materials)
    import tempfile
    handle, temporary = tempfile.mkstemp(suffix=".sqlite3", prefix="town-bundle-")
    os.close(handle)
    removed = build_database(data_dir / "town.sqlite3", Path(temporary), args.strip_chat)
    entries = [(relative, Path(temporary) if relative == "town.sqlite3" else path)
               for relative, path in entries]
    if args.strip_chat:
        notes.append("已移除聊天正文：%s" % json.dumps(removed, ensure_ascii=False))

    bundled_db = next(source for relative, source in entries if relative == "town.sqlite3")
    redactions = []
    files = {}
    total = 0
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, source in entries:
            if relative == "provider.json":
                kept, dropped = scrub_provider(source)
                redactions.extend(dropped)
                payload = json.dumps(kept, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
                archive.writestr("data/" + relative, payload)
                files["data/" + relative] = dict(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
                total += len(payload)
                continue
            if source.suffix.lower() in (".md", ".txt", ".json", ".html", ".csv"):
                hits = scan_secrets(source)
                if hits:
                    redactions.append("%s 命中疑似凭据特征：%s" % (relative, "; ".join(hits)))
            archive.write(source, "data/" + relative)
            sha, size = digest(source), source.stat().st_size
            files["data/" + relative] = dict(sha256=sha, bytes=size)
            total += size

        manifest = dict(
            format=FORMAT, app_version=version(),
            exported_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            source=os.path.basename(str(data_dir)), file_count=len(files), total_bytes=total,
            database=summarise(bundled_db),
            chat_stripped=bool(args.strip_chat),
            excluded=notes + sorted(ALWAYS_EXCLUDED_DIRS) + sorted(ALWAYS_EXCLUDED_FILES),
            redactions=sorted(set(redactions)),
            credentials="不含任何模型密钥：密钥保存在操作系统的凭据管理器里，按数据目录路径命名，换机器必须重新填写。",
            files=files)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    if redactions:
        print("注意：")
        for note in redactions:
            print("  - " + note)
    print("已导出 %s" % output)
    print("  文件 %d 个，共 %.1f MB" % (len(files), total / (1 << 20)))
    print("  记录：%s" % json.dumps(manifest["database"], ensure_ascii=False))
    if args.strip_chat:
        print("  聊天正文已移除（%s）" % json.dumps(removed, ensure_ascii=False))
    if notes:
        for note in notes:
            print("  " + note)
    return 0


def read_manifest(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        try:
            raw = archive.read("manifest.json")
        except KeyError:
            raise SystemExit("不是小镇状态包：缺少 manifest.json") from None
        return json.loads(raw.decode("utf-8"))


def inspect_bundle(args):
    manifest = read_manifest(args.bundle)
    if manifest.get("format") != FORMAT:
        raise SystemExit("状态包格式不匹配：%s" % manifest.get("format"))
    print("格式：%s" % manifest["format"])
    print("应用版本：%s（导出于 %s）" % (manifest.get("app_version"), manifest.get("exported_at")))
    print("文件 %d 个，共 %.1f MB" % (manifest["file_count"], manifest["total_bytes"] / (1 << 20)))
    print("记录：%s" % json.dumps(manifest.get("database") or {}, ensure_ascii=False))
    print("排除：%s" % ", ".join(manifest.get("excluded") or []))
    print("凭据：%s" % manifest.get("credentials", ""))
    if manifest.get("redactions"):
        print("脱敏记录：")
        for note in manifest["redactions"]:
            print("  - " + note)
    return 0


def restore_bundle(args):
    data_dir = Path(args.data).resolve()
    manifest = read_manifest(args.bundle)
    if manifest.get("format") != FORMAT:
        raise SystemExit("状态包格式不匹配：%s" % manifest.get("format"))

    occupied = (data_dir / "town.sqlite3").exists() or (data_dir / "workspaces").exists()
    if occupied and not args.force:
        raise SystemExit("目标数据目录已有小镇数据：%s\n确认要覆盖请加 --force（建议先自行备份）。" % data_dir)

    data_dir.mkdir(parents=True, exist_ok=True)
    missing = []
    with zipfile.ZipFile(args.bundle) as archive:
        names = set(archive.namelist())
        for relative, meta in manifest["files"].items():
            portable_name = relative.casefold()
            target_name = portable_name[len("data/"):] if portable_name in ("data/town.sqlite3", "data/provider.json") else relative[len("data/"):]
            target = data_dir / target_name
            resolved = target.resolve()
            relative_target = resolved.relative_to(data_dir) if resolved.is_relative_to(data_dir) else resolved
            if private_work_path(relative) or private_work_path(relative_target):
                print("  已跳过包内居民私有资料、会话、研发工作区或本机技能")
                continue
            if local_budget_verification_path(relative) or local_budget_verification_path(target.resolve()):
                print("  已跳过包内本机模型预算验证档案；" + BUDGET_REVERIFY_NOTICE)
                continue
            if (not relative.startswith("data/") or "\\" in relative or "\x00" in relative
                    or any(part in ("", ".", "..") or ":" in part or part != part.rstrip(" .") for part in relative.split("/"))
                    or data_dir not in resolved.parents):
                raise SystemExit("状态包文件路径无效，未恢复该文件")
            if relative not in names:
                missing.append(relative)
                continue
            payload = archive.read(relative)
            if hashlib.sha256(payload).hexdigest() != meta["sha256"]:
                raise SystemExit("校验失败，包内文件已被改动：%s" % relative)
            if portable_name == "data/provider.json":
                try:
                    provider = json.loads(payload.decode("utf-8"))
                except (UnicodeError, ValueError):
                    raise SystemExit("包内 provider.json 无法解析，未恢复模型设置") from None
                provider, redactions = provider_fields(provider)
                payload = json.dumps(provider, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
                for note in redactions:
                    print("  " + note)
            if portable_name == "data/town.sqlite3":
                payload = sanitized_database(payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        # Restore the private mode the town uses for resident work areas.
        for folder in (data_dir / "workspaces").glob("*"):
            if folder.is_dir():
                os.chmod(folder, 0o700)
    if missing:
        print("包内缺少清单里的文件：%s" % ", ".join(missing))
    print("已恢复到 %s" % data_dir)
    print("  应用版本：%s" % manifest.get("app_version"))
    print("  记录：%s" % json.dumps(manifest.get("database") or {}, ensure_ascii=False))
    print("下一步：打开小镇后重新填写模型密钥（它不随数据包迁移）。")
    print("  " + BUDGET_REVERIFY_NOTICE)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出或恢复星栖小镇的便携状态包")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="把数据目录打包成可迁移的状态包")
    export.add_argument("--data", required=True, help="小镇数据目录（包含 town.sqlite3）")
    export.add_argument("--out", required=True, help="输出的 zip 路径")
    export.add_argument("--include-materials", action="store_true", help="同时打包星探下载的材料（约 65 MB）")
    export.add_argument("--allow-running", action="store_true", help="允许在小镇运行时导出（不推荐，可能不一致）")
    export.add_argument("--strip-chat", action="store_true",
                        help="移除聊天正文，只保留训练、成果、预算与审计记录，适合放到公开仓库")
    export.set_defaults(handler=export_bundle)

    inspect = sub.add_parser("inspect", help="查看状态包内容与校验信息，不解压")
    inspect.add_argument("bundle")
    inspect.set_defaults(handler=inspect_bundle)

    restore = sub.add_parser("restore", help="把状态包恢复到一个数据目录")
    restore.add_argument("bundle")
    restore.add_argument("--data", required=True, help="目标数据目录")
    restore.add_argument("--force", action="store_true", help="允许覆盖已有数据")
    restore.set_defaults(handler=restore_bundle)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
