#!/usr/bin/env python3
"""Validate immutable releases and publish only a complete, verified asset set."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("macos-arm64", "macos-x86_64", "windows-x64")
MANIFEST = "release-build.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payloads(version, platform=None):
    names = {
        "macos-arm64": [f"StarhavenTown-{version}-macOS-arm64.dmg"],
        "macos-x86_64": [f"StarhavenTown-{version}-macOS-x86_64.dmg"],
        "windows-x64": [f"StarhavenTown-{version}-windows-x64-Setup.exe",
                        f"StarhavenTown-{version}-windows-x64.zip"],
    }
    selected = names[platform] if platform else sum(names.values(), [])
    return selected + [f"starhaven-town-{version}-source.zip"]


def asset_names(version, platform=None):
    return sorted(name for payload in payloads(version, platform)
                  for name in (payload, payload + ".sha256"))


def preflight(root, tag):
    require(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag), "Use an existing vMAJOR.MINOR.PATCH tag")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    require(tag == "v" + version, "Tag does not match VERSION")
    notes = root / "docs" / f"UPDATE_{version}.md"
    require(notes.is_file(), "Version update notes are missing")
    headings = notes.read_text(encoding="utf-8-sig").splitlines()
    first = headings[0] if headings else ""
    require(re.search(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])", first),
            "Update-note heading does not match VERSION")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8-sig")
    latest = re.search(r"^## ([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)", changelog, re.MULTILINE)
    require(latest and latest.group(1) == version, "Latest CHANGELOG entry does not match VERSION")
    git = lambda ref: subprocess.check_output(["git", "rev-parse", "--verify", ref], cwd=root, text=True).strip()
    commit = git("HEAD^{commit}")
    require(git(f"refs/tags/{tag}^{{commit}}") == commit, "Checkout differs from the immutable version tag")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True)
    require(not dirty.strip(), "Release checkout has uncommitted or untracked changes")
    return {"tag": tag, "version": version, "commit": commit}


def verify_files(directory, version, platform=None):
    expected = asset_names(version, platform)
    for name in expected:
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                "Missing or invalid release asset: " + name)
    for name in payloads(version, platform):
        line = (directory / (name + ".sha256")).read_text(encoding="utf-8")
        match = re.fullmatch(r"([a-f0-9]{64})  " + re.escape(name) + r"\n?", line)
        require(match and match.group(1) == sha256(directory / name), "Checksum mismatch: " + name)
    archive = directory / f"starhaven-town-{version}-source.zip"
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        require(len(set(names)) == len(names), "Duplicate source ZIP entries")
        require(all(name.startswith("starhaven-town/") and ".." not in Path(name).parts for name in names),
                "Unsafe source ZIP path")
        for required in ("VERSION", "AGENTS.md", ".agents/skills/starhaven-release/SKILL.md",
                         ".agents/skills/starhaven-release/agents/openai.yaml", f"docs/UPDATE_{version}.md"):
            require("starhaven-town/" + required in names, "Source ZIP omits " + required)
        require(package.read("starhaven-town/VERSION").decode("utf-8").strip() == version,
                "Source ZIP VERSION mismatch")
    return {name: sha256(directory / name) for name in expected}


def stamp(directory, identity, platform):
    files = verify_files(directory, identity["version"], platform)
    manifest = dict(identity, platform=platform, files=files)
    (directory / MANIFEST).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")


def assemble(artifacts, output, identity):
    require(not output.exists() or not any(output.iterdir()), "Release output must be empty")
    staged = {}
    for platform in PLATFORMS:
        directory = artifacts / ("starhaven-" + platform)
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
        require(all(manifest.get(key) == value for key, value in identity.items()),
                "Build belongs to a different tag/commit: " + platform)
        require(manifest.get("platform") == platform, "Build platform mismatch")
        files = verify_files(directory, identity["version"], platform)
        require(manifest.get("files") == files, "Build changed after validation: " + platform)
        allowed = set(files) | {MANIFEST, "source-files.txt"}
        require({item.name for item in directory.iterdir()} <= allowed, "Unexpected build files")
        for name, digest in files.items():
            if name in staged:
                require(sha256(staged[name]) == digest, "Cross-platform source archives differ: " + name)
            staged[name] = directory / name
    require(set(staged) == set(asset_names(identity["version"])), "The release must have exactly ten assets")
    output.mkdir(parents=True, exist_ok=True)
    for name, path in staged.items():
        shutil.copyfile(path, output / name)
    return verify_files(output, identity["version"])


class GitHub:
    """GitHub CLI uses GITHUB_TOKEN/GH_TOKEN; no local credential-store coupling."""
    def __init__(self, repository):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid repository")
        self.repository = repository

    def api(self, method, endpoint, data=None, upload=None):
        command = ["gh", "api", "--method", method, endpoint]
        if upload:
            command += ["-H", "Content-Type: application/octet-stream", "--input", str(upload)]
        elif data is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def download(self, tag, directory):
        subprocess.run(["gh", "release", "download", tag, "--repo", self.repository,
                        "--dir", str(directory)], check=True)


def verify_remote_assets(assets, files, directory, complete):
    names = [asset["name"] for asset in assets]
    require(len(names) == len(set(names)) and set(names) <= set(files), "Unexpected or duplicate remote asset")
    if complete:
        require(set(names) == set(files), "Remote release does not have exactly ten assets")
    for asset in assets:
        name = asset["name"]
        require(asset.get("state") == "uploaded" and asset.get("digest") == "sha256:" + files[name]
                and asset.get("size") == (directory / name).stat().st_size,
                "Existing asset differs; refusing replacement: " + name)


def verify_remote_tag(client, base, tag, commit):
    target = client.api("GET", base + "/git/ref/tags/" + tag)["object"]
    for _ in range(5):
        if target["type"] != "tag":
            break
        target = client.api("GET", base + "/git/tags/" + target["sha"])["object"]
    require(target["type"] == "commit" and target["sha"] == commit, "Remote tag differs from the verified commit")


def publish(client, identity, directory, notes, evidence):
    version, tag, commit = (identity[key] for key in ("version", "tag", "commit"))
    files = verify_files(directory, version)
    require({item.name for item in directory.iterdir()} == set(files), "Release directory must contain exactly ten assets")
    base = "repos/" + client.repository
    verify_remote_tag(client, base, tag, commit)
    runs = client.api("GET", base + "/actions/workflows/ci.yml/runs?head_sha=" + commit + "&status=completed&per_page=100")["workflow_runs"]
    successful = [run for run in runs if run.get("head_sha") == commit and run.get("conclusion") == "success"]
    require(successful, "Checks has not succeeded for this exact commit")
    # Querying releases instead of treating any API error as 'not found' keeps
    # authentication and service failures from creating a second release.
    releases, page = [], 1
    while True:
        batch = client.api("GET", base + f"/releases?per_page=100&page={page}")
        releases.extend(item for item in batch if item["tag_name"] == tag)
        if len(batch) < 100:
            break
        page += 1
    require(len(releases) <= 1, "More than one release uses this tag")
    release = releases[0] if releases else client.api("POST", base + "/releases", {
        "tag_name": tag, "target_commitish": commit, "name": "星栖小镇 " + version,
        "body": notes, "draft": True, "prerelease": False,
    })
    # target_commitish may be a branch name even when the existing tag is
    # correct. The dereferenced remote tag above is the commit authority.
    require(release.get("tag_name") == tag and release.get("body") == notes,
            "Existing release has different tag or notes; refusing replacement")
    endpoint = base + f"/releases/{release['id']}"
    assets = client.api("GET", endpoint + "/assets?per_page=100")
    verify_remote_assets(assets, files, directory, complete=not release["draft"])
    if release["draft"]:
        existing = {asset["name"] for asset in assets}
        for name in sorted(set(files) - existing):
            client.api("POST", f"https://uploads.github.com/{endpoint}/assets?name=" + quote(name), upload=directory / name)
        assets = client.api("GET", endpoint + "/assets?per_page=100")
        verify_remote_assets(assets, files, directory, complete=True)
        verify_remote_tag(client, base, tag, commit)
        release = client.api("PATCH", endpoint, {"draft": False})
    with tempfile.TemporaryDirectory() as temporary:
        downloaded = Path(temporary)
        client.download(tag, downloaded)
        require({item.name for item in downloaded.iterdir()} == set(files), "Downloaded asset set differs")
        require(verify_files(downloaded, version) == files, "Downloaded release differs from verified build")
    verify_remote_tag(client, base, tag, commit)
    record = dict(identity, release_url=release["html_url"], release_id=release["id"],
                  checks_run=successful[0]["id"], build_run=os.environ.get("GITHUB_RUN_ID"),
                  assets=files, downloaded_and_verified=True,
                  limitations=["Windows Server 2022 is not Windows 10 17763.379 validation",
                               "Default macOS builds use ad-hoc signing, without Apple notarization"])
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "stamp", "assemble", "publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--platform", choices=PLATFORMS)
    parser.add_argument("--directory", type=Path, default=ROOT / "dist")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "build" / "release-artifacts")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "release-assets")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args()
    identity = preflight(ROOT, args.tag)
    if args.action == "preflight":
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                for key, value in identity.items():
                    stream.write(f"{key}={value}\n")
        print(json.dumps(identity))
    elif args.action == "stamp":
        require(args.platform, "stamp needs --platform")
        stamp(args.directory, identity, args.platform)
    elif args.action == "assemble":
        assemble(args.artifacts, args.output, identity)
        print("Verified all three builds and ten release assets")
    else:
        notes = (ROOT / "docs" / f"UPDATE_{identity['version']}.md").read_text(encoding="utf-8")
        record = publish(GitHub(args.repository or ""), identity, args.output, notes,
                         ROOT / "build" / f"release-{identity['version']}-verification.json")
        print(record["release_url"])


if __name__ == "__main__":
    main()
