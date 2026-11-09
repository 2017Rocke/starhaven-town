"""Windows desktop shell for Starhaven Town.

Starts the private local backend and hosts the town UI in a native WebView2 window.
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


def acquire_single_instance():
    if sys.platform != "win32":
        return None
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.CreateMutexW(None, False, "Local\\StarhavenTownSingleInstance")
    if not handle:
        raise RuntimeError("无法创建单实例锁。")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return False
    return handle


def release_single_instance(handle):
    if not handle:
        return
    import ctypes
    ctypes.windll.kernel32.CloseHandle(handle)


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


def load_webview():
    try:
        import webview  # type: ignore
        return webview
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "桌面窗口组件不可用。请使用官方 Windows 安装包，或安装 Microsoft Edge WebView2 运行时后重试。"
        ) from exc


def main() -> int:
    if sys.platform != "win32":
        print("此启动器仅用于 Windows。", file=sys.stderr)
        return 2

    mutex = acquire_single_instance()
    if mutex is False:
        # Another instance owns the mutex; try to keep UX simple.
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "星栖小镇已经在运行。", APP_NAME, 0x40)
        except Exception:
            print("星栖小镇已经在运行。", file=sys.stderr)
        return 0

    root = app_root()
    data_dir = data_directory()
    process = None
    ready_file = None
    try:
        process, payload, ready_file = start_backend(root, data_dir)
        url = "http://127.0.0.1:%s/" % payload["port"]
        webview = load_webview()
        window = webview.create_window(
            APP_NAME,
            url=url,
            width=1280,
            height=850,
            min_size=(1000, 700),
            confirm_close=False,
            text_select=True,
        )

        def _watch():
            while True:
                if process.poll() is not None:
                    try:
                        window.destroy()
                    except Exception:
                        pass
                    return
                time.sleep(0.5)

        threading.Thread(target=_watch, daemon=True).start()
        webview.start(gui="edgechromium")
        return 0
    except Exception as exc:  # noqa: BLE001
        append_log(data_dir, traceback.format_exc())
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "无法打开星栖小镇：\n%s\n\n详细日志：%%LOCALAPPDATA%%\\StarhavenTown\\logs\\desktop-windows.log" % exc,
                APP_NAME,
                0x10,
            )
        except Exception:
            print(exc, file=sys.stderr)
        return 1
    finally:
        stop_backend(process, ready_file) if process is not None else None
        release_single_instance(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
