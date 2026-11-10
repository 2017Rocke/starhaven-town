"""Check the relocated Windows runtime without the builder's Python environment."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def pe_machine(path):
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Not a Windows executable: " + str(path))
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError("Invalid Windows PE header: " + str(path))
        return struct.unpack("<H", stream.read(2))[0]


def verify_runtime(runtime, check_https=False):
    if sys.platform != "win32":
        raise RuntimeError("The Windows runtime must be checked on Windows")
    runtime = Path(runtime).resolve(strict=True)
    python = runtime / "python.exe"
    if pe_machine(python) != 0x8664:
        raise RuntimeError("Bundled Python must be x64")
    # CPython's redistributable DLLs must ship app-local, not rely on the build agent.
    if not (runtime / "vcruntime140.dll").is_file():
        raise RuntimeError("Bundled Python is missing app-local vcruntime140.dll")
    for path in runtime.rglob("*"):
        if path.is_symlink() and runtime not in path.resolve().parents:
            raise RuntimeError("Runtime symlink leaves its bundle: " + str(path))
    script = r'''
import ctypes, http.server, json, pathlib, sqlite3, ssl, sys, threading, urllib.request
root = pathlib.Path(sys.argv[1]).resolve()
def inside(path):
    target = pathlib.Path(path).resolve()
    return target == root or root in target.parents
assert sys.version_info[:2] == (3, 12), 'Use the documented Python 3.12 runtime'
assert sys.flags.isolated and sys.flags.utf8_mode and sys.dont_write_bytecode
assert inside(sys.executable) and inside(sys.prefix) and inside(sys.base_prefix)
assert all(inside(path) for path in sys.path), 'Python imports escape the bundle'
for module in (ctypes, http.server, json, pathlib, sqlite3, ssl, threading, urllib.request):
    if getattr(module, '__file__', None): assert inside(module.__file__)
db = sqlite3.connect(':memory:')
assert db.execute('select 1').fetchone() == (1,)
db.close()
context = ssl.create_default_context()
assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
if sys.argv[2] == 'https':
    with urllib.request.urlopen('https://www.python.org/', context=context, timeout=40) as response:
        assert response.status == 200
print(json.dumps({'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
    'isolated': True, 'utf8': True, 'tls_verification': True, 'https_checked': sys.argv[2] == 'https'}))
'''
    with tempfile.TemporaryDirectory(prefix="starhaven-runtime-") as temporary:
        environment = {key: value for key, value in os.environ.items() if key.upper() in (
            "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "SYSTEMDRIVE")}
        environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
        output = subprocess.check_output([str(python), "-I", "-X", "utf8", "-B", "-c", script, str(runtime), "https" if check_https else "local"],
            cwd=temporary, env=environment, text=True, encoding="utf-8", timeout=70)
    return json.loads(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--https", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_runtime(args.runtime, args.https), indent=2))
