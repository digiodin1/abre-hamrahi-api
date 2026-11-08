"""Exact visible names, reversible legacy migration, and immutable history.

Never overwrite or delete an object. Only locally indexed, hash-verified files
can be renamed or moved. A failed upload leaves its predecessor in history.
"""
import hashlib

METADATA = '_abre-backup-data'


class CloudLayout:
    def __init__(self, client, root, error, sanitize, cancelled):
        self.client, self.root = client, root
        self.error, self.sanitize, self.cancelled = error, sanitize, cancelled
        self.cache = {}

    def listing(self, parent):
        items = self.client.list_folder(parent)
        names = [x['name'] for x in items]
        if len(names) != len(set(names)):
            raise self.error('نام تکراری در پوشهٔ ابری وجود دارد؛ انتخاب خودکار امن نیست.')
        return dict(zip(names, items))

    def folder(self, name, parent, legacy=None):
        key = (parent, name)
        if key in self.cache:
            return self.cache[key]
        items = self.listing(parent)
        current, previous = items.get(name), items.get(legacy) if legacy != name else None
        if previous:
            if previous.get('key') or current:
                raise self.error('ساختار قدیمی و جدید مقصد تعارض دارند؛ ادغام خودکار انجام نشد.')
            self.cancelled()
            self.client.rename(previous['id'], name)
            current = previous
        if current and current.get('key'):
            raise self.error('نام پوشهٔ مقصد با یک فایل تعارض دارد: ' + name)
        result = current['id'] if current else self.client.folder(name, parent, {})
        self.cache[key] = result
        return result

    def parent(self, job, relative):
        root = self.folder(self.root, None)
        parent = self.folder(job['name'], root, job['name'] + '-' + job['id'])
        for part in relative.parts[:-1]:
            legacy = self.sanitize(part) + '-' + hashlib.sha256(part.encode()).hexdigest()[:8]
            parent = self.folder(part, parent, legacy)
        return parent

    def adopt_legacy(self, parent, name, old, items):
        current = items.get(name)
        previous = items.get(old[2]) if old and old[2] != name else None
        if previous and previous['id'] == old[1]:
            if current or not previous.get('key'):
                raise self.error('نسخهٔ قدیمی و نام اصلی هم‌زمان وجود دارند؛ بررسی دستی لازم است.')
            self.client.verify(previous['id'], old[0], old[3], cancelled=self.cancelled)
            self.cancelled()
            self.client.rename(previous['id'], name)
            items.pop(old[2])
            current = dict(previous, name=name)
            items[name] = current
        return current

    def archive(self, job, relative, current, old):
        if not old or current['id'] != old[1] or not current.get('key'):
            raise self.error('فایل هم‌نام مقصد در فهرست محلی ثبت نشده؛ بازنویسی خودکار امن نیست.')
        self.client.verify(current['id'], old[0], old[3], cancelled=self.cancelled)
        parent = self.history_parent(job, relative, old[0])
        existing = self.listing(parent).get(relative.name)
        if existing and existing['id'] != current['id']:
            raise self.error('نسخهٔ تاریخچه از قبل موجود است؛ برای جلوگیری از تکرار، انتقال متوقف شد.')
        self.cancelled()
        self.client.move(current['id'], parent)

    def history_parent(self, job, relative, digest):
        parent = self.folder(METADATA, self.folder(self.root, None))
        parent = self.folder('history', parent)
        parent = self.folder(job['id'], parent)
        parent = self.folder(hashlib.sha256(relative.as_posix().encode()).hexdigest(), parent)
        return self.folder(digest, parent)

    def restore_version(self, job, relative, digest, size, parent):
        existing = self.listing(self.history_parent(job, relative, digest)).get(relative.name)
        if not existing:
            return None
        if not existing.get('key'):
            raise self.error('نام فایل در تاریخچه با پوشه تعارض دارد.')
        self.client.verify(existing['id'], digest, size, cancelled=self.cancelled)
        self.cancelled()
        self.client.move(existing['id'], parent)
        return existing

    def manifests(self, job):
        parent = self.folder(METADATA, self.folder(self.root, None))
        parent = self.folder('manifests', parent)
        return self.folder(job['id'], parent)
