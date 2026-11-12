#!/usr/bin/env python3
"""Export public source using an allowlist, never the complete private workspace."""
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {"VERSION", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md", "ROADMAP.md", "CONTRIBUTING.md", ".gitignore", "requirements-build.txt", "server.py", "model_bridge.py", "provider_bridge.py", "credential_store.py", "scout_workshop.py", "scout_collect.py", "scout_scan.py", "scout_journey.py", "town_security.py", "desktop_runner.py", "打开星栖小镇.command", "使用说明.md"}
DIRECTORIES = {"web", "desktop", "scripts", "tests", "docs", "defaults", ".github"}
SUFFIXES = {".py", ".swift", ".cs", ".manifest", ".iss", ".ps1", ".md", ".json", ".html", ".css", ".js", ".svg", ".yml", ".yaml", ".png"}


def public_files():
    found = []
    candidates = [ROOT / name for name in FILES if (ROOT / name).exists()]
    for name in DIRECTORIES:
        candidates.extend((ROOT / name).rglob("*"))
    for path in sorted(candidates):
        relative = path.relative_to(ROOT)
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


def main():
    version = (ROOT / "VERSION").read_text().strip()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / ("starhaven-town-" + version + "-source.zip")
    sources = public_files()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sources:
            package.write(path, "starhaven-town/" + path.relative_to(ROOT).as_posix())
    (output / (archive.name + ".sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n", encoding="utf-8")
    (output / "source-files.txt").write_text("\n".join(str(p.relative_to(ROOT)) for p in sources) + "\n", encoding="utf-8")
    print("Exported", len(sources), "public source files:", archive.name)


if __name__ == "__main__":
    main()
