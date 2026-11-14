"""Private backend process owned by the native desktop application."""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import sys
import time

# The bundled runtime uses -I, so imports are limited to this trusted application
# directory plus the runtime's standard library (never the user's Python packages).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import BASE_DIR, create_server


def _secure_chmod(path, mode):
    try:
        path.chmod(mode)
    except OSError:
        # Windows may ignore POSIX permission bits; keep going.
        pass


def migrate_legacy(data_dir, legacy_dir):
    """Copy an existing database with SQLite's online backup; never move/delete it."""
    data_dir, legacy_dir = Path(data_dir), Path(legacy_dir)
    target = data_dir / "town.sqlite3"
    original = legacy_dir / "data" / "town.sqlite3"
    if target.exists() or not original.is_file() or original.is_symlink():
        return False
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = data_dir / "migration.sqlite3"
    source = dest = None
    try:
        if pending.exists():
            pending.unlink()
        source = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
        dest = sqlite3.connect(str(pending))
        try:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("原有聊天数据校验未通过，已保留原文件。")
            dest.commit()
        finally:
            dest.close()
            dest = None
            source.close()
            source = None
        _secure_chmod(pending, 0o600)
        pending.replace(target)
    finally:
        if dest is not None:
            try:
                dest.close()
            except Exception:
                pass
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            # Windows may briefly keep a handle; migration result already replaced or failed closed.
            pass
    return True


def prepare(data_dir, legacy_dir=None):
    data_dir = Path(data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings_file = data_dir / "settings.json"
    settings = {}
    if settings_file.exists():
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("小镇本机设置格式有误，请检查 settings.json。")
    if legacy_dir:
        legacy_dir = Path(legacy_dir).expanduser().resolve()
        migrate_legacy(data_dir, legacy_dir)
        if (legacy_dir.parent / "config.json").is_file() and not settings.get("source_dir"):
            settings["source_dir"] = str(legacy_dir.parent)
            settings["report_root"] = str(legacy_dir.parent.parent / "collect-skill" / "daily")
            settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            _secure_chmod(settings_file, 0o600)
    source = Path(settings.get("source_dir") or data_dir / "team").expanduser().resolve()
    reports = Path(settings.get("report_root") or source.parent / "collect-skill" / "daily").expanduser().resolve()
    return data_dir, source, reports


def _parent_running(pid):
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # QUERY_LIMITED_INFORMATION works for an unelevated parent and keeps x64 handles intact.
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--stop-file", type=Path, help="Private desktop shutdown signal")
    parser.add_argument("--legacy-dir", type=Path)
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--owner-stdin", action="store_true", help="Read private native confirmation secret from inherited stdin")
    args = parser.parse_args()
    data_dir, source, reports = prepare(args.data_dir, args.legacy_dir)
    if args.migrate_only:
        print("原有数据已复制或目标数据已存在；原文件保持不变。")
        return
    owner_secret = None
    if args.owner_stdin:
        import re
        owner_secret = sys.stdin.readline(130).strip()
        if not re.fullmatch(r"[a-f0-9]{64}", owner_secret):
            raise RuntimeError("原生确认通道初始化失败")
    server = create_server(port=0, data_dir=data_dir, source_dir=source, report_root=reports, web_dir=BASE_DIR / "web", owner_secret=owner_secret)
    stopping = threading.Event()

    def shutdown(*unused):
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def watch_parent():
        while not stopping.wait(0.5):
            if args.stop_file and args.stop_file.is_file():
                shutdown()
                return
            # Prefer explicit parent pid so Windows and detached launchers still stop cleanly.
            if args.parent_pid:
                if not _parent_running(args.parent_pid):
                    shutdown()
                    return
            elif os.getppid() == 1 or (sys.platform == "win32" and os.getppid() == 0):
                shutdown()
                return

    if args.parent_pid or args.stop_file:
        if args.parent_pid and not _parent_running(args.parent_pid):
            server.server_close()
            return
        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        if args.ready_file:
            payload = dict(port=server.server_port, pid=os.getpid(), version=(BASE_DIR / "VERSION").read_text().strip())
            temporary = args.ready_file.with_suffix(".pending")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            _secure_chmod(temporary, 0o600)
            temporary.replace(args.ready_file)
        server.serve_forever(poll_interval=0.2)
    finally:
        stopping.set()
        server.server_close()
        if args.ready_file:
            args.ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
