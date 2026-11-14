# Abre Hamrahi API

Free community Windows and Android clients for synchronizing selected local folders to Abrehamrahi. Version **1.7.0** preserves original names, replaces changed files in their existing cloud location, and sends locally removed files to the provider's native trash. An unchanged file is reused.

[Download Windows and Android](https://github.com/digiodin1/abre-hamrahi-api/releases/latest)

## Windows

Install the Windows x64 setup from Releases, open **Abre Hamrahi API**, sign in with your own account, select folders, and save. Choose **Instant** or **Daily** with a local time beside each folder. These schedule controls save immediately and remain available during uploads; an ongoing pass finishes with its captured configuration.

Enable automatic synchronization for unattended work. Enable **Run at Windows sign-in** separately if desired. The program runs in the notification area; closing the browser leaves synchronization running. Startup and unattended updates do not repeatedly open browser tabs. Use the tray icon or shortcut to reopen the panel.

The remote root defaults to the computer name. Source folder and file names remain exact. Two selected roots cannot share a name. Only one process can own a private profile. Credentials are encrypted with Windows DPAPI and are never returned by the local API.

## Synchronization and recovery

- Changed files use native replacement in the same destination, without creating custom history copies or uploading manifests.
- Local deletions use native cloud trash after a complete, error-free source scan and successful file transfers for that folder. Missing roots, scan errors, conflicting metadata, partial retries and protected exclusions prevent unsafe cleanup. The client never permanently empties trash.
- Source-to-cloud synchronization does not automatically download cloud edits. Unexpected or conflicting remote content is reported rather than silently overwritten. Cloud restoration is available through the provider's interface.
- Normal uploads require fresh object metadata confirming exact byte size, object ID, finalized storage key, name and parent. Optional SHA-256 readback downloads the uploaded content for a stronger check.
- File failures retain their reasons and retry controls. Automatic retries use bounded backoff. Durable local indexing preserves completed work across restarts. An ambiguous completion is reconciled with current cloud content instead of blindly repeating the write.
- Older custom history created by previous releases remains intact; upgrading does not erase historical data. The hidden legacy `syncBehavior: "versioned"` API setting is retained for compatibility. New and migrated profiles default to `mirror`.

There are no default content/extension exclusions or application file-size cap. Existing explicit exclusions survive upgrades. The running application's own state is protected against recursive synchronization. Links and junctions are reported instead of followed. Files that cannot be read or are changing remain visible errors and are retried. Provider quota and transfer limits still apply.

Eight files upload concurrently by default, configurable from 1 to 16. Multipart files stream up to four parts concurrently. Directory inventories and HTTPS connections are reused, files are sent largest first, and change events normally process only affected paths. Periodic reconciliation repairs missed notifications. Automatic unchanged-file checks may reuse filesystem fingerprints; manual sync rereads content.

## Dashboard and reports

The capacity gauge uses the provider's account-wide used space and plan plus gift capacity. Refresh runs separately from file transfers; failed refreshes retain and mark the last observation.

The folder volume gauge uses exactly:

```text
100 × min(total physical local bytes, total visible cloud bytes)
    / max(total physical local bytes, total visible cloud bytes)
```

Two empty totals yield 100; only one empty total yields 0. The value stays between 0 and 100 and does not use the current file's progress. Matching volume alone does not prove matching content. Folder observations show timestamps and become stale after changes. Physical local totals include explicitly excluded content, while eligibility is reported separately.

Folder and file inventories show local/cloud sizes. Processing shows stages, loading state, transfer bytes, speed and failures. Reports retain 30 passes. Local links select recorded files in Explorer and never execute them.

## Android

The native Android 8.0+ APK signs into the cloud separately. It uses the system folder chooser, per-folder instant/daily schedules, native replacement/trash, fresh upload metadata verification, byte-based progress, volume/capacity gauges, and bundled IRANSansX fonts. It does not connect to the Windows API or expose the computer.

Monitoring checks document trees while a foreground service is allowed to run. Android controls job timing, battery and network access; this is not an exact background-time guarantee. See [Android installation, build and test details](docs/android.md). Release checks include compilation, signatures, alignment and simulated adapter/schedule tests; physical phone validation is recorded separately.

## Development

```powershell
git clone https://github.com/digiodin1/abre-hamrahi-api.git
cd abre-hamrahi-api
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ".[native]"
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
node tests/web_checks.mjs
```

Python tests use synthetic temporary folders and a fake cloud. UI controller checks use a DOM double, not a visual browser. See [Windows packages](docs/windows-install.md), [automatic sync](docs/automatic-sync.md), [local API](docs/local-api.md), [names and upgrades](docs/names-and-history.md), [contributing](CONTRIBUTING.md) and [security](SECURITY.md).

## Privacy and license

No private profile, account session, signing key, telemetry or analytics is included. The panel binds only to loopback and protects actions with origin/host checks and a process CSRF token. Do not publish its port. The provider can read uploaded content; client-side end-to-end encryption is not provided.

Software: [MIT](LICENSE). Third-party fonts have their own terms; see [NOTICE](NOTICE.md).
