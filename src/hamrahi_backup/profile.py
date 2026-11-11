"""Remember an explicitly selected local desktop profile across upgrades."""
import json
import os
from pathlib import Path
import tempfile


def load_profile(home):
    home = Path(home)
    try:
        path = home / 'launcher.json'
        if path.stat().st_size > 16384:
            raise ValueError('Profile is too large')
        value = json.loads(path.read_text('utf-8'))
        directory = Path(value['directory'])
        port = value['port']
        if not directory.is_absolute() or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid desktop profile')
        return directory, port
    except (OSError, ValueError, KeyError, TypeError):
        return home, 8765


def remember_profile(home, directory, port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    directory = Path(directory).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('Profile directory is not a directory')
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='launcher-', suffix='.tmp', dir=home)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'directory': str(directory), 'port': port}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, home / 'launcher.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
