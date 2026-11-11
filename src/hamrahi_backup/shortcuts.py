"""Windows shortcuts using the Shell COM API; no script-policy changes."""
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .cli import default_data_dir


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def create_link(destination, target, arguments, working_directory):
    if os.name != 'nt':
        raise OSError('Desktop shortcuts require Windows')
    ole = ctypes.OleDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    clsid = GUID.parse('00021401-0000-0000-c000-000000000046')
    shell_iid = GUID.parse('000214f9-0000-0000-c000-000000000046')
    persist_iid = GUID.parse('0000010b-0000-0000-c000-000000000046')
    link, persist = ctypes.c_void_p(), ctypes.c_void_p()
    initialized = False
    def invoke(pointer, index, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(pointer, *args)
        if result < 0:
            raise OSError('Windows shortcut operation failed')
        return result
    try:
        try:
            ole.CoInitializeEx(None, 2)
            initialized = True
        except OSError as error:
            if getattr(error, 'winerror', None) != -2147417850:
                raise
        ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(shell_iid), ctypes.byref(link))
        invoke(link, 20, [wintypes.LPCWSTR], str(target))
        invoke(link, 11, [wintypes.LPCWSTR], arguments)
        invoke(link, 9, [wintypes.LPCWSTR], str(working_directory))
        invoke(link, 7, [wintypes.LPCWSTR], 'Start or open Abre Hamrahi API')
        invoke(link, 15, [ctypes.c_int], 7)
        invoke(link, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
               ctypes.byref(persist_iid), ctypes.byref(persist))
        invoke(persist, 6, [wintypes.LPCWSTR, wintypes.BOOL], str(destination), True)
    finally:
        for pointer in (persist, link):
            if pointer:
                invoke(pointer, 2, [])
        if initialized:
            ole.CoUninitialize()


def create_shortcuts(directory, port, desktop=None):
    if os.name != 'nt':
        raise OSError('Desktop shortcuts require Windows')
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    if desktop is None:
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer):
            raise OSError('Windows desktop directory is unavailable')
        desktop = Path(buffer.value)
    desktop = Path(desktop)
    executable = Path(sys.executable)
    if executable.with_name('pythonw.exe').is_file():
        executable = executable.with_name('pythonw.exe')
    runner = Path(__file__).with_name('desktop_runner.py')
    arguments = subprocess.list2cmdline([str(runner), '--data-dir', str(Path(directory).resolve()),
                                       '--port', str(port)])
    link = desktop / 'Abre Hamrahi API.lnk'
    create_link(link, executable, arguments, runner.parent)
    (desktop / 'Abre Hamrahi Cloud.url').write_text(
        '[InternetShortcut]\nURL=https://abrehamrahi.ir/drive/files\n', encoding='ascii')
    return link


def main(argv=None):
    parser = argparse.ArgumentParser(description='Create current-user Windows desktop shortcuts.')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        create_shortcuts(args.data_dir, args.port)
    except OSError:
        print('Cannot create desktop shortcuts. Check Windows and desktop permissions.', file=sys.stderr)
        return 1
    print('Created desktop app launcher and cloud shortcuts.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
