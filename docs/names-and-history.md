# Names, history, and duplicate files

The visible backup tree uses the original file and directory names, without
sanitizing, truncating, or appending a content hash. If the provider rejects or
changes a name, the operation fails visibly. Upload completion must confirm both
the exact name and parent directory.

An unchanged file is verified and reused. When an indexed file changes, its old
bytes are downloaded and SHA-256 checked before the existing object is moved to:

```
<cloud root>/_abre-backup-data/history/<job id>/<SHA-256 of relative path>/<SHA-256 of content>/<original filename>
```

Then the new content is uploaded under its original name in the visible tree.
There is no overwrite or delete request. A transfer failure can leave the visible
location temporarily empty, but its predecessor remains in history. Retry
reconciles the actual cloud state. Reverting to an archived version reuses that
object instead of uploading another copy. Unindexed, conflicting, or corrupted
objects stop the operation and require review. `_abre-backup-data` is reserved
as a source folder name under a cloud root.

Manifests are stored separately under `_abre-backup-data/manifests/<job id>`.
Their names contain the full hash of their canonical contents. An unchanged
manifest is reused; a timer alone does not generate a new manifest. The local
Reports page still records every run and its time. A previous manifest's object
IDs continue to identify versions moved into history; relative paths and hashes
allow manual restore without guessing from a filename. Keep these manifests.

## Upgrading the old layout

Stop the old application before running the new version. Preserve its private
state directory and SQLite index. Do not run old and new releases against the
same account and source at the same time.

The next sync recognizes old `<source name>-<job id>` directories and the old
sanitized, hash-suffixed nested directories. When the new name is free, it
renames the existing directory in place. A file with the old hash-suffixed name
is renamed only when its ID is recorded in the local index and its full hash
and size have been verified. File object IDs and contents are preserved, so
unchanged data need not be uploaded again. Preview performs no cloud mutation.

If both old and new folders already exist, migration stops instead of merging
them. If the local index is missing or the configured cloud root has changed,
automatic reconciliation cannot prove ownership of old objects. Restore the
original state or review the trees manually before syncing. Unknown historical
files and manifests are left intact. This is not an account-wide duplicate
cleaner and does not remove copies produced by older releases.

## What counts as a duplicate?

Two different source paths, such as `report.txt` and `report - Copy.txt`, remain
two files in the backup even when their bytes match. Removing either cloud file
would break the exact source tree. Different content hashes represent versions,
not duplicates. Similar names, equal sizes, or a short hash prefix alone do not
prove equality.

For existing duplicates, first record each original path and object ID, download
the candidates, and compare complete SHA-256 hashes. Preserve one verified
recoverable copy and its path mappings before proposing any cleanup. Review
changes in the source folder separately; excluding or removing a source copy
does not authorize deletion of its cloud history. This client never automatically
deletes cloud data.

The adapter's rename and move payloads follow the provider's public web frontend
observed on November 5, 2026. That frontend is not a stable public integration
contract. Each mutation is read back and checked for the same object ID, content
key, size, and unaffected location/name. An ambiguous response is reconciled
with reads and is never blindly replayed. Changing provider behavior can still
require an adapter update.
