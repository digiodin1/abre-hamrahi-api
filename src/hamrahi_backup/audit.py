"""Read-only inventory and content verification of configured backup folders."""
import hashlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid

from .engine import BackupError, atomic_json, is_link, now


def revision_stamp(metadata):
    if not isinstance(metadata, dict) or not metadata.get('key') or not metadata.get('etag'):
        return None
    version = {key: metadata.get(key) for key in ('id', 'key', 'etag', 'size', 'version', 'last_modified')}
    return hashlib.sha256(json.dumps(version, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verified_receipt(manager, identifier, digest, size, metadata):
    revision = revision_stamp(metadata)
    if not revision:
        return None
    with manager.db() as database:
        record = database.execute('SELECT sha,size,revision,verified_at FROM verified_objects WHERE remote_id=?',
                                  (identifier,)).fetchone()
    lifetime = min(3600, manager.config['intervalMinutes'] * 60)
    if (record and record[:3] == (digest, size, revision)
            and 0 <= time.time() - record[3] < lifetime):
        return record[3]
    return None


def verify_object(manager, identifier, digest, size, metadata=None, reuse=False):
    receipt = verified_receipt(manager, identifier, digest, size, metadata) if reuse else None
    if receipt is not None:
        return receipt, True
    with manager.db() as database:
        database.execute('DELETE FROM verified_objects WHERE remote_id=?', (identifier,))
    observed = manager.client.verify(identifier, digest, size, cancelled=manager.cancelled, metadata=metadata)
    observed = observed if isinstance(observed, dict) else metadata
    revision = revision_stamp(observed)
    verified_at = time.time()
    if revision and observed.get('id') == identifier and observed.get('size') == size:
        with manager.db() as database:
            database.execute('INSERT OR REPLACE INTO verified_objects VALUES (?,?,?,?,?)',
                             (identifier, digest, size, revision, verified_at))
    return verified_at, False


def config_stamp(config):
    return hashlib.sha256(json.dumps({key: config[key] for key in
        ('remoteRoot', 'folders', 'exclude', 'maxFileMiB', 'settleSeconds', 'verifyUploads')},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def volume_stamp(config):
    scope = {'remoteRoot': config['remoteRoot'], 'folders': [
        {key: job[key] for key in ('id', 'path', 'name')} for job in config['folders']]}
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def folder_summaries(manager):
    with manager.db() as database:
        saved = {job: json.loads(payload) for job, payload in
                 database.execute('SELECT job,payload FROM folder_audits')}
        for job, payload in database.execute('SELECT job,payload FROM folder_volumes'):
            volume = json.loads(payload)
            if job not in saved or (volume.get('measuredAt', 0), volume['checkedAt']) > (
                    saved[job].get('measuredAt', 0), saved[job]['checkedAt']):
                saved[job] = volume
    for job, volume in getattr(manager, 'live_folder_stats', {}).items():
        if job not in saved or (volume.get('measuredAt', 0), volume['checkedAt']) > (
                saved[job].get('measuredAt', 0), saved[job]['checkedAt']):
            saved[job] = volume
    stamp = config_stamp(manager.config)
    volume_scope = volume_stamp(manager.config)
    fresh = (getattr(manager, 'audit_generation', None) == manager.change_generation
             and not manager.state['busy'] and not manager.state.get('pendingSync'))
    return {job['id']: saved[job['id']] | {'stale': not fresh or
            saved[job['id']].get('configStamp') != stamp,
            'scopeChanged': (saved[job['id']]['volumeStamp'] != volume_scope
                if 'volumeStamp' in saved[job['id']] else saved[job['id']].get('configStamp') != stamp)} for job in manager.config['folders']
            if job['id'] in saved}


def match_summary(manager, summaries):
    jobs = [job for job in manager.config['folders'] if job['enabled']]
    observations = [summaries.get(job['id']) for job in jobs]
    known = bool(jobs) and all(item is not None and not item.get('scopeChanged')
                              and not item.get('sizeUnreadable') for item in observations)
    total_bytes = sum(item.get('localTotalBytes', item.get('localBytes', 0)) for item in observations if item)
    eligible_bytes = sum(item.get('localBytes', 0) for item in observations if item)
    cloud_bytes = sum(item.get('cloudBytes', 0) for item in observations if item)
    largest = max(total_bytes, cloud_bytes)
    percent = (100 * min(total_bytes, cloud_bytes) / largest if largest else 100) if known else None
    stale = any(item.get('stale', True) for item in observations if item)
    return {'percent': percent, 'complete': known and cloud_bytes == total_bytes,
            'known': known, 'stale': stale, 'checking': False,
            'totalBytes': total_bytes, 'cloudBytes': cloud_bytes, 'matchedBytes': cloud_bytes,
            'eligibleBytes': eligible_bytes, 'excludedBytes': max(0, total_bytes - eligible_bytes),
            'remainingBytes': max(0, total_bytes - cloud_bytes),
            'extraBytes': max(0, cloud_bytes - total_bytes),
            'checkedAt': min((item['checkedAt'] for item in observations if item), default=None),
            'scope': 'physical-folder-volume', 'assurance': 'volume-similarity'}


def folder_files(manager, data):
    job = next((job for job in manager.config['folders'] if job['id'] == data.get('job')), None)
    offset = data.get('offset', 0)
    if not job or type(offset) is not int or not 0 <= offset <= 100000000:
        raise BackupError('پوشه یا شمارهٔ صفحه معتبر نیست.')
    with manager.db() as database:
        total = database.execute('SELECT count(*) FROM audit_files WHERE job=?', (job['id'],)).fetchone()[0]
        rows = database.execute('SELECT rel,local_size,cloud_size,remote_id,status,error FROM audit_files '
            'WHERE job=? ORDER BY CASE status WHEN \'error\' THEN 0 ELSE 1 END,rel LIMIT 100 OFFSET ?',
            (job['id'], offset)).fetchall()
        summary = database.execute('SELECT payload FROM folder_audits WHERE job=?', (job['id'],)).fetchone()
    return {'job': job['id'], 'offset': offset, 'total': total,
            'assurance': json.loads(summary[0]).get('assurance', 'sha256') if summary else None,
            'run': json.loads(summary[0])['run'] if summary else None,
            'items': [dict(zip(('relative', 'localBytes', 'cloudBytes', 'remoteId', 'status', 'error'), row)) |
                      {'path': str(Path(job['path']) / row[0])} for row in rows]}


def repair_paths(manager):
    paths = set()
    with manager.db() as database:
        for job in manager.config['folders']:
            if not job['enabled']:
                continue
            summary = database.execute('SELECT payload FROM folder_audits WHERE job=?', (job['id'],)).fetchone()
            if not summary:
                continue
            run = json.loads(summary[0])['run']
            for relative, in database.execute("SELECT rel FROM audit_files WHERE job=? AND status='error'", (job['id'],)):
                path = Path(job['path']) / relative
                if path.is_file():
                    paths.add(str(manager.selected_item({'run': run, 'path': str(path)}, require_error=True)))
    if not paths:
        raise BackupError('فایل قابل تلاش مجددی در آخرین بررسی وجود ندارد؛ ابتدا بررسی کامل را اجرا کنید.')
    return paths


def remote_tree(manager, parent):
    files, errors, seen = {}, [], set()
    by_parent = None
    if hasattr(manager.client, 'list_tree'):
        by_parent, identities = {}, set()
        for item in manager.client.list_tree(parent):
            if item['id'] in identities:
                raise BackupError('صفحه‌بندی فهرست ابری عضو تکراری دارد.', 503)
            identities.add(item['id'])
            by_parent.setdefault(item.get('parent'), []).append(item)
    pending = [(parent, '', 0)]
    while pending:
        manager.cancelled()
        folder, prefix, depth = pending.pop()
        if folder in seen or depth > 128 or len(seen) > 100000:
            raise BackupError('ساختار پوشه‌های ابر چرخه یا عمق نامعتبر دارد.', 503)
        seen.add(folder)
        groups = {}
        for item in by_parent.get(folder, []) if by_parent is not None else manager.client.list_folder(folder):
            name = item.get('name')
            if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\\' in name:
                raise BackupError('نام عضو پوشهٔ ابری معتبر نیست.', 503)
            groups.setdefault(name, []).append(item)
        for name, matches in groups.items():
            relative = prefix + name
            if len(matches) != 1:
                errors.append('نام تکراری در ابر: ' + relative)
                continue
            item = matches[0]
            if item.get('key'):
                if type(item.get('size')) is not int or item['size'] < 0:
                    raise BackupError('اندازهٔ فایل ابری معتبر نیست.', 503)
                files[relative] = item
            else:
                pending.append((item['id'], relative + '/', depth + 1))
    if by_parent is not None and any(identifier not in seen for identifier in by_parent):
        raise BackupError('ساختار فهرست بازگشتی ابر کامل و قابل تأیید نیست.', 503)
    return files, errors


def checked_file(manager, run, job, path, remote, temporary, snapshot_lock, reuse):
    root = Path(job['path'])
    relative = path.relative_to(root).as_posix()
    item = remote.get(relative)
    length = manager.file_size(path)
    manager.update(phase='بررسی نسخهٔ محلی و ابری', current=str(path), fileStage='verify')
    status, message, proof, receipt = 'unchanged', '', None, None
    staged = Path(temporary) / uuid.uuid4().hex
    try:
        manager.cancelled()
        with snapshot_lock:
            digest, length, modified, sqlite_snapshot = manager.snapshot(path, staged)
        if not item:
            raise BackupError('نسخهٔ ابری این فایل موجود نیست.')
        if item['size'] != length:
            raise BackupError('حجم نسخهٔ ابری با نسخهٔ محلی برابر نیست.')
        receipt = verify_object(manager, item['id'], digest, length, item, reuse=reuse)
        proof = (job['id'], relative, digest, length, modified, item['id'], path.name)
    except (OSError, sqlite3.Error, BackupError) as error:
        if manager.stop.is_set():
            raise
        status = 'error'
        message = str(error) if isinstance(error, BackupError) else 'خواندن فایل محلی برای بررسی ناموفق بود.'
    finally:
        staged.unlink(missing_ok=True)
    return ((job['id'], relative, length, item['size'] if item else None,
             item['id'] if item else None, status, message), proof, receipt)


def store_checked(manager, run, root, records):
    if not records:
        return
    with manager.db() as database:
        database.executemany('UPDATE run_items SET status=?,size=?,error=?,remote_id=? WHERE run=? AND path=?',
            ((row[5], row[2], row[6], row[4], run, str(root / row[1])) for row, proof in records))
        database.executemany('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                             (proof for row, proof in records if proof))
    records.clear()


def exact_folder(manager, parent, name):
    matches = [item for item in manager.client.list_folder(parent) if item['name'] == name]
    if len(matches) > 1 or matches and matches[0].get('key'):
        raise BackupError('نام پوشهٔ ابری تعارض دارد: ' + name)
    return matches[0]['id'] if matches else None


def local_usage(manager, root):
    total, files, unreadable = 0, 0, 0
    pending = [root]
    while pending:
        manager.cancelled()
        directory = pending.pop()
        try:
            if is_link(directory):
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    manager.cancelled()
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += info.st_size
                            files += 1
                    except OSError:
                        unreadable += 1
        except OSError:
            unreadable += 1
    return {'localTotalBytes': total, 'localTotalFiles': files, 'sizeUnreadable': unreadable}


def capture_synced_folder(manager, config, job, paths, result, verified):
    root = Path(job['path'])
    remote_root = exact_folder(manager, None, config['remoteRoot'])
    parent = exact_folder(manager, remote_root, job['name']) if remote_root is not None else None
    remote, cloud_errors = remote_tree(manager, parent) if parent is not None else ({}, [])
    result['errors'].extend(cloud_errors)
    with manager.db() as database:
        items = database.execute('SELECT path,status,size,error,remote_id FROM run_items WHERE run=?',
                                 (result['id'],)).fetchall()
    rows, confirmed_bytes, confirmed, proof_times = [], 0, 0, []
    for raw, status, length, message, identifier in items:
        try:
            relative = Path(raw).relative_to(root).as_posix()
        except ValueError:
            continue
        item = remote.get(relative)
        matched = (status in ('ok', 'unchanged') and item
                   and item['id'] == identifier and item['size'] == length)
        if matched and verified and revision_stamp(item):
            with manager.db() as database:
                indexed = database.execute('SELECT sha FROM files WHERE job=? AND rel=?',
                                           (job['id'], relative)).fetchone()
            receipt = verified_receipt(manager, identifier, indexed[0], length, item) if indexed else None
            matched = receipt is not None
            if matched:
                proof_times.append(receipt)
        if matched:
            confirmed += 1
            confirmed_bytes += length
        elif not message:
            message = 'اندازه یا شناسهٔ نسخهٔ ابری پس از ارسال قابل تأیید نیست.' if not verified else 'نسخهٔ ابری پس از ارسال قابل تأیید نیست.'
            if status in ('ok', 'unchanged'):
                result['errors'].append(relative + ': ' + message)
                manager.record_item(result['id'], Path(raw), 'error', length, message, identifier)
        rows.append((job['id'], relative, length, item['size'] if item else None,
                     item['id'] if item else None, 'unchanged' if matched else 'error', message))
    usage = local_usage(manager, root)
    summary = {'run': result['id'], 'checkedAt': now(), 'measuredAt': time.time_ns(), 'startedAt': result['at'],
               'contentVerifiedAt': min(proof_times) if proof_times else None,
               'assurance': 'sha256' if verified else 'size-and-identity',
               'configStamp': config_stamp(config), 'volumeStamp': volume_stamp(config),
               'localBytes': sum(row[2] for row in rows),
               'cloudBytes': sum(item['size'] for item in remote.values()),
               'localFiles': len(rows), 'cloudFiles': len(remote), 'verifiedFiles': confirmed,
               'verifiedBytes': confirmed_bytes, 'issues': len(rows) - confirmed + len(cloud_errors),
               'excluded': 0, 'cloudExtras': len(set(remote) - {row[1] for row in rows}),
               'complete': not cloud_errors and confirmed == len(rows) and len(paths) == len(rows)} | usage
    with manager.db() as database:
        database.execute('DELETE FROM audit_files WHERE job=?', (job['id'],))
        database.executemany('INSERT INTO audit_files VALUES (?,?,?,?,?,?,?)', rows)
        database.execute('INSERT OR REPLACE INTO folder_audits VALUES (?,?)',
                         (job['id'], json.dumps(summary)))
    return summary


def audit_folders(manager, reuse=True):
    config = json.loads(json.dumps(manager.config))
    jobs = [job for job in config['folders'] if job['enabled']]
    if not jobs:
        raise BackupError('حداقل یک پوشهٔ فعال اضافه کنید.')
    manager.audit_generation = None
    generation = manager.change_generation
    result = {'id': uuid.uuid4().hex, 'at': now(), 'audit': True, 'preview': False,
              'uploaded': 0, 'unchanged': 0, 'bytes': 0, 'files': 0,
              'errors': [], 'excluded': 0, 'samples': [], 'manifests': [],
              'reusedProofs': 0, 'downloadedProofs': 0, 'deep': not reuse}
    manager.active_result = result
    atomic_json(manager.state_dir / 'active-run.json', result)
    manager.update(runId=result['id'], steps=[
        {'key': 'connection', 'label': 'بررسی اتصال', 'status': 'running'},
        {'key': 'scan', 'label': 'اندازه‌گیری پوشه‌ها', 'status': 'pending'},
        {'key': 'transfer', 'label': 'مقایسه و بررسی SHA-256', 'status': 'pending'},
        {'key': 'manifest', 'label': 'ثبت نتیجهٔ بررسی', 'status': 'pending'}])
    manager.update(connection=manager.client.connection(), phase='اندازه‌گیری پوشه‌ها')
    manager.step('connection', 'ok')
    manager.step('scan')
    inventory = []
    for job in jobs:
        root, skips, scan_errors, blocked = Path(job['path']), [], [], {}
        def scan_error(path, message):
            relative = path.relative_to(root).as_posix()
            blocked[relative] = (manager.file_size(path), message)
            manager.record_item(result['id'], path, 'error', manager.file_size(path), message)
        paths = list(manager.walk(root, config, scan_errors, skips, scan_error))
        result['errors'].extend(scan_errors)
        result['excluded'] += len(skips)
        with manager.db() as database:
            database.executemany('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)',
                                 ((result['id'], str(path), 'queued', manager.file_size(path), '', None) for path in paths))
        usage = local_usage(manager, root)
        inventory.append((job, paths, blocked, scan_errors, skips, usage))
    manager.step('scan', 'ok' if not result['errors'] else 'error')
    manager.step('transfer')
    remote_root = exact_folder(manager, None, config['remoteRoot'])
    with manager.db(), tempfile.TemporaryDirectory(prefix='audit-', dir=manager.state_dir) as temporary:
        for job, paths, blocked, scan_errors, skips, usage in inventory:
            manager.cancelled()
            root = Path(job['path'])
            parent = exact_folder(manager, remote_root, job['name']) if remote_root is not None else None
            remote, cloud_errors = remote_tree(manager, parent) if parent is not None else ({}, [])
            result['errors'].extend(cloud_errors)
            rows = []
            local_bytes = sum(manager.file_size(path) for path in paths) + sum(value[0] for value in blocked.values())
            verified, verified_bytes, proof_times = 0, 0, []
            for relative, (length, message) in blocked.items():
                item = remote.get(relative)
                rows.append((job['id'], relative, length, item['size'] if item else None,
                             item['id'] if item else None, 'error', message))
            snapshot_lock, pending_records = threading.Lock(), []
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='cloud-audit') as executor:
                remaining_paths = iter(paths)
                def submit_next():
                    path = next(remaining_paths, None)
                    return executor.submit(checked_file, manager, result['id'], job, path, remote,
                                           temporary, snapshot_lock, reuse) if path is not None else None
                tasks = {task for task in (submit_next() for _ in range(4)) if task is not None}
                while tasks:
                    manager.cancelled()
                    finished, tasks = wait(tasks, return_when=FIRST_COMPLETED)
                    for task in finished:
                        row, proof, receipt = task.result()
                        rows.append(row)
                        pending_records.append((row, proof))
                        if proof:
                            verified += 1
                            verified_bytes += row[2]
                            result['unchanged'] += 1
                            result['files'] += 1
                            proof_times.append(receipt[0])
                            result['reusedProofs' if receipt[1] else 'downloadedProofs'] += 1
                        else:
                            result['errors'].append(row[1] + ': ' + row[6])
                        if len(pending_records) >= 32:
                            store_checked(manager, result['id'], root, pending_records)
                        manager.update(skipped=result['unchanged'], errors=result['errors'][-100:])
                        next_task = submit_next()
                        if next_task is not None:
                            tasks.add(next_task)
            store_checked(manager, result['id'], root, pending_records)
            summary = {'run': result['id'], 'checkedAt': now(), 'measuredAt': time.time_ns(), 'startedAt': result['at'],
                       'configStamp': config_stamp(config), 'volumeStamp': volume_stamp(config), 'localBytes': local_bytes,
                       'cloudBytes': sum(item['size'] for item in remote.values()),
                       'localFiles': len(paths) + len(blocked), 'cloudFiles': len(remote),
                       'verifiedFiles': verified, 'verifiedBytes': verified_bytes,
                       'contentVerifiedAt': min(proof_times) if proof_times else None,
                       'issues': len(blocked) + len(paths) - verified + len(cloud_errors),
                       'excluded': len(skips), 'cloudExtras': len(set(remote) - {row[1] for row in rows}),
                       'complete': not scan_errors and not cloud_errors and verified == len(paths)} | usage
            with manager.db() as database:
                database.execute('DELETE FROM audit_files WHERE job=?', (job['id'],))
                database.executemany('INSERT INTO audit_files VALUES (?,?,?,?,?,?,?)', rows)
                database.execute('INSERT OR REPLACE INTO folder_audits VALUES (?,?)',
                                 (job['id'], json.dumps(summary)))
    manager.step('transfer', 'ok' if not result['errors'] else 'error')
    manager.step('manifest', 'ok')
    manager.audit_generation = generation
    manager.finish_result(result)
    manager.update(phase='بررسی کامل پایان یافت' if result['ok'] else 'بررسی کامل: فایل‌های نیازمند رسیدگی وجود دارند', fileStage=None)
