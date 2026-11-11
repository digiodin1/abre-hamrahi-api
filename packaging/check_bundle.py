"""Check bundle privacy and stripped application code paths before publication."""
import json
import marshal
from pathlib import Path, PureWindowsPath
import re
import types

from PyInstaller.archive.readers import CArchiveReader


def check_bundle(bundle, source_root):
    bundle, source_root = Path(bundle), Path(source_root).resolve()
    forbidden = {'.qa', '.backup', '.git', 'config.json', 'history.json', 'session.bin',
                 'session.dpapi', 'index.sqlite3', 'active-run.json', 'last-test.json'}
    markers = [str(source_root).encode('utf-8'), str(source_root).encode('utf-16-le')]
    secrets = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
               rb'gh[pousr]_[A-Za-z0-9_]{30,}', rb'github_pat_[A-Za-z0-9_]{40,}',
               rb'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}']
    count = 0
    for path in bundle.rglob('*'):
        assert not set(path.relative_to(bundle).parts) & forbidden, 'Private runtime filename found in bundle'
        if path.is_file():
            raw = path.read_bytes()
            assert not any(marker in raw for marker in markers), 'Developer path found in bundle'
            assert not any(re.search(pattern, raw) for pattern in secrets), 'Credential marker found in bundle'
            count += 1
    archive = CArchiveReader(str(bundle / 'AbreHamrahiAPI.exe'))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    modules = [pyz.extract(name) for name in pyz.toc if name.startswith('hamrahi_backup')]
    modules.append(marshal.loads(archive.extract('windows_entry')))
    def inspect(code):
        assert not PureWindowsPath(code.co_filename).is_absolute(), 'Absolute path remains in application bytecode'
        for value in code.co_consts:
            if isinstance(value, types.CodeType):
                inspect(value)
            elif isinstance(value, str):
                assert str(source_root).casefold() not in value.casefold(), 'Developer path remains in application constant'
                assert not any(re.search(pattern, value.encode('utf-8')) for pattern in secrets), 'Credential marker found in application code'
    for code in modules:
        inspect(code)
    report = {'bundlePrivacy': 'passed', 'payloadFiles': count, 'applicationModules': len(modules),
              'applicationCodePaths': 'relative'}
    print(json.dumps(report))
    return report
