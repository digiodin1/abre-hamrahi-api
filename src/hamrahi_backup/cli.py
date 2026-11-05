"""Small desktop launcher. Persistent state is outside the installation directory."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import threading
import webbrowser

from . import __version__
from .engine import BackupManager
from .server import PanelServer


def default_data_dir():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'AbrHamrahiAPI'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')) / 'abr-hamrahi-api'


@contextmanager
def instance_lock(directory):
    """OS lock releases on exit/crash. Do not remove the persistent lock file."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = (directory / 'instance.lock').open('a+b')
    locked = False
    try:
        try:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0')
                stream.flush()
        except PermissionError:
            stream.close()
            raise RuntimeError('Another process is using this data directory.') from None
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise RuntimeError('Another process is using this data directory. Close it first.') from None
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Abre Hamrahi API: local versioned backup panel.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--port', type=int, default=8765, help='Loopback panel port (default: 8765)')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(), help='Private local settings/session directory')
    parser.add_argument('--no-browser', action='store_true', help='Run the panel and scheduler without opening a browser')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    directory = args.data_dir.expanduser().resolve()
    try:
        with instance_lock(directory):
            manager = BackupManager(directory, state_dir=directory, port=args.port)
            server = PanelServer(manager, args.port)
            scheduler = threading.Thread(target=manager.scheduler, daemon=True, name='backup-scheduler')
            scheduler.start()
            url = f'http://127.0.0.1:{server.server_port}/'
            print(f'Abre Hamrahi API {__version__}: {url}', flush=True)
            print('Keep this process running for scheduled backups. Press Ctrl+C to stop.', flush=True)
            if os.name != 'nt':
                print('Account login requires Windows DPAPI; this platform supports offline preview/testing only.', flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                print('Stopping; waiting for the current bounded network request...', flush=True)
            finally:
                shutdown_failed = False
                manager.close()
                server.server_close()
                scheduler.join(timeout=2)
                if manager.work_lock.acquire(timeout=195):
                    manager.work_lock.release()
                else:
                    print('Shutdown timed out; the interrupted file was not confirmed. Run backup again.', file=sys.stderr)
                    shutdown_failed = True
            if shutdown_failed:
                return 1
    except (OSError, RuntimeError) as error:
        # Never echo paths or response bodies from arbitrary errors.
        print('Cannot start: another instance, unavailable port, or inaccessible data directory. ' + type(error).__name__, file=sys.stderr)
        return 1
    return 0
