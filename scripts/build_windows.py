#!/usr/bin/env python3
"""Assemble a portable Windows 10 x64 (Intel/AMD) package for Starhaven Town."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "StarhavenTown"
DISPLAY_NAME = "星栖小镇"


def run(*args, **kwargs):
    print("+", " ".join(str(part) for part in args))
    subprocess.check_call([str(part) for part in args], **kwargs)


def write_png(path: Path, rgba_rows):
    height = len(rgba_rows)
    width = len(rgba_rows[0]) // 4
    raw = b"".join(b"\x00" + row for row in rgba_rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def generate_icon_png(path: Path, size: int = 64):
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_icon import pixels  # type: ignore

    canvas = pixels()
    # nearest-neighbor scale from 64
    rows = []
    for y in range(size):
        row = bytearray()
        src_y = min(63, y * 64 // size)
        for x in range(size):
            src_x = min(63, x * 64 // size)
            row.extend(canvas[src_y][src_x])
        rows.append(bytes(row))
    write_png(path, rows)


def copy_runtime(src: Path, dest: Path):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=False)
    # Ensure python.exe exists for console-less launcher fallback.
    if not (dest / "python.exe").exists():
        raise RuntimeError("Python runtime missing python.exe: " + str(src))


def _site_packages(runtime: Path, python: Path) -> Path:
    script = "import sysconfig; print(sysconfig.get_path('purelib'))"
    out = subprocess.check_output([str(python), "-c", script], text=True).strip()
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_webview(python: Path, runtime: Path):
    """Install WebView host into the bundled runtime without mutating uv's external marker."""
    target = _site_packages(runtime, python)
    # Use the build host interpreter's pip to drop wheels into the runtime site-packages.
    host = Path(sys.executable)
    run(
        host,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        "--target",
        str(target),
        "pywebview>=5.1",
        "pythonnet>=3.0.3",
        "clr_loader>=0.2.6",
        "proxy_tools",
        "bottle",
        "typing_extensions",
    )
    # Smoke import inside the target interpreter.
    run(python, "-c", "import webview, sys; print('webview', getattr(webview, '__version__', 'unknown'), sys.version)")


def write_launcher(package: Path):
    # pythonw keeps a desktop-app feel without a console window.
    cmd = package / "启动星栖小镇.cmd"
    cmd.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if exist \"backend\\runtime\\pythonw.exe\" (\r\n"
        "  start \"\" \"backend\\runtime\\pythonw.exe\" -I -B \"backend\\windows_app.py\"\r\n"
        ") else (\r\n"
        "  \"backend\\runtime\\python.exe\" -I -B \"backend\\windows_app.py\"\r\n"
        ")\r\n",
        encoding="utf-8",
    )
    ps1 = package / "启动星栖小镇.ps1"
    ps1.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "Set-Location -LiteralPath $PSScriptRoot\n"
        "$pythonw = Join-Path $PSScriptRoot 'backend\\runtime\\pythonw.exe'\n"
        "$python = Join-Path $PSScriptRoot 'backend\\runtime\\python.exe'\n"
        "$app = Join-Path $PSScriptRoot 'backend\\windows_app.py'\n"
        "if (Test-Path -LiteralPath $pythonw) {\n"
        "  Start-Process -FilePath $pythonw -ArgumentList @('-I','-B', $app)\n"
        "} else {\n"
        "  & $python -I -B $app\n"
        "}\n",
        encoding="utf-8",
    )
    readme = package / "使用说明.txt"
    readme.write_text(
        DISPLAY_NAME + " Windows 10 x64 便携版\n"
        "================================\n\n"
        "1. 解压到任意目录（建议路径不含中文空格以外的特殊权限限制）。\n"
        "2. 双击「启动星栖小镇.cmd」。\n"
        "3. 首次对话前，在右上角「小镇设置」连接你自己的模型服务。\n"
        "4. API 密钥保存在 Windows 凭据管理器，不会写入本解压目录或 Git 仓库。\n"
        "5. 个人数据目录：%LOCALAPPDATA%\\StarhavenTown\\\n"
        "6. 需要 Microsoft Edge WebView2 运行时（Windows 10/11 通常已自带）。\n"
        "7. 本包面向 64 位 Windows 10（Intel/AMD x86_64）。\n",
        encoding="utf-8",
    )


def build_package(runtime_src: Path, output_dir: Path, version: str) -> Path:
    build = ROOT / "build" / "windows-x64"
    if build.exists():
        shutil.rmtree(build)
    package = build / f"{APP_NAME}-{version}-windows-x64"
    backend = package / "backend"
    backend.mkdir(parents=True)

    for name in (
        "server.py",
        "desktop_runner.py",
        "model_bridge.py",
        "provider_bridge.py",
        "credential_store.py",
        "VERSION",
    ):
        shutil.copy2(ROOT / name, backend / name)
    shutil.copy2(ROOT / "desktop" / "windows_app.py", backend / "windows_app.py")
    shutil.copytree(ROOT / "web", backend / "web")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, package / name)

    runtime = backend / "runtime"
    copy_runtime(runtime_src, runtime)
    python = runtime / "python.exe"
    install_webview(python, runtime)

    icon = package / "app-icon.png"
    generate_icon_png(icon)
    write_launcher(package)

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{APP_NAME}-{version}-windows-x64.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(build).as_posix())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output_dir / (archive.name + ".sha256")).write_text(checksum + "  " + archive.name + "\n", encoding="utf-8")
    print("Windows package:", archive)
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-runtime", type=Path, required=True, help="Windows x64 Python prefix containing python.exe")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Build the Windows package on a Windows x64 machine (or GitHub Actions windows-latest).")
    if os.environ.get("PROCESSOR_ARCHITECTURE", "").lower() not in ("amd64", "x86_64") and os.environ.get("PROCESSOR_ARCHITEW6432", "").lower() not in ("amd64", "x86_64"):
        # Allow build on 64-bit Windows; PROCESSOR_ARCHITECTURE is AMD64 on x64.
        arch = os.environ.get("PROCESSOR_ARCHITECTURE", "")
        if arch.upper() != "AMD64":
            print("Warning: expected AMD64 host, found", arch)
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    build_package(args.python_runtime.resolve(), ROOT / "dist", version)


if __name__ == "__main__":
    main()
