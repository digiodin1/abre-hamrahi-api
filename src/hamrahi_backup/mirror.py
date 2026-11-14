from pathlib import Path
import os

from .watcher import relevant_path


def prune_cloud(manager, job, config, layout, run, result, identity):
    from .engine import BackupError
    root = Path(job['path'])
    current_root = root.stat()
    if identity != (current_root.st_dev, current_root.st_ino):
        raise BackupError('پوشهٔ محلی جابه‌جا شد؛ حذف ابری متوقف شد.')
    with os.scandir(root) as entries:
        next(entries, None)
    parent = layout.parent(job, Path('inventory'))
    pending, directories = [(parent, Path())], []
    visited = set()
    while pending:
        owner, relative = pending.pop()
        if owner in visited:
            raise BackupError('ساختار پوشه‌های ابر چرخه دارد؛ حذف متوقف شد.', 503)
        visited.add(owner)
        for item in list(layout.listing(owner).values()):
            if (not isinstance(item.get('name'), str) or item['name'] in ('', '.', '..') or
                    '/' in item['name'] or '\\' in item['name']):
                raise BackupError('نام نامعتبر در فهرست ابر؛ حذف متوقف شد.', 503)
            path = relative / item['name']
            source = root / path
            if relevant_path(source, config, manager.state_dir) is None:
                continue
            if not item.get('key'):
                pending.append((item['id'], path))
                directories.append((item, source))
                continue
            manager.cancelled()
            try:
                source.stat()
                continue
            except FileNotFoundError:
                pass
            current_root = root.stat()
            if identity != (current_root.st_dev, current_root.st_ino):
                raise BackupError('پوشهٔ محلی حین سینک جابه‌جا شد؛ حذف ابری متوقف شد.')
            manager.update(phase='همگام‌سازی حذف فایل‌ها', current=str(source), fileStage='trash')
            try:
                manager.client.trash(item)
            except Exception as error:
                manager.record_item(run, source, 'error', int(item.get('size', 0)), error=str(error), remote_id=item['id'])
                raise
            layout.listing(owner).pop(item['name'], None)
            with manager.db() as database:
                database.execute('DELETE FROM files WHERE job=? AND rel=? AND remote_id=?',
                                 (job['id'], path.as_posix(), item['id']))
                database.execute('DELETE FROM source_fingerprints WHERE job=? AND rel=?',
                                 (job['id'], path.as_posix()))
            result['trashed'] = result.get('trashed', 0) + 1
            manager.record_item(run, source, 'trashed', int(item.get('size', 0)), remote_id=item['id'])
    for item, source in reversed(directories):
        manager.cancelled()
        try:
            source.stat()
            continue
        except FileNotFoundError:
            pass
        if manager.client.list_folder(item['id']):
            continue
        current_root = root.stat()
        if identity != (current_root.st_dev, current_root.st_ino):
            raise BackupError('پوشهٔ محلی تغییر کرد؛ حذف پوشهٔ ابری متوقف شد.')
        manager.client.trash(item)
        layout.listing(item['parent']).pop(item['name'], None)
        result['trashed'] = result.get('trashed', 0) + 1
