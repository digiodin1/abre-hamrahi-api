# Contributing

Pull requests and issue reports are welcome. Please keep the project dependency-free at runtime and preserve its fail-closed behavior.

Before opening a pull request:

1. Run `python -m unittest discover -s tests -v` on Windows and, when practical, on another supported Python platform.
2. Run `python -m py_compile` for every Python file under `src` and `tests`.
3. Check that no test or fixture contains a real phone number, token, account ID, absolute home path, provider response body, or private document name.
4. Add a regression test for a changed safety boundary, API shape, persistence rule, or failure mode.
5. Keep provider API fixtures synthetic. Never add a live account test, password, OTP, session file, or cloud object ID.

Changes to the provider adapter must state which observed request or response shape they support and what happens when that shape changes. Network retries must remain bounded, idempotent reads may retry, and an ambiguous upload completion must never be replayed blindly.

The public panel must remain loopback-only. Do not add CORS, remote JavaScript, arbitrary file serving, telemetry, automatic startup installation, or code downloaded and executed from the network.

Use focused commits and explain user-visible behavior in the pull request. The GitHub Actions workflow is the minimum required check; maintainers may add a Windows manual smoke test before a release.
