"""Exact visible names, reversible legacy migration, and immutable history.

Never overwrite or delete an object. Only locally indexed, hash-verified files
can be renamed or moved. A failed upload leaves its predecessor in history.
"""
import hashlib

METADATA = '_abre-backup-data'


class CloudLayout:
    def __init__(self, client, root, error, sanitize, cancelled, verifier=None):
        self.client, self.root = client, root
        self.error, self.sanitize, self.cancelled = error, sanitize, cancelled
        self.cache = {}
        self.listings = {}
        self.verifier = verifier or (lambda item, digest, size: self.client.verify(
            item['id'], digest, size, cancelled=self.cancelled))

    def listing(self, parent):
        if parent in self.listings:
            return self.listings[parent]
        items = self.client.list_folder(parent)
        names = [x['name'] for x in items]
        if len(names) != len(set(names)):
            raise self.error('نام تکراری در پوشهٔ ابری وجود دارد؛ انتخاب خودکار امن نیست.')
        self.listings[parent] = dict(zip(names, items))
        return self.listings[parent]

    def prime(self, parent):
        if not hasattr(self.client, 'list_tree'):
            return
        groups, identities = {parent: {}}, set()
        for item in self.client.list_tree(parent):
            name, identifier, owner = item.get('name'), item.get('id'), item.get('parent')
            if (identifier in identities or identifier == parent or not isinstance(name, str)
                    or name in ('', '.', '..') or '/' in name or '\\' in name):
                raise self.error('فهرست بازگشتی ابر عضو نامعتبر یا تکراری دارد.', 503)
            identities.add(identifier)
            siblings = groups.setdefault(owner, {})
            if name in siblings:
                raise self.error('نام تکراری در پوشهٔ ابری وجود دارد؛ انتخاب خودکار امن نیست.')
            siblings[name] = item
            if not item.get('key'):
                groups.setdefault(identifier, {})
        pending, visited = [parent], set()
        while pending:
            owner = pending.pop()
            if owner in visited:
                raise self.error('ساختار پوشه‌های ابر چرخه دارد.', 503)
            visited.add(owner)
            pending.extend(item['id'] for item in groups[owner].values() if not item.get('key'))
        if set(groups) != visited:
            raise self.error('ساختار فهرست بازگشتی ابر کامل نیست.', 503)
        self.listings.update(groups)

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
        items.pop(legacy, None)
        items[name] = dict(current, name=name) if current else {'id': result, 'name': name, 'parent': parent, 'key': None}
        if not current:
            self.listings[result] = {}
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
            self.verifier(previous, old[0], old[3])
            self.cancelled()
            self.client.rename(previous['id'], name)
            items.pop(old[2])
            current = dict(previous, name=name)
            items[name] = current
        return current

    def archive(self, job, relative, current, old):
        if not old or current['id'] != old[1] or not current.get('key'):
            raise self.error('فایل هم‌نام مقصد در فهرست محلی ثبت نشده؛ بازنویسی خودکار امن نیست.')
        self.verifier(current, old[0], old[3])
        parent = self.history_parent(job, relative, old[0])
        existing = self.listing(parent).get(relative.name)
        if existing and existing['id'] != current['id']:
            raise self.error('نسخهٔ تاریخچه از قبل موجود است؛ برای جلوگیری از تکرار، انتقال متوقف شد.')
        self.cancelled()
        self.client.move(current['id'], parent)
        self.listing(current['parent']).pop(current['name'], None)
        self.listing(parent)[relative.name] = dict(current, parent=parent)

    def history_parent(self, job, relative, digest):
        parent = self.folder(METADATA, self.folder(self.root, None))
        parent = self.folder('history', parent)
        parent = self.folder(job['id'], parent)
        parent = self.folder(hashlib.sha256(relative.as_posix().encode()).hexdigest(), parent)
        return self.folder(digest, parent)

    def restore_version(self, job, relative, digest, size, parent):
        owner = self.folder(self.root, None)
        for name in (METADATA, 'history', job['id'], hashlib.sha256(relative.as_posix().encode()).hexdigest(), digest):
            folder = self.listing(owner).get(name)
            if not folder:
                return None
            if folder.get('key'):
                raise self.error('مسیر تاریخچه با یک فایل تعارض دارد.')
            owner = folder['id']
        existing = self.listing(owner).get(relative.name)
        if not existing:
            return None
        if not existing.get('key'):
            raise self.error('نام فایل در تاریخچه با پوشه تعارض دارد.')
        self.verifier(existing, digest, size)
        self.cancelled()
        self.client.move(existing['id'], parent)
        self.listing(owner).pop(relative.name, None)
        restored = dict(existing, parent=parent)
        self.listing(parent)[relative.name] = restored
        return restored

    def manifests(self, job):
        parent = self.folder(METADATA, self.folder(self.root, None))
        parent = self.folder('manifests', parent)
        return self.folder(job['id'], parent)
