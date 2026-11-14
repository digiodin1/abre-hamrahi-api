# Names, replacement and trash

The selected tree uses original file and directory names without hash suffixes or aliases. Completion verifies the exact name and destination. Duplicate destination names stop the operation.

## Mirror behavior in 1.7

The default `syncBehavior: "mirror"` replaces an indexed changed file using the provider's native overwrite and version group, preserving one current visible path. Unchanged files are not uploaded again. No custom history or remote manifest is created in this mode. Provider-managed internal versions may still exist.

After a complete, successful folder pass, cloud-only entries within that selected folder are sent to native trash. A missing/unreadable source, scan failure, failed transfer, changed source root, protected exclusion or partial retry prevents unsafe deletion. Every trash request checks the current target metadata and reads back the result. An ambiguous response is reconciled without repeating the mutation. The client never calls permanent deletion or empties trash. Restore and provider retention are managed in the cloud interface.

This mirrors local changes toward the cloud. Remote-to-local downloads and bidirectional conflict resolution are not implemented.

## Upgrading

Preserve the private state directory and SQLite index. Do not run releases concurrently against the same source/account profile.

Old indexed hash-suffixed names are migrated in place only after verification. Conflicting old/new folders stop migration. Existing custom history and manifests under `_abre-backup-data` or Android's `_abre-android-data` are left intact. The update does not perform account-wide historical cleanup.

For compatibility, an explicitly configured `syncBehavior: "versioned"` keeps the former archive/manifest behavior. The panel saves mirror mode. The compatibility setting is not a second visible sync option.

## Conflicts and uncertain uploads

Unindexed files can be adopted only after a complete content hash verification against the local snapshot. Different or unverifiable remote content is a visible conflict. Finalized storage keys can differ from provisional upload keys; recovery uses the actual finalized object and content evidence.

Two local files with different names remain two cloud paths even if their bytes match. Equal sizes alone do not establish duplicate content.

The adapter follows observed provider web requests and validates response shapes. Provider behavior changes may require an adapter update; malformed responses fail visibly instead of silently changing names or permanently deleting objects.
