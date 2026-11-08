# Abre Hamrahi API

Abre Hamrahi API is a free, open-source, local Windows panel for one-way, versioned backups to Abrehamrahi. It scans folders you choose, preserves their original file names, uploads only changed content, and verifies every uploaded object with SHA-256 when verification is enabled.

Version **1.1.0** preserves original file names and keeps verified predecessors in separate history. Account, folders, settings, processing, and reports have separate pages. The application release status does not imply official provider support.

The project is intentionally conservative:

- It never deletes cloud objects or local files.
- A failed or unverified upload is not written to the local index.
- SQLite files are copied through a read-only snapshot so committed WAL data is included.
- Passwords and one-time codes are never written. The access and refresh session is encrypted with Windows DPAPI and kept in the private data directory.
- The panel binds to `127.0.0.1` only, uses a per-process CSRF token, rejects cross-origin requests, and serves a fixed asset allowlist.
- The API rejects non-loopback peers, sends browser isolation headers, and checks signed-transfer DNS twice to detect a changing destination.
- Preview mode works without contacting the provider and is the recommended first run.

## Provider status

This is an unofficial integration. As of November 4, 2026, Abrehamrahi did not publish a stable public developer API or WebDAV contract for this workflow. The adapter therefore fails closed when a response does not match the small set of observed endpoints. A provider-side change can require an adapter update. Do not make this your only copy of important data.

## Install on Windows

Python 3.11 or newer is required. The basic install has no third-party runtime dependency. File changes are detected every two seconds; installing the optional `watchdog` package enables native filesystem notifications instead.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
abre-hamrahi-api
```

The command opens the local panel in your default browser. The process must remain running for scheduled work; the browser does not. Use `abre-hamrahi-api --no-browser` on a machine where you want to open the panel yourself. The compatibility command `abr-hamrahi-api` remains available. The default private state directory is `%LOCALAPPDATA%\AbrHamrahiAPI`; it is outside the installed package.

For a portable or test directory:

```powershell
abre-hamrahi-api --data-dir C:\AbrHamrahiAPIState --port 8765
```

Only the loopback address is accepted. Do not expose the port through a firewall, reverse proxy, tunnel, or LAN binding.

The provider cannot call this local API back into your computer. A process already running as your Windows user remains outside this tool’s protection boundary.

## First run

1. Open the panel and sign in with the account owner’s phone and either the one-time code or account password. Complete any provider registration or terms step on the official site first.
2. Add one or more local folders. Do not select the state directory, a drive root, a symlink, a mounted credential store, or a folder containing secrets you do not want to back up.
3. Save the settings and run **Preview without upload**. Resolve missing-folder, permissions, live-database, size, and exclusion messages.
4. Run **Connection and capacity check**, then **Upload and restore test**. The test creates a small file under `_connection-tests`, downloads it, and compares its SHA-256 hash. It is intentionally left in the cloud so the result is inspectable.
5. Run a real sync. Turn on automatic sync only after a successful preview and round-trip test. With **Start sync when files change** enabled, filesystem notifications are debounced until the file has stopped changing, then the next sync uploads changed content. A periodic full check remains as a recovery pass for missed notifications.

Automatic sync can be paused and resumed from the panel. If the computer is offline or the provider rejects a request, retries use bounded backoff and the next change remains pending; the local source is never deleted.

**Automatic sync** is the master switch for the periodic reconciliation. **Also start when files change** adds the faster change trigger; it is available only when automatic sync is enabled. Neither setting starts the application after Windows logon. Enable **Run at Windows sign-in** in Settings and save to register the current interpreter, state directory, and port under the current user's Windows Run key. Turn it off and save before uninstalling or moving the installation. It starts without a browser and requires no administrator privileges.

New cloud roots default to the computer name. Source folders, nested folders, and files retain their exact names; there is no cloud alias. Two selected source folders cannot share a name. The updated layout migrates indexed legacy names in place after verification and stops if old and new trees conflict. If the provider rejects or changes a name, the error is reported instead of silently renaming it. See [names, migration, and history](docs/names-and-history.md) before upgrading.

The Processing page shows stages and every eligible file in pages of 100, with errors first. Retry beside a failed file processes only that file and generates a partial manifest. It does not acknowledge unrelated pending changes. Reports retain the last 30 runs, including file results and partial successes. An interrupted run is recovered at the next start. File links select the recorded local file in Windows Explorer; they never execute arbitrary files. Removed, excluded, disabled, or linked source paths cannot be opened or retried through this API.

See [automatic sync and desktop behavior](docs/automatic-sync.md) for settings, the Google Drive for desktop design references, and the supported behavior. For native Windows change notifications, install with `python -m pip install ".[native]"`. The folder picker requires the Tcl/Tk component of the standard Python Windows installer; manual path entry remains available.

The [local API reference](docs/local-api.md) documents file reports, retries, startup settings, and request protections.

Changed files move their verified predecessors into `_abre-backup-data/history` before the new content is uploaded with its original name. There are no overwrite or delete requests. Interrupted transfers can leave the current location empty until retry; the previous bytes remain in history. Unchanged files and manifests are reused. Identical source files with different names remain separate paths. Restore is deliberately manual: download the chosen object and use the manifest to map its relative path and hash back to the source folder.

## Safety and privacy

The default exclusion list omits environment files, private keys, credential-like names, temporary files, development dependency folders, the application state directory, and database sidecar files. Review the list for your own data before the first sync.

Remote manifests contain relative paths, hashes, sizes, and provider object IDs. They do not contain the original absolute Windows source path. Local configuration, history, SQLite index, logs, and encrypted session material stay in the private state directory and are ignored by Git. No telemetry or external analytics are included.

This project does not provide end-to-end encryption. The provider and its signed storage URLs can read uploaded content. Encrypt sensitive files before selecting them if your threat model requires client-side confidentiality.

## Development

```powershell
git clone https://github.com/digiodin1/abre-hamrahi-api.git
cd abre-hamrahi-api
py -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

The test suite uses an in-memory fake cloud and synthetic temporary folders. It does not require an Abrehamrahi account and never sends test files to the network. See [CONTRIBUTING.md](CONTRIBUTING.md) for the review checklist and [SECURITY.md](SECURITY.md) for vulnerability reporting.

With Node.js 18 or newer, run `node tests/web_checks.mjs` for dependency-free UI controller checks. They use a minimal DOM double and do not replace a visual browser check. For manual browser QA with 105 synthetic files and a retryable error, run `python tests/ui_fixture.py` with `PYTHONPATH=src` and open the printed loopback URL. This fixture never accesses a real provider account.

## License

MIT. See [LICENSE](LICENSE).
