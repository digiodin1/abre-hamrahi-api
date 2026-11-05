# Local panel API (1.0)

This is a private desktop API bound to IPv4 loopback. It is not a server that the cloud provider can call. Do not forward or publish its port.

Read `GET /api/backup` for configuration, current stages, the last 30 run summaries, startup registration status, and the process CSRF token. Account tokens and passwords are never returned. `GET /api/status` is a small readiness response. Only the fixed UI assets are served; there is no arbitrary file download route.

Every action below is `POST /api/backup/<action>` with a JSON object, a same-origin `Origin`, and `X-Abr-Hamrahi-CSRF` from the current process. Restarting invalidates old CSRF tokens. Requests are limited to 64 KiB. Unknown actions and fields used as paths do not bypass the configured source checks.

| Action | Request / behavior |
| --- | --- |
| `config` | Save the full configuration. Includes `remoteRoot`, `folders`, `automatic`, `syncOnChange`, `startupEnabled`, `intervalMinutes`, `settleSeconds`, `maxFileMiB`, `verifyUploads`, and `exclude`. Folder names are derived from local paths, regardless of a supplied alias. |
| `browse` | Open the Windows folder chooser; returns a selected path or cancellation. |
| `preview` | Start a local-only scan without contacting the provider. |
| `sync` | Start a full one-way backup of active sources. |
| `connection` / `test` | Check quota or perform a small upload/download verification. |
| `pause` / `resume` / `cancel` | Pause automation, resume pending work, or request cancellation after the current bounded operation. |
| `items` | `{ "run": "<32 lowercase hexadecimal characters>", "offset": 0, "failedOnly": false }`. Returns up to 100 file records plus `total`. Page with offsets 0, 100, 200, etc. |
| `retry` | `{ "run": "<run ID>", "path": "<recorded failed local file>" }`. Starts a separate run containing only that file. The file must still exist in an enabled, non-excluded source without symlink/junction parents. |
| `reveal` | Same run/path shape. Selects the recorded file in Explorer. It does not execute files or serve their contents over HTTP. |

The `login`, `code`, `captcha`, and `logout` actions support the panel's account flow. Do not put credentials in query strings, command examples, issues, screenshots, or reports.

The automatic master switch enables periodic reconciliation. `syncOnChange` adds debounced filesystem-triggered work. `startupEnabled` separately opts into the current user's Windows Run key; it is false by default. Failed registration leaves the configuration unchanged. If configuration persistence fails after registration, the previous registry value is restored.

Run items are persisted locally in SQLite and retained with the last 30 summaries. File states are `queued`, `processing`, `ok`, `unchanged`, `preview`, and `error`. The legacy summary `samples` field is limited to 100; use `items` for the full list. Files excluded by configuration are not sent or listed as eligible items. A run cut off by process exit is recovered at the next launch; unfinished file records become retryable errors. A file-only retry emits a manifest marked `partial: true` and never clears unrelated pending source changes.
