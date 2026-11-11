"""Standalone Windows entry point. No developer machine paths or state."""
import argparse
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import sys

from hamrahi_backup import __version__
from hamrahi_backup.cli import default_data_dir, main as serve
from hamrahi_backup.desktop import main as desktop, open_panel
from hamrahi_backup.startup import StartupRegistration


def main():
    multiprocessing.freeze_support()
    for stream in ('stdout', 'stderr'):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, 'w', encoding='utf-8'))
    args = sys.argv[1:]
    if args[:1] == ['--folder-picker']:
        if len(args) != 2:
            return 2
        from hamrahi_backup.folder_picker import choose_folder
        Path(args[1]).write_text(json.dumps(choose_folder(), ensure_ascii=False), encoding='utf-8')
        return 0
    if args[:1] == ['--diagnostics']:
        if len(args) != 2:
            return 2
        import tkinter
        from importlib.resources import files
        from hamrahi_backup.engine import protect
        from watchdog.observers import Observer
        root = tkinter.Tk()
        root.withdraw()
        try:
            tk_version = root.tk.call('info', 'patchlevel')
        finally:
            root.destroy()
        probe = b'Abre Hamrahi API synthetic encryption check'
        assert protect(protect(probe), decrypt=True) == probe
        assert files('hamrahi_backup').joinpath('web', 'backup.html').is_file()
        Path(args[1]).write_text(json.dumps({'version': __version__, 'frozen': bool(getattr(sys, 'frozen', False)),
            'tk': tk_version, 'dpapiRoundTrip': True, 'webAssets': True,
            'nativeObserver': Observer.__name__}), encoding='utf-8')
        return 0
    if args == ['--remove-startup']:
        StartupRegistration(default_data_dir(), 8765).configure(False)
        return 0
    if args[:1] == ['--create-shortcuts']:
        from hamrahi_backup.shortcuts import main as shortcuts
        return shortcuts(args[1:])
    if args[:1] == ['--serve']:
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, False, 'Local\\AbreHamrahiAPI.Desktop')
        if not handle:
            return 1
        try:
            return serve(args[1:])
        finally:
            kernel.CloseHandle(handle)
    if args[:1] == ['--headless-open']:
        parser = argparse.ArgumentParser()
        parser.add_argument('--port', type=int, required=True)
        parser.add_argument('--data-dir', type=Path, required=True)
        parsed = parser.parse_args(args[1:])
        if not 1 <= parsed.port <= 65535:
            return 2
        return open_panel(parsed.data_dir, parsed.port, open_browser=lambda _: True)
    if args == ['--version']:
        return serve(args)
    return desktop(args)


if __name__ == '__main__':
    raise SystemExit(main())
