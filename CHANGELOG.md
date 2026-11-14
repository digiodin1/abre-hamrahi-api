# Changelog

## 1.7.1 - 2026-11-14

- Align account quota precision with the provider's two-decimal display; clearly distinguish remaining space from used space and percentage.
- Show quota observation seconds and exact remaining bytes on hover; preserve asynchronous refresh and stale-value warnings.
- Match Android quota precision and show its last observed used/total capacity alongside remaining space.

## 1.7.0 - 2026-11-14

- Save per-folder instant/daily schedules independently while uploads continue; preserve the running pass and durable daily deadlines.
- Make guarded local-to-cloud mirror the default: native replacement at the original path, native trash for removals after a successful full scan, no custom history or manifest uploads.
- Preserve old history and explicit legacy versioned configurations; never permanently delete or empty provider trash.
- Bundle IRANSansX fonts and application icons; show physical local/cloud folder volumes, file sizes, account quota freshness, process stages, retryable errors and reports.
- Improve throughput with bounded concurrent files/parts, pooled HTTPS, cached listings and source fingerprints; require fresh finalized metadata before indexing uploads.
- Update Android with native replacement/trash, independent schedules, local fonts, byte progress, volume/quota gauges, paginated file records and file-specific retry.
- Verify synthetic live replacement/trash without touching personal content; validate Windows packages and signed Android builds. Physical-phone validation remains outstanding.

## 1.5.0 - 2026-11-11

- Keep folder selection and preparation available during uploads; preserve save guards around active jobs and continue progress polling while the chooser is open.
- Add a Windows notification area icon with open/exit controls, graceful cleanup and Explorer-restart recovery.
- Remember an explicitly migrated desktop profile so installed launches, shortcuts and optional startup use its original account, state and port.
- Derive the reported release from the package version rather than a separate hardcoded value.
- Add a signed native Android APK with Storage Access Framework folder selection, Keystore-protected sessions, version preservation, SHA-256 verification, recovery journals, foreground monitoring and scheduled background jobs.
- Document Android storage/battery limits and physical-device validation still required for the first Android build.

## 1.4.0 - 2026-11-11

- Package the Windows runtime, native file watcher and folder chooser in a per-user installer and portable bundle.
- Validate real install, repeat launch, repair and uninstall in isolated profiles without Python on PATH.
- Audit runtime bundles for private state, credentials and absolute developer paths.

## 1.3.1 - 2026-11-11

- Show per-file failures, readable byte units, progress and automatic-retry status.
- Preserve failed and deferred rows across retry and recover interrupted work.

## 1.3.0 - 2026-11-11

- Add a start-or-open desktop launcher, duplicate-click serialization and Windows Shell shortcut creation.
- Process affected file paths in change-triggered runs after a successful baseline; preserve edits arriving during transfers.
- Keep full reconciliation for structural changes, restart, configuration, queue overflow and periodic integrity checks.
- Retry transient monitor failures without silently losing the scheduler.
- Add private session/key/test directories to new-install exclusions; preserve existing configured exclusions.
- Document broad project sources, explicit exclusions and partial recovery manifests.

## 1.2.0 - 2026-11-08

- Add a confirmed Exit application button in Settings and a protected local `shutdown` API action.
- Stop new work, cancel active backups cooperatively, and preserve completed records, settings and login during cleanup.
- Reject exit while a folder chooser is open; prevent a chooser from starting after exit is requested.
- Keep an exit acknowledgement visible and stop UI polling after the request succeeds.
- Test cancellation during an active transfer, HTTP trust boundaries, UI confirmation, real process exit and reopening saved state.

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
