# Changelog

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
