#!/usr/bin/env python3
"""Reproducible source-to-bundle build, with an explicitly supplied Python runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

from generate_icon import generate
from runtime_check import verify_runtime

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "星栖小镇"
BACKEND_FILES = ("server.py", "desktop_runner.py", "model_bridge.py", "provider_bridge.py", "credential_store.py",
                 "scout_workshop.py", "scout_collect.py", "scout_scan.py", "scout_journey.py", "VERSION")


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def plist(destination, executable, identifier, name, version, minimum):
    content = dict(CFBundleName=name, CFBundleDisplayName=name, CFBundleIdentifier=identifier,
                   CFBundleExecutable=executable, CFBundlePackageType="APPL", CFBundleIconFile="AppIcon",
                   CFBundleShortVersionString=version, CFBundleVersion=version, NSHighResolutionCapable=True,
                   LSMinimumSystemVersion=minimum, NSPrincipalClass="NSApplication",
                   NSAppTransportSecurity={"NSAllowsLocalNetworking": True})
    with destination.open("wb") as output:
        plistlib.dump(content, output)


def bundle_skeleton(path):
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "Resources").mkdir()
    (path / "Contents" / "PkgInfo").write_bytes(b"APPL????")


def copy_backend(destination):
    """Copy the public application and its shipped defaults, never user data."""
    destination.mkdir(parents=True)
    for name in BACKEND_FILES:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "web", destination / "web")
    (destination / "defaults").mkdir()
    shutil.copy2(ROOT / "defaults" / "residents.json", destination / "defaults" / "residents.json")


def copy_runtime(runtime, destination):
    executable = runtime / "bin" / "python3"
    info = json.loads(subprocess.check_output([str(executable), "-I", "-B", "-c",
        "import json,sys,platform; print(json.dumps({'version':f'{sys.version_info.major}.{sys.version_info.minor}', 'arch':platform.machine()}))"], text=True))
    if info["arch"] != platform.machine():
        raise RuntimeError("Python runtime architecture must match this build host")
    (destination / "bin").mkdir(parents=True)
    shutil.copy2(executable.resolve(), destination / "bin" / "python3")
    # Relocatable python-build-standalone distributions keep extension libraries in lib.
    shutil.copytree(runtime / "lib", destination / "lib", symlinks=True,
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "*.pyo", "test", "tests", "ensurepip", "idlelib", "tkinter", "turtle*", "*.a", "pkgconfig", "_tkinter*"))
    for notice in runtime.glob("*LICENSE*"):
        if notice.is_file():
            shutil.copy2(notice, destination / notice.name)
    standard_license = destination / "lib" / ("python" + info["version"]) / "LICENSE.txt"
    if not standard_license.exists():
        raise RuntimeError("The selected runtime must include its Python LICENSE.txt")
    # Fail instead of shipping a runtime that needs the author's installed frameworks.
    result = subprocess.check_output(["otool", "-L", str(destination / "bin" / "python3")], text=True)
    for line in result.splitlines()[1:]:
        dependency = line.strip().split(" (", 1)[0]
        if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
            raise RuntimeError("Use a relocatable python-build-standalone runtime; external dependency: " + dependency)
    print("Bundled runtime:", json.dumps(verify_runtime(destination)))


def clean_bytecode(bundle):
    # Bytecode stores source paths from the build machine and must not be shipped.
    # Keep this final sweep even though runtime copies and smoke tests exclude it.
    for path in bundle.rglob("*"):
        if path.is_file() and path.suffix in (".pyc", ".pyo"):
            path.unlink()
    for path in sorted(bundle.rglob("__pycache__"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def sign_tree(bundle, identity):
    clean_bytecode(bundle)
    binaries = []
    for path in bundle.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                magic = f.read(4)
            if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
                binaries.append(path)
    signing_options = ["--options", "runtime", "--timestamp"] if identity != "-" else []
    for binary in binaries:
        run("codesign", "--force", "--sign", identity, *signing_options, binary)
    run("codesign", "--force", "--sign", identity, *signing_options, bundle)
    run("codesign", "--verify", "--deep", "--strict", bundle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-runtime", type=Path, required=True, help="Relocatable Python prefix (bin/python3 and lib)")
    parser.add_argument("--identity", default=os.environ.get("STARHAVEN_SIGN_IDENTITY", "-"))
    parser.add_argument("--minimum-macos", default="13.0")
    parser.add_argument("--skip-dmg", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("Build the desktop application on macOS")
    version = (ROOT / "VERSION").read_text().strip()
    arch = platform.machine()
    build = ROOT / "build" / ("macos-" + arch)
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    app = build / (APP_NAME + ".app")
    bundle_skeleton(app)
    resources = app / "Contents" / "Resources"
    backend = resources / "backend"
    copy_backend(backend)
    copy_runtime(args.python_runtime.resolve(), backend / "runtime")
    launcher = backend / "StarhavenBackend"
    launcher.write_text('#!/bin/sh\nset -eu\nbackend_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexport SSL_CERT_FILE=/etc/ssl/cert.pem\nexec "$backend_dir/runtime/bin/python3" -I -B "$backend_dir/desktop_runner.py" "$@"\n')
    launcher.chmod(0o755)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, resources / name)
    iconset = build / "AppIcon.iconset"
    generate(iconset)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "AppIcon.icns")
    target = arch + "-apple-macosx" + args.minimum_macos
    cache = ROOT / "build" / "swift-cache"
    cache.mkdir(exist_ok=True)
    run("swiftc", "-O", "-target", target, "-module-cache-path", cache, "-framework", "AppKit", "-framework", "WebKit", ROOT / "desktop" / "App.swift", "-o", app / "Contents" / "MacOS" / "StarhavenTown")
    plist(app / "Contents" / "Info.plist", "StarhavenTown", "cn.starhaven.town", APP_NAME, version, args.minimum_macos)
    sign_tree(app, args.identity)
    installer = build / "安装星栖小镇.app"
    bundle_skeleton(installer)
    installer_resources = installer / "Contents" / "Resources"
    (installer_resources / "Payload").mkdir()
    shutil.copytree(app, installer_resources / "Payload" / app.name, symlinks=True)
    shutil.copy2(resources / "AppIcon.icns", installer_resources / "AppIcon.icns")
    run("swiftc", "-O", "-target", target, "-module-cache-path", cache, "-framework", "AppKit", ROOT / "desktop" / "Installer.swift", "-o", installer / "Contents" / "MacOS" / "StarhavenInstaller")
    plist(installer / "Contents" / "Info.plist", "StarhavenInstaller", "cn.starhaven.town.installer", "安装星栖小镇", version, args.minimum_macos)
    # The nested payload was already signed; preserve its signature.
    run("codesign", "--force", "--sign", args.identity, *(["--options", "runtime", "--timestamp"] if args.identity != "-" else []), installer)
    run("codesign", "--verify", "--deep", "--strict", installer)
    stage = build / "image"
    stage.mkdir()
    shutil.copytree(installer, stage / installer.name, symlinks=True)
    (stage / "安装说明.txt").write_text("双击「安装星栖小镇」，再点击「安装并打开」。\n应用安装到个人应用程序目录，并在桌面创建图标。\n以后运行新版安装器即可升级；聊天与档案保留。\n首次AI对话请在小镇设置中连接你自己的模型服务。\n此构建" + ("为本地签名预览版，未完成Apple公证。\n" if args.identity == "-" else "已签名，发布前还需完成Apple公证。\n"), encoding="utf-8")
    if not args.skip_dmg:
        dmg = output / ("StarhavenTown-" + version + "-macOS-" + arch + ".dmg")
        run("hdiutil", "create", "-volname", APP_NAME + " " + version, "-srcfolder", stage, "-ov", "-format", "UDZO", dmg)
        checksum = hashlib.sha256(dmg.read_bytes()).hexdigest()
        (output / (dmg.name + ".sha256")).write_text(checksum + "  " + dmg.name + "\n")
        print("Installer:", dmg)
    print("Application:", app)
    print("Installer application:", installer)


if __name__ == "__main__":
    main()
