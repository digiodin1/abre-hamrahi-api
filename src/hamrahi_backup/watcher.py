"""Recursive filesystem notifications with a polling fallback for unsupported volumes."""
import fnmatch
import os
from pathlib import Path
import threading
import time


def relevant_path(raw, config, state_dir):
    if not raw:
        return None
    path = Path(os.path.abspath(raw))
    if path.name.endswith(('-wal', '-journal')):
        path = path.with_name(path.name.rsplit('-', 1)[0])
    for candidate in (path, *path.parents):
        try:
            if candidate.is_symlink() or bool(getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400):
                return None
        except OSError:
            pass
    try:
        path = path.resolve()
    except (OSError, RuntimeError):
        return None
    for folder in config['folders']:
        if not folder['enabled']:
            continue
        root = Path(folder['path'])
        try:
            path.relative_to(root)
        except ValueError:
            continue
        for candidate in (path, *path.parents):
            if candidate == root.parent:
                break
            try:
                candidate.resolve().relative_to(state_dir)
                return None
            except ValueError:
                pass
            if candidate != root:
                relative = candidate.relative_to(root).as_posix().lower()
                if any(fnmatch.fnmatchcase(relative, pattern.replace('\\', '/').lower()) or
                       fnmatch.fnmatchcase(candidate.name.lower(), pattern.lower()) for pattern in config['exclude']):
                    return None
        return path
    return None


class ChangeMonitor:
    def __init__(self, notify):
        self.notify = notify
        self.observer = None
        self.signature = None
        self.retry_at = 0
        self.lock = threading.RLock()
        self.status = {'watching': 0, 'backend': None, 'error': None}

    def configure(self, config, state_dir):
        with self.lock:
            return self._configure(config, state_dir)

    def _configure(self, config, state_dir):
        active = config['automatic'] and config['syncOnChange']
        roots = [folder['path'] for folder in config['folders'] if folder['enabled']] if active else []
        available = [root for root in roots if Path(root).is_dir() and relevant_path(root, config, state_dir)]
        signature = (tuple(roots), tuple(available), tuple(config['exclude']))
        alive = self.observer is not None and self.observer.is_alive() and all(
            emitter.is_alive() for emitter in self.observer.emitters)
        retrying = self.observer is None and self.retry_at and time.monotonic() < self.retry_at
        if signature == self.signature and (alive or retrying):
            return dict(self.status)
        self.close()
        self.signature = signature
        self.retry_at = 0
        self.status = {'watching': 0, 'backend': None, 'error': None}
        if not available:
            if roots:
                self.status['error'] = 'پوشهٔ فعال در دسترس نیست؛ با اتصال دوباره، پایش از سر گرفته می‌شود.'
                self.retry_at = time.monotonic() + 30
            return dict(self.status)
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            observer = PollingObserver(self.notify, available, config, state_dir)
            observer.start()
            self.observer = observer
            self.status.update(watching=len(available), backend='polling-fallback')
            return dict(self.status)
        notify = self.notify

        class Events(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type not in ('created', 'modified', 'deleted', 'moved', 'closed'):
                    return
                if event.is_directory and event.event_type == 'modified':
                    return
                for raw in (event.src_path, getattr(event, 'dest_path', None)):
                    path = relevant_path(raw, config, state_dir)
                    if path is not None:
                        notify(path)

        observer = Observer(timeout=1)
        try:
            for root in available:
                observer.schedule(Events(), root, recursive=True)
            observer.start()
            self.observer = observer
            self.status.update(watching=len(available), backend='native')
            if len(available) != len(roots):
                self.status['error'] = 'بعضی پوشه‌ها در دسترس نیستند؛ پس از اتصال دوباره بررسی می‌شوند.'
            return dict(self.status)
        except (OSError, RuntimeError):
            observer.stop()
            if observer.is_alive():
                observer.join(timeout=5)
            fallback = PollingObserver(self.notify, available, config, state_dir)
            fallback.start()
            self.observer = fallback
            self.status.update(watching=len(available), backend='polling-fallback',
                               error='رویداد native در این پوشه در دسترس نبود؛ پایش سبک دوره‌ای فعال است.')
            return dict(self.status)

    def close(self):
        with self.lock:
            if self.observer is not None:
                self.observer.stop()
                self.observer.join(timeout=5)
                self.observer = None


class PollingObserver:
    def __init__(self, notify, roots, config, state_dir):
        self.notify = notify
        self.roots = [Path(root) for root in roots]
        self.config = config
        self.state_dir = Path(state_dir)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='abr-hamrahi-file-watch')
        self.emitters = []

    def schedule(self, *_args, **_kwargs):
        return None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        self.thread.join(timeout)

    def is_alive(self):
        return self.thread.is_alive()

    def _snapshot(self):
        result = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for directory, dirs, files in os.walk(root):
                if self.stop_event.is_set():
                    return result
                dirs[:] = [name for name in dirs if relevant_path(Path(directory) / name, self.config, self.state_dir)]
                for name in files:
                    if self.stop_event.is_set():
                        return result
                    path = Path(directory) / name
                    if relevant_path(path, self.config, self.state_dir) is None:
                        continue
                    try:
                        info = path.stat()
                    except OSError:
                        continue
                    result[str(path)] = (info.st_size, info.st_mtime_ns)
        return result

    def _run(self):
        before = self._snapshot()
        while not self.stop_event.wait(2):
            after = self._snapshot()
            changed = set(before) ^ set(after)
            changed.update(path for path in set(before) & set(after) if before[path] != after[path])
            for path in changed:
                self.notify(Path(path))
            before = after
