#!/usr/bin/env python3
"""Export public source using an allowlist, never the complete private workspace."""
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {"VERSION", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md", "ROADMAP.md", "CONTRIBUTING.md", "AGENTS.md", ".gitignore", "requirements-build.txt", "server.py", "model_bridge.py", "provider_bridge.py", "credential_store.py", "scout_workshop.py", "scout_collect.py", "scout_scan.py", "scout_journey.py", "town_security.py", "town_roads.py", "town_store.py", "town_artifacts.py", "town_budget.py", "town_skills.py", "town_owner_actions.py", "town_growth.py", "desktop_runner.py", "打开星栖小镇.command", "使用说明.md"}
DIRECTORIES = {"web", "desktop", "scripts", "tests", "docs", "defaults", ".github", ".agents/skills/starhaven-release"}
SUFFIXES = {".py", ".swift", ".cs", ".manifest", ".iss", ".ps1", ".md", ".json", ".html", ".css", ".js", ".svg", ".yml", ".yaml", ".png"}


def public_files(root=ROOT):
    found = []
    candidates = [root / name for name in FILES if (root / name).exists()]
    for name in DIRECTORIES:
        candidates.extend((root / name).rglob("*"))
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or "__pycache__" in relative.parts:
            continue
        if str(relative) not in FILES and path.suffix not in SUFFIXES:
            continue
        if path.stat().st_size > 5_000_000:
            raise RuntimeError("Unexpected large source file: " + str(relative))
        if path.suffix != ".png":
            content = path.read_text(encoding="utf-8")
            if re.search(r"/Users/[A-Za-z][^/\s\"']*/", content):
                raise RuntimeError("Personal absolute path in source: " + str(relative))
            if re.search(r"(?:sk-proj-|sk-)[A-Za-z0-9_-]{24,}", content):
                raise RuntimeError("Possible credential in source: " + str(relative))
        found.append(path)
    return found


def export_source(root=ROOT):
    version = (root / "VERSION").read_text().strip()
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / ("starhaven-town-" + version + "-source.zip")
    sources = public_files(root)
    # Stored entries avoid different zlib versions producing different bytes.
    # Checkout timestamps, executable bits and CRLF settings must not affect the
    # source archive built from the same commit on Mac and Windows.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as package:
        for path in sources:
            info = zipfile.ZipInfo("starhaven-town/" + path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix == ".command" else 0o100644) << 16
            payload = path.read_bytes()
            if path.suffix != ".png":
                payload = payload.replace(b"\r\n", b"\n")
            package.writestr(info, payload)
    (output / (archive.name + ".sha256")).write_bytes((hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n").encode("utf-8"))
    (output / "source-files.txt").write_bytes(("\n".join(p.relative_to(root).as_posix() for p in sources) + "\n").encode("utf-8"))
    print("Exported", len(sources), "public source files:", archive.name)
    return archive


if __name__ == "__main__":
    export_source()
