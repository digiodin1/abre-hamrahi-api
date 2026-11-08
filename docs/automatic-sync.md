# Automatic sync

## Setup

1. Sign in, select a local source folder, click **Add**, and save the settings.
2. Run an offline preview and the upload/restore test before enabling automation.
3. Enable **Automatic sync** and **Start sync when files change**, then save.
4. Keep the application process running. The browser can be closed.

Creating, changing, renaming, or deleting a file queues a new backup pass. Changes are grouped during a quiet period. A change arriving during an upload stays pending for another pass. Files that are still being written are deferred and retried. A periodic complete pass checks cloud integrity even if change-triggered passes run frequently.

The native observer is optional: `python -m pip install ".[native]"`. Without it, file size and modification time are checked every two seconds. Excluded directories and application state are omitted from observation. Native observer failures fall back to polling; missing folders are checked again when available. A periodic full hash pass also catches changes missed by notifications or metadata polling.

## Settings

| Setting | Behavior |
| --- | --- |
| Automatic sync | Enables unattended uploads; disabled by default. |
| Start sync when files change | Adds change-triggered runs to periodic checks. |
| Wait after last file change | Default 15 seconds; recently modified files are deferred. |
| Complete check interval | Default 60 minutes, minimum 5; change-triggered runs do not postpone this deadline. |
| `debounceSeconds` | Default 5 seconds, range 1–300; stored in local configuration. The longer of debounce and settle delay is used. |
| Pause / Resume | Persists across restart. Resume queues a fresh check, including changes made while paused. |
| Verify uploaded content | Downloads and compares SHA-256 before recording success. |
| Exclusions | Folder names or glob patterns, one per line. Application credentials/state are always excluded. |

Failed automatic work remains pending. Retry delay grows from 5 seconds to a maximum of 5 minutes. No uploads run until an account session is available. The panel displays pending work, observation status, errors, and recent results. Cancel also pauses automatic uploads so the scheduler does not immediately restart cancelled work.

## Desktop model and boundaries

The design references are Google's official [Drive for desktop guide](https://support.google.com/drive/answer/10838124?hl=en) and [streaming/mirroring guide](https://support.google.com/drive/answer/13401938?hl=en). They informed selected-folder management, reacting to edits, visible sync state, and pause/resume controls.

This implementation provides one-way versioned backup. It does not provide cloud placeholders, streaming mounts, bidirectional conflict resolution, or automatic remote-to-local downloads. Original file names are preserved; verified predecessors move to separate history when content changes. Unchanged files and manifests are reused. Local deletions and renames are reflected in a new manifest while old cloud objects remain available. Empty folders are not uploaded. Restore remains manual using the chosen version and its manifest. Read [names, migration, and history](names-and-history.md) before upgrading an older installation.

Automation stops while Windows is asleep or the application is closed. On restart, a fresh pass is queued when automation is enabled; a saved pause remains respected. A continuously changing file can be deferred until it stabilizes. Polling can miss changes that preserve both size and modification time until the next full check.

## Folder picker

The picker runs in an isolated Python GUI process using the Windows folder dialog provided by Tk. It requires Python's Tcl/Tk component and reports cancellation separately from failure. Only one picker can be open at a time, with a three-minute timeout. It does not execute a user-provided command or change PowerShell policy. If Tcl/Tk is unavailable, enter the absolute local folder path manually.
