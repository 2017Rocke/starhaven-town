#!/usr/bin/env python3
"""Build a per-user installer and portable Windows 10 1809+ x64 package."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "StarhavenTown"
DISPLAY_NAME = "星栖小镇"
BACKEND_FILES = ("server.py", "desktop_runner.py", "model_bridge.py", "provider_bridge.py", "credential_store.py",
                 "scout_workshop.py", "scout_collect.py", "scout_scan.py", "scout_journey.py", "town_security.py", "town_roads.py", "VERSION")


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


WEBVIEW_SDK_VERSION = "1.0.3537.50"
WEBVIEW_SDK_SHA256 = "5ea526bbd728adda0da4d31219267e96460494a427e4894c4e09d9f320f4b9aa"


def copy_runtime(src: Path, dest: Path):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    if not (dest / "python.exe").is_file():
        raise RuntimeError("Python runtime missing python.exe: " + str(src))


def copy_backend(destination: Path):
    """Copy the public application and its shipped defaults, never user data."""
    destination.mkdir(parents=True)
    for name in BACKEND_FILES:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "web", destination / "web")
    (destination / "defaults").mkdir()
    shutil.copy2(ROOT / "defaults" / "residents.json", destination / "defaults" / "residents.json")


def generate_icon_ico(png: Path, icon: Path):
    # A PNG-backed ICO is supported on every Windows version we target.
    payload = png.read_bytes()
    icon.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 64, 64, 0, 0, 1, 32, len(payload), 22) + payload)


def download_webview_sdk(cache: Path) -> Path:
    import urllib.request
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ("webview2-" + WEBVIEW_SDK_VERSION + ".nupkg")
    if not archive.is_file():
        url = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{0}/microsoft.web.webview2.{0}.nupkg".format(WEBVIEW_SDK_VERSION)
        with urllib.request.urlopen(url, timeout=90) as response:
            payload = response.read(20 * 1024 * 1024)
        if hashlib.sha256(payload).hexdigest() != WEBVIEW_SDK_SHA256:
            raise RuntimeError("Microsoft WebView2 SDK checksum mismatch")
        archive.write_bytes(payload)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != WEBVIEW_SDK_SHA256:
        raise RuntimeError("Cached Microsoft WebView2 SDK checksum mismatch; remove " + str(archive))
    return archive


def build_native_host(package: Path):
    sdk = download_webview_sdk(ROOT / "build" / "downloads")
    with zipfile.ZipFile(sdk) as archive:
        members = {
            "lib/net462/Microsoft.Web.WebView2.Core.dll": "Microsoft.Web.WebView2.Core.dll",
            "lib/net462/Microsoft.Web.WebView2.WinForms.dll": "Microsoft.Web.WebView2.WinForms.dll",
            "build/native/x64/WebView2Loader.dll": "WebView2Loader.dll",
            "LICENSE.txt": "LICENSE-WebView2.txt",
        }
        for source, target in members.items():
            (package / target).write_bytes(archive.read(source))
    from runtime_check_windows import pe_machine
    if pe_machine(package / "WebView2Loader.dll") != 0x8664:
        raise RuntimeError("WebView2 native loader must be x64")
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError(".NET Framework C# compiler is missing: " + str(compiler))
    # Built-in Framework compiler targets the old Framework API surface, not modern .NET.
    run(compiler, "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/debug-", "/utf8output", "/codepage:65001",
        "/out:" + str(package / "StarhavenTown.exe"),
        "/win32icon:" + str(package / "app-icon.ico"),
        "/win32manifest:" + str(ROOT / "desktop" / "windows.manifest"),
        "/reference:System.dll", "/reference:System.Core.dll", "/reference:System.Drawing.dll",
        "/reference:System.Windows.Forms.dll", "/reference:System.Web.Extensions.dll",
        "/reference:" + str(package / "Microsoft.Web.WebView2.Core.dll"),
        "/reference:" + str(package / "Microsoft.Web.WebView2.WinForms.dll"),
        ROOT / "desktop" / "WindowsHost.cs")
    (package / "StarhavenTown.exe.config").write_text(
        '<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.7.2"/></startup></configuration>\n', encoding="utf-8")


def write_launcher(package: Path, version: str):
    (package / "使用说明.txt").write_text(
        DISPLAY_NAME + " Windows x64 版\n\n"
        "安装版：双击 Setup.exe 安装，仅当前用户使用，不需要管理员权限。\n"
        "便携版：完整解压后双击 StarhavenTown.exe；不要单独移动 exe。支持中文、空格路径。\n"
        "系统：Windows 10 1809（内部版本 17763）及以上，64 位 Intel/AMD 处理器。\n"
        "已内置 Python，无需自己安装 Python、Node.js 或 pywebview。\n"
        "需要 .NET Framework 4.7.2（标准 Windows 10 1809 自带）和 Microsoft Edge WebView2。\n"
        "旧系统未必自带 WebView2；首次启动会提供微软官方安装入口。选 Evergreen Standalone Installer x64。\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/#download-section\n"
        "AI 对话请在「小镇设置」连接自己的模型服务；密钥保存在 Windows 凭据管理器。\n"
        "数据默认在 %LOCALAPPDATA%\\StarhavenTown，不可写时回退到程序旁的 data 文件夹；菜单可打开实际目录。\n"
        "升级与卸载保留用户数据；便携版换目录前请备份，回退目录里的数据不会自动迁移。\n"
        "1809 默认使用 --no-sandbox 兼容模式，会关闭浏览器进程沙箱；较新系统初始化失败时也会尝试该模式。\n"
        "支持使用程序旁 WebView2Runtime 文件夹内的固定版运行时，本安装包默认不包含该目录。\n"
        f"上游 1.5.1 记录 Windows 10.0.17763.379 启动自检通过；{version} 完整功能与安装升级尚待该系统验收。\n"
        "微软支持要求安装适用累计更新；古老补丁可能影响运行时安装与 HTTPS。\n"
        "当前 EXE 尚未代码签名，系统可能显示未知发布者；请从官方 GitHub Releases 下载。\n",
        encoding="utf-8-sig",
    )


def checksum(path: Path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(digest + "  " + path.name + "\n", encoding="utf-8")


def find_inno_setup() -> Path:
    candidates = [shutil.which("ISCC.exe")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base:
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("Install Inno Setup 6 from https://jrsoftware.org/isdl.php before building the installer.")


def build_package(runtime_src: Path, output_dir: Path, version: str) -> Path:
    import re
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("VERSION must contain a numeric three-part release version")
    build = ROOT / "build" / "windows-x64"
    if build.exists():
        shutil.rmtree(build)
    package = build / f"{APP_NAME}-{version}-windows-x64"
    backend = package / "backend"
    copy_backend(backend)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, package / name)
    runtime = backend / "runtime"
    copy_runtime(runtime_src, runtime)
    from runtime_check_windows import verify_runtime
    print("Bundled runtime:", verify_runtime(runtime, check_https=True))
    generate_icon_png(package / "app-icon.png")
    generate_icon_ico(package / "app-icon.png", package / "app-icon.ico")
    build_native_host(package)
    write_launcher(package, version)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{APP_NAME}-{version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(build).as_posix())
    checksum(archive)
    run(find_inno_setup(), "/DAppVersion=" + version, "/DPackageDir=" + str(package),
        "/DOutputDir=" + str(output_dir), ROOT / "desktop" / "windows_installer.iss")
    installer = output_dir / f"{APP_NAME}-{version}-windows-x64-Setup.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup did not produce the installer")
    checksum(installer)
    print("Windows installer:", installer)
    print("Windows portable:", archive)
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-runtime", type=Path, required=True, help="Relocatable Windows x64 Python 3.12 prefix containing python.exe")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows x64 (or GitHub Actions windows-2022).")
    if struct.calcsize("P") != 8:
        raise SystemExit("Use a 64-bit Python build host")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    build_package(args.python_runtime.resolve(), ROOT / "dist", version)


if __name__ == "__main__":
    main()
