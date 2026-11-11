"""Opt-in, per-user Windows startup registration; never requires elevation."""
import hashlib
import ctypes
import os
from pathlib import Path
import subprocess
from .runtime import command as runtime_command


class StartupPathError(OSError):
    pass


class StartupRegistration:
    def __init__(self, data_dir, port):
        self.data_dir = Path(data_dir).resolve()
        self.port = port
        self.name = 'AbreHamrahiAPI-' + hashlib.sha256(str(self.data_dir).casefold().encode()).hexdigest()[:12]

    def command(self):
        parts = runtime_command('serve', self.data_dir, self.port)
        command = subprocess.list2cmdline(parts[:])
        # Windows Run values support at most 260 characters. Try existing 8.3
        # paths before refusing a registration that Windows would silently skip.
        # https://learn.microsoft.com/windows/win32/setupapi/run-and-runonce-registry-keys
        if len(command) > 260 and os.name == 'nt':
            for index in [position for position, part in enumerate(parts) if Path(part).is_absolute()]:
                buffer = ctypes.create_unicode_buffer(32768)
                length = ctypes.windll.kernel32.GetShortPathNameW(parts[index], buffer, len(buffer))
                if 0 < length < len(buffer):
                    parts[index] = buffer.value
            command = subprocess.list2cmdline(parts)
        if len(command) > 260:
            raise StartupPathError('Startup command is too long; use a shorter installation or data path')
        return command

    def read(self):
        if os.name != 'nt':
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                return winreg.QueryValueEx(key, self.name)[0]
        except FileNotFoundError:
            return None

    def write(self, command):
        if os.name != 'nt':
            if command is not None:
                raise OSError('Windows startup is unavailable on this platform')
            return
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run',
                                0, winreg.KEY_SET_VALUE) as key:
            if command is None:
                try:
                    winreg.DeleteValue(key, self.name)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, command)

    def configure(self, enabled):
        self.write(self.command() if enabled else None)

    def status(self):
        try:
            registered = self.read()
            return {'supported': os.name == 'nt', 'registered': bool(registered and registered == self.command()), 'error': None}
        except OSError:
            return {'supported': os.name == 'nt', 'registered': False,
                    'error': 'وضعیت اجرای خودکار ویندوز قابل بررسی نیست.'}
