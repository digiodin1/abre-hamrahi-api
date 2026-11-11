"""Commands that work from a source checkout and a standalone Windows bundle."""
import os
from pathlib import Path
import sys


def executable():
    path = Path(sys.executable).resolve()
    if not getattr(sys, 'frozen', False) and os.name == 'nt' and path.with_name('pythonw.exe').is_file():
        path = path.with_name('pythonw.exe')
    return path


def command(role, directory, port):
    if role not in ('serve', 'desktop'):
        raise ValueError('Unknown application role')
    if getattr(sys, 'frozen', False):
        prefix = [str(executable())] + (['--serve'] if role == 'serve' else [])
    else:
        runner = 'startup_runner.py' if role == 'serve' else 'desktop_runner.py'
        prefix = [str(executable()), str(Path(__file__).with_name(runner).resolve())]
    return prefix + ['--data-dir', str(Path(directory).expanduser().resolve()), '--port', str(port)] + (['--no-browser'] if role == 'serve' else [])
