"""Only the package, its declared UI assets and runtime dependencies are bundled."""
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'packaging/windows_entry.py')], pathex=[str(root / 'src')],
             binaries=[], datas=[(str(root / 'src/hamrahi_backup/web'), 'hamrahi_backup/web')],
             hiddenimports=['watchdog.observers.read_directory_changes'],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest', 'unittest'],
             noarchive=False, optimize=1)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='AbreHamrahiAPI',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=True, icon=str(root / 'src/hamrahi_backup/web/app.ico'))
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='AbreHamrahiAPI')
