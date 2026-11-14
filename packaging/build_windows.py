"""Build a self-contained x64 Windows app and per-user installer."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile
from check_bundle import check_bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Build on Windows x64')
    root = Path(__file__).resolve().parents[1]
    compiler = args.iscc.expanduser().resolve(strict=True)
    version = tomllib.loads((root / 'pyproject.toml').read_text('utf-8'))['project']['version']
    out = root / 'dist' / 'windows'
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(out), '--workpath', str(root / 'build' / 'windows'),
        str(root / 'packaging' / 'AbreHamrahiAPI.spec')], cwd=root, check=True)
    notices = out / 'AbreHamrahiAPI' / 'licenses'
    notices.mkdir(exist_ok=True)
    shutil.copyfile(root / 'NOTICE.md', notices / 'Project-NOTICE.md')
    shutil.copyfile(Path(sys.base_prefix) / 'LICENSE.txt', notices / 'Python-LICENSE.txt')
    shutil.copyfile(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms', notices / 'Tk-license.terms')
    for package in ('pyinstaller', 'watchdog'):
        distribution = importlib.metadata.distribution(package)
        for item in distribution.files:
            if item.name.lower().startswith(('license', 'copying')):
                shutil.copyfile(distribution.locate_file(item), notices / (package + '-' + item.name))
    check_bundle(out / 'AbreHamrahiAPI', root)
    subprocess.run([str(compiler), '/Qp', '/DAppVersion=' + version,
        str(root / 'packaging' / 'windows.iss')], cwd=root, check=True)
    archive = out / f'AbreHamrahiAPI-{version}-windows-x64-portable.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted((out / 'AbreHamrahiAPI').rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(out))
        zipped.write(root / 'LICENSE', 'AbreHamrahiAPI/LICENSE')
        zipped.write(root / 'docs/windows-install.md', 'AbreHamrahiAPI/README.txt')
    assets = [out / f'AbreHamrahiAPI-{version}-windows-x64-setup.exe', archive]
    (out / 'SHA256SUMS.txt').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in assets), encoding='ascii')
    metadata = {'version': version, 'platform': 'windows-x64', 'python': platform.python_version(),
                'assets': [{'name': path.name, 'bytes': path.stat().st_size} for path in assets]}
    (out / 'build-info.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
