"""Windows desktop shell for Starhaven Town.

Developer helpers for the private backend and compiled native WebView2 host.
API keys are never written into the project tree; they stay in Windows Credential Manager.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback

APP_NAME = "星栖小镇"
BACKEND_NAMES = (
    "server.py",
    "desktop_runner.py",
    "model_bridge.py",
    "provider_bridge.py",
    "credential_store.py",
    "VERSION",
)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "StarhavenTown"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backend_directory(root: Path) -> Path:
    bundled = root / "backend"
    if (bundled / "desktop_runner.py").is_file():
        return bundled
    return root


def python_executable(root: Path, backend: Path) -> Path:
    candidates = [
        backend / "runtime" / "python.exe",
        backend / "runtime" / "pythonw.exe",
        root / "runtime" / "python.exe",
        Path(sys.executable),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("未找到内置 Python 运行时，请重新下载完整 Windows 安装包。")


def wait_ready(ready_file: Path, process: subprocess.Popen, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("小镇后端启动失败，请查看日志后重试。")
        if ready_file.is_file():
            try:
                payload = json.loads(ready_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("port"):
                    return payload
            except (OSError, ValueError):
                pass
        time.sleep(0.05)
    raise RuntimeError("启动超时：小镇窗口没有在限定时间内准备好。")


def append_log(data_dir: Path, message: str):
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "desktop-windows.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message.rstrip() + "\n")


def start_backend(root: Path, data_dir: Path):
    backend = backend_directory(root)
    missing = [name for name in BACKEND_NAMES if not (backend / name).exists()]
    if missing:
        raise FileNotFoundError("安装包不完整，缺少：" + ", ".join(missing))
    if not (backend / "web" / "index.html").is_file():
        raise FileNotFoundError("安装包不完整，缺少 web 资源。")

    python = python_executable(root, backend)
    ready_dir = Path(tempfile.mkdtemp(prefix="starhaven-win-"))
    ready_file = ready_dir / "ready.json"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        [
            str(python),
            "-I",
            "-X",
            "utf8",
            "-B",
            str(backend / "desktop_runner.py"),
            "--data-dir",
            str(data_dir),
            "--ready-file",
            str(ready_file),
            "--parent-pid",
            str(os.getpid()),
        ],
        cwd=str(backend),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        payload = wait_ready(ready_file, process)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        raise
    return process, payload, ready_file


def stop_backend(process: subprocess.Popen, ready_file: Path | None):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except Exception:
            process.kill()
            try:
                process.wait(timeout=3)
            except Exception:
                pass
    if ready_file is not None:
        try:
            ready_file.unlink(missing_ok=True)
            ready_file.parent.rmdir()
        except OSError:
            pass


def main() -> int:
    """Developer convenience entry point; release shortcuts use the native EXE."""
    if sys.platform != "win32":
        print("此启动器仅用于 Windows。", file=sys.stderr)
        return 2
    root = app_root()
    executable = root / "StarhavenTown.exe"
    if not executable.is_file():
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        executable = root / "build" / "windows-x64" / ("StarhavenTown-" + version + "-windows-x64") / "StarhavenTown.exe"
    if not executable.is_file():
        print("请先运行 scripts/build_windows.py 构建原生窗口，或下载完整 Windows 安装包。", file=sys.stderr)
        return 2
    return subprocess.call([str(executable)])


if __name__ == "__main__":
    raise SystemExit(main())
