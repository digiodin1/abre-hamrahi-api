# Security policy

## What the API can and cannot reach

The local API binds to IPv4 `127.0.0.1` and rejects any request whose peer is not a loopback address. It serves four fixed panel assets and the JSON panel routes only; it has no arbitrary file-download route, no directory listing, no CORS, and no remote asset loading. The provider receives only files from folders that you explicitly select. It cannot open a connection back into your computer through this API.

A malicious process already running as your Windows user can still inspect or interact with local applications. This tool cannot protect against that threat; keep Windows, browser extensions, and downloaded software trusted.

Please do not publish credentials, encrypted session files, provider response bodies, private source paths, or personal data in an issue or pull request.

For a suspected vulnerability, open a private GitHub security advisory for this repository. Include a short reproduction, affected version, impact, and a safe fix suggestion. Redact account identifiers and upload URLs. If the report can be reproduced only with a live account, provide a synthetic description first and wait for maintainer instructions.

The panel is designed for a single user on a trusted Windows desktop. It binds to loopback, but a local malicious process running as the same user can usually read or interact with local applications. This is not a server product and should not be exposed to a network.
