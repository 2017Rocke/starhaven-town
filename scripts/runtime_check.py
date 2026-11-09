"""Reject a bundled Python runtime that still depends on the build machine.

This checks static load commands and performs a real isolated import smoke test.
It deliberately fails closed for dynamic-linker paths it cannot resolve safely.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


MACH_O = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
SYSTEM_ROOTS = (Path("/usr/lib"), Path("/System/Library"))


def _inside(path, root):
    return path == root or root in path.parents


def _system(path):
    return any(_inside(path, root) for root in SYSTEM_ROOTS)


def _read_commands(binary, environment):
    linked = subprocess.check_output(["/usr/bin/otool", "-L", str(binary)],
                                     text=True, env=environment, timeout=30)
    commands = subprocess.check_output(["/usr/bin/otool", "-l", str(binary)],
                                       text=True, env=environment, timeout=30)
    dependencies = []
    for line in linked.splitlines():
        match = re.match(r"\s+(.+?)\s+\(compatibility version .+\)\s*$", line)
        if match:
            dependencies.append(match.group(1))
    runpaths, install_names, command = [], set(), ""
    for line in commands.splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            command = stripped[4:]
        elif command == "LC_RPATH" and stripped.startswith("path "):
            match = re.fullmatch(r"path (.+) \(offset \d+\)", stripped)
            if not match:
                raise RuntimeError("Unrecognized runtime loader path in " + str(binary))
            runpaths.append(match.group(1))
            command = ""
        elif command == "LC_ID_DYLIB" and stripped.startswith("name "):
            match = re.fullmatch(r"name (.+) \(offset \d+\)", stripped)
            if not match:
                raise RuntimeError("Unrecognized runtime library identity in " + str(binary))
            install_names.add(match.group(1))
            command = ""
    # `otool -L` also prints a dylib's own install name; that is not an import.
    return [dependency for dependency in dependencies if dependency not in install_names], runpaths


def _expand(path, loader, executable):
    for prefix, directory in (("@loader_path", loader.parent), ("@executable_path", executable.parent)):
        if path == prefix:
            return directory.resolve()
        if path.startswith(prefix + "/"):
            return (directory / path[len(prefix) + 1:]).resolve()
    if path.startswith("/"):
        # Resolve symlinks and '..' before accepting a system path.
        return Path(path).resolve()
    raise RuntimeError("Cannot safely resolve runtime loader path: " + path)


def _permitted(path, runtime):
    if not _inside(path, runtime) and not _system(path):
        raise RuntimeError("Bundled runtime refers to an external library or runpath: " + str(path))


def verify_runtime(runtime):
    """Validate a copied runtime prefix; return concise evidence for build logs."""
    runtime = Path(runtime).resolve(strict=True)
    executable = runtime / "bin" / "python3"
    if not runtime.is_dir() or not executable.is_file():
        raise RuntimeError("Runtime needs bin/python3 and its own standard library")
    if not os.access(str(executable), os.X_OK):
        raise RuntimeError("Bundled Python is not executable")
    paths = list(runtime.rglob("*"))
    for path in paths:
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError("Bundled runtime has a broken or cyclic symlink: " + str(path)) from exc
            if not _inside(target, runtime):
                raise RuntimeError("Bundled runtime symlink escapes its bundle: " + str(path))
    binaries = []
    for path in paths:
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACH_O:
                    binaries.append(path)
    if executable.resolve() not in {path.resolve() for path in binaries}:
        raise RuntimeError("Bundled Python is not a Mach-O executable")

    with tempfile.TemporaryDirectory(prefix="starhaven-runtime-check-") as temporary:
        # Never inherit the developer's Python, DYLD, virtualenv, key or proxy settings.
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": temporary, "TMPDIR": temporary,
            "LANG": "en_US.UTF-8", "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        }
        commands = {path: _read_commands(path, environment) for path in binaries}
        executable_real = executable.resolve()
        main_runpaths = commands[executable_real][1] if executable_real in commands else commands[executable][1]
        main_paths = [_expand(item, executable_real, executable_real) for item in main_runpaths]
        for binary, (dependencies, runpaths) in commands.items():
            paths_for_binary = [_expand(item, binary, executable_real) for item in runpaths] + main_paths
            for directory in paths_for_binary:
                _permitted(directory, runtime)
            for dependency in dependencies:
                if dependency.startswith("@rpath/"):
                    suffix = dependency[len("@rpath/"):]
                    candidates = [(directory / suffix).resolve() for directory in paths_for_binary]
                    for candidate in candidates:
                        _permitted(candidate, runtime)
                    # Apple libraries can exist only in the dyld shared cache.
                    if not any((_inside(candidate, runtime) and candidate.is_file()) or _system(candidate)
                               for candidate in candidates):
                        raise RuntimeError("Cannot resolve bundled dependency %s used by %s" % (dependency, binary))
                else:
                    target = _expand(dependency, binary, executable_real)
                    _permitted(target, runtime)
                    if _inside(target, runtime) and not target.is_file():
                        raise RuntimeError("Bundled runtime dependency is missing: " + str(target))

        script = """
import ctypes, fcntl, http.server, json, pathlib, sqlite3, ssl, sys, threading, urllib.request
root = pathlib.Path(sys.argv[1]).resolve()
def inside(value):
    path = pathlib.Path(value).resolve()
    return path == root or root in path.parents
assert inside(sys.executable), 'Interpreter escaped the bundled runtime'
assert sys.dont_write_bytecode, 'Runtime checks must not create build-path bytecode'
assert inside(sys.prefix) and inside(sys.base_prefix), 'Python prefix points outside the bundle'
assert all(inside(path) for path in sys.path), 'Python imports can escape the bundle'
for module in (ctypes, http.server, json, pathlib, sqlite3, ssl, threading, urllib.request):
    if getattr(module, '__file__', None):
        assert inside(module.__file__), 'Standard-library module escaped the bundle'
db = sqlite3.connect(':memory:')
assert db.execute('select 1').fetchone() == (1,)
db.close()
assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0, 'No trusted TLS certificates'
print(json.dumps({'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
                  'isolated': bool(sys.flags.isolated), 'bytecode_disabled': sys.dont_write_bytecode,
                  'tls_certificates': True}))
"""
        output = subprocess.check_output([str(executable), "-I", "-B", "-c", script, str(runtime)],
                                         cwd=temporary, env=environment, text=True, timeout=45)
        evidence = json.loads(output)
        if not evidence.get("isolated"):
            raise RuntimeError("Bundled interpreter did not enter isolated mode")
        evidence["mach_o_files_checked"] = len(binaries)
        return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify a macOS application's copied Python runtime")
    parser.add_argument("runtime", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify_runtime(arguments.runtime), ensure_ascii=False, indent=2))
