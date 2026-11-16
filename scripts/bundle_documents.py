"""Install pinned image/PDF wheels into the shipped interpreter, not the host."""
import json
from pathlib import Path
import platform
import subprocess
import sys

SMOKE = '''
import io
from PIL import Image
import pypdfium2 as pdfium
for kind in ('PNG', 'JPEG', 'WEBP'):
    data = io.BytesIO()
    with Image.new('RGB', (80, 60), 'blue') as source:
        source.save(data, kind)
    with Image.open(io.BytesIO(data.getvalue())) as decoded:
        decoded.load()
        assert decoded.size == (80, 60)
document = pdfium.PdfDocument.new()
page = document.new_page(100, 120)
bitmap = page.render(scale=1)
picture = bitmap.to_pil()
assert picture.size == (100, 120)
picture.close(); bitmap.close(); page.close(); document.close()
print('Bundled PNG/JPEG/WebP codecs and PDF renderer verified')
'''


def normalize_macos_wheels(destination, runtime):
    """Remove wheel builder search paths; dependencies must still resolve locally.

    Wheels sometimes retain unused absolute LC_RPATHs from their CI builder.
    The application's full runtime_check remains authoritative after this step.
    Never rewrite an external dependency to make that check pass.
    """
    import os
    from runtime_check import MACH_O, _read_commands, _system
    root = Path(runtime).resolve()
    for binary in sorted(Path(destination).rglob('*')):
        if not binary.is_file() or binary.is_symlink(): continue
        with binary.open('rb') as stream:
            if stream.read(4) not in MACH_O: continue
        _, runpaths = _read_commands(binary, dict(os.environ))
        changed = False
        for entry in runpaths:
            if not entry.startswith('/'): continue
            path = Path(entry).resolve()
            if path == root or root in path.parents or _system(path): continue
            subprocess.run(['/usr/bin/install_name_tool', '-delete_rpath', entry, str(binary)], check=True)
            changed = True
        if changed:
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(binary)], check=True)


def install_document_runtime(runtime, windows=False):
    runtime = Path(runtime)
    executable = runtime / ('python.exe' if windows else 'bin/python3')
    info = json.loads(subprocess.check_output([str(executable), '-I', '-B', '-c',
        'import sys,json;print(json.dumps(dict(version="%s.%s"%sys.version_info[:2],abi="cp%s%s"%sys.version_info[:2])))'], text=True))
    destination = runtime / ('Lib/site-packages' if windows else 'lib/python' + info['version'] + '/site-packages')
    wheel_platform = 'win_amd64' if windows else ('macosx_11_0_arm64' if platform.machine() == 'arm64' else 'macosx_10_13_x86_64')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile',
        '--only-binary=:all:', '--no-deps', '--upgrade', '--target', str(destination),
        '--python-version', info['version'], '--implementation', 'cp', '--abi', info['abi'],
        '--platform', wheel_platform, '-r', str(Path(__file__).resolve().parents[1] / 'requirements-runtime.txt')], check=True)
    if not windows: normalize_macos_wheels(destination, runtime)
    subprocess.run([str(executable), '-I', '-B', '-c', SMOKE], check=True)
