# Automatic sync

## Setup

1. Sign in, select a local source folder, click **Add**, and choose its schedule: **Instant** or **Daily** with a local time. Save the settings. Existing folder schedule controls save independently, including during an upload; the running pass retains its captured configuration.
2. Run an offline preview and the upload/restore test before enabling automation.
3. Enable **Automatic sync** and **Start sync when files change**, then save.
4. Keep the application process running. The browser can be closed.

For instant folders, creating, changing, renaming, or deleting a file queues a new backup pass. Changes are grouped during a quiet period. A change arriving during an upload stays pending for another pass. Files that are still being written are deferred and retried. A periodic complete pass checks instant folders even if change-triggered passes run frequently.

Daily folders are excluded from file-change observation and instant reconciliation. Each has an independent `syncMode: "daily"` and `dailyTime: "HH:MM"` (default `02:00`) using the system's local clock. The first run is the next occurrence of that time. Deadlines persist in the private database across restarts; a missed run catches up once when the application and account are available, then schedules the next local day. Failed folders retain their due time and retry with bounded backoff. Successfully completed daily folders advance independently even when another folder has errors. Disabling or pausing automation prevents both modes from uploading. The process must remain running for scheduled work; enabling Windows startup helps it resume after login. Manual sync includes all enabled folders regardless of schedule. Existing folders migrate to instant mode.

The native observer is optional: `python -m pip install ".[native]"`. Without it, file size and modification time are checked every two seconds. User-configured exclusions and application state are omitted from observation. Native observer failures fall back to polling; missing folders are checked again when available. Periodic reconciliation checks all selected paths and cloud identities. With SHA-256 verification enabled it rereads the contents; in size-and-identity mode unchanged indexed fingerprints can avoid rereading. Manual sync and explicit change notifications always reread their selected files.

## Settings

| Setting | Behavior |
| --- | --- |
| Automatic sync | Enables unattended uploads; disabled by default. |
| Start sync when files change | Adds change-triggered runs to periodic checks. |
| Wait after last file change | Default 15 seconds; recently modified files are deferred. |
| Complete check interval | Instant folders only; default 60 minutes, minimum 5. Change-triggered runs do not postpone this deadline. |
| Folder schedule | Instant changes or a daily local time, selected independently beside each folder. |
| `debounceSeconds` | Default 5 seconds, range 1–300; stored in local configuration. The longer of debounce and settle delay is used. |
| Pause / Resume | Persists across restart. Resume queues a fresh check, including changes made while paused. |
| Verify uploaded content | Downloads and compares SHA-256 before recording success. |
| Concurrent files | `uploadWorkers`: 8 by default, range 1–16. Large files run first; each multipart file uses at most four part workers. |
| Exclusions | Empty by default; optional folder names or glob patterns, one per line. The running application's own state directory is always omitted to prevent recursion. |

Failed automatic work remains pending. Retry delay grows from 5 seconds to a maximum of 5 minutes. No uploads run until an account session is available. The panel displays pending work, observation status, errors, and recent results. Cancel also pauses automatic uploads so the scheduler does not immediately restart cancelled work.

The processing page compares the total physical selected-folder volume with current visible cloud volume. Sent bytes, active transfers and average transfer speed are displayed separately; transfer activity never becomes the volume percentage. Scanning and connection setup use an indeterminate indicator until sizes are known. A failed file has its reason beside it; files not attempted after a batch outage are marked as waiting to continue. A file-level failure leaves unrelated files running. The previous pass's error remains visible while automatic recovery runs. Reports retain each pass and its per-file details. With automation off, use **Start / continue backup**, or retry an individual failed or deferred file after the current pass ends.

Successful files are indexed locally. Recovery lists the cloud again and reuses matching verified content; it does not blindly replay an upload whose completion response was lost. HTTP 500 indicates a provider server failure, which the client cannot repair. Repeated failure therefore remains visible and retries stay bounded by the delay above. Reopening the application queues a full reconciliation when automation is enabled. Keep the application state directory to retain the index and history. Size units in the Persian panel are spelled out and use multiples of 1024 bytes.

## Desktop model and boundaries

After an initial successful pass, existing-file edit notifications can create
partial runs containing just the affected files. Directory changes, deletion,
restart, configuration changes and periodic checks still reconcile fully.
See [desktop launch and project folders](desktop-and-projects.md) for shortcut
creation, exclusions, timing and the meaning of partial runs.

The design references are Google's official [Drive for desktop guide](https://support.google.com/drive/answer/10838124?hl=en) and [streaming/mirroring guide](https://support.google.com/drive/answer/13401938?hl=en). They informed selected-folder management, reacting to edits, visible sync state, and pause/resume controls.

This implementation mirrors local changes to the selected cloud folder. Changed indexed files use native replacement at their original path; unchanged files are reused. A complete successful folder pass moves absent cloud paths to native trash. Missing sources, failed scans/transfers and partial retries do not authorize cleanup. The application never empties trash or permanently deletes objects. Empty folders retain their original names. Restore uses the cloud interface. It does not automatically download cloud edits or provide streaming mounts. Read [names and upgrades](names-and-history.md) before upgrading.

Automation stops while Windows is asleep or the application is closed. On restart, a fresh pass is queued when automation is enabled; a saved pause remains respected. A continuously changing file can be deferred until it stabilizes. Notifications cannot postpone the whole queue indefinitely. Polling and fingerprint reuse can miss changes that preserve filesystem metadata; use manual sync or enable full SHA-256 verification when content proofs are required.

## Folder picker

The picker runs in an isolated Python GUI process using the Windows folder dialog provided by Tk. It requires Python's Tcl/Tk component and reports cancellation separately from failure. Only one picker can be open at a time, with a three-minute timeout. It does not execute a user-provided command or change PowerShell policy. If Tcl/Tk is unavailable, enter the absolute local folder path manually.
