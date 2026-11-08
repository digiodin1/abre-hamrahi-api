# Changelog

## 1.1.0 - 2026-11-08

- Preserve original file names exactly; reject provider name changes instead of silently accepting them.
- Migrate indexed legacy folder/file names in place, with full file hash checks and conflict stops.
- Move verified predecessors into separate history without overwrite/delete; reuse archived content on reversion.
- Reuse identical manifests and keep them outside the visible source tree.
- Reconcile ambiguous rename/move responses with reads; stop rather than blindly replay a write.
- Document source copies versus content versions and safe review of existing duplicates.
- Verify legacy rename, both content versions, and unchanged-run reuse against isolated synthetic data on the real provider (November 7, 2026).

Stop the old process and preserve the private index before upgrading. Both old and new trees already existing together require manual review; migration does not merge or delete them.

## 1.0.0 - 2026-11-05

- Separate account, folders, settings, processing, and reports pages.
- Preserve source folder names exactly, including nested folders; default new cloud roots to the computer name.
- Add opt-in Windows logon startup, scoped to the current user and private state directory.
- Show live stages, all eligible files through paginated reports, per-file errors, and isolated retry.
- Preserve partial results and recover interrupted runs; local file links select recorded files in Explorer without executing them.
- Promote the application to its first main release. The provider integration remains unofficial.

### Upgrade from 0.2

The new folder layout omits the old ID/hash suffixes on cloud folders. Existing cloud backups remain available; the first run with the new layout may upload another copy. Folders with the same name must be selected separately under distinct cloud roots. Existing custom cloud roots are retained.

## 0.2.0 - 2026-11-05

- Display the requested Abre Hamrahi API product name and provide the `abre-hamrahi-api` launcher while retaining older command aliases.
- React to file changes with debounce, retained pending work, bounded retry, periodic integrity checks, and pause/resume controls.
- Use optional native filesystem notifications with a standard-library polling fallback.
- Open the folder picker in a dedicated Python GUI process, independent of PowerShell execution policy.
- Cover Windows canonical paths, concurrent edits, retry, pause, and automatic uploads with synthetic regression tests.

## 0.1.0 - 2026-11-04

- Initial public release of the standalone local Abr Hamrahi API panel.
- Versioned, one-way uploads with SHA-256 verification and no cloud deletion.
- SQLite read-only snapshots, bounded multipart streaming, cancellation, quota checks, and offline preview.
- Windows DPAPI session storage and a single-instance state-directory lock.
- Loopback-only panel with fixed assets, CSRF and Origin checks, body limits, safe error responses, and no telemetry.
- Synthetic offline regression and HTTP trust-boundary tests.
- Renamed the public product and added an explicit loopback peer check plus browser isolation headers.
