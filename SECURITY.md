# Security

## Reporting a problem

Please report vulnerabilities privately through GitHub's **Security → Report a vulnerability**
on this repository rather than in a public issue. Include what an attacker could do and how to
reproduce it.

## What Track Anatomy is designed for

A single-user app on your own computer. The server has **no login**, so its protections are about
making sure only the app's own page, running on your machine, can drive it:

| Threat | Protection |
|---|---|
| Other devices on the network | Listens on `127.0.0.1` only by default. Binding elsewhere prints a warning. |
| A website you visit calling `http://localhost:8102` | API requests marked cross-site by the browser (`Sec-Fetch-Site`) are refused. State-changing requests need a custom header, which a cross-site page can't send without a CORS grant, and the server grants none. |
| DNS rebinding (a site whose domain resolves to 127.0.0.1) | Requests are refused unless the `Host` header is `localhost` / `127.0.0.1` / `[::1]` or listed in `TRACK_ANATOMY_ALLOWED_HOSTS`. |
| Clickjacking (the UI in a hidden frame) | `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY`. The CSP also restricts scripts to the app's own files. |
| Path traversal via the file API | Files are served only from whitelisted per-track folders, and resolved paths must stay inside the track folder. |
| Oversized or bogus uploads | Size limit (`TRACK_ANATOMY_MAX_UPLOAD_MB`), extension whitelist, and every upload is probed with `ffprobe` before it is accepted. |
| Destroying user folders on export | "Save Ableton project" only replaces a folder that carries the app's marker file, and otherwise picks a new name. |

Not in scope: running Track Anatomy as a service for other people. If you expose it beyond
localhost (for example behind a reverse proxy), put your own authentication in front of it.

## Things to be aware of

- **Untrusted audio files** are decoded by ffmpeg, which has had parser vulnerabilities over the
  years. Keep ffmpeg up to date, as you would for a media player.
- **Model weights** are downloaded on first use over HTTPS from their publishers' hosts (GitHub
  releases used by python-audio-separator, Meta's Demucs host, JKU for beat_this, and Hugging
  Face for All-In-One). Some are PyTorch checkpoints, which are pickle files: loading one from a
  compromised host could run code. The app does not load models from anywhere else.

## Known advisories in pinned dependencies

- **PyTorch 2.11 — CVE-2025-3000** (memory corruption in `torch.jit.script`, fixed in 2.13).
  Track Anatomy never JIT-compiles code, trusted or not, so it isn't reachable here. The pin
  will move to 2.13+ once separation and beat tracking are re-validated on it.

Checked with `pip-audit` on 2026-09-26; nothing else is flagged.

## Privacy

No accounts, no analytics, no telemetry (library telemetry is switched off). Audio and analysis
results never leave your machine. The only network traffic is downloading model weights; one
library (Hugging Face) also checks for updated weights on each analysis. Set
`TRACK_ANATOMY_OFFLINE=1` once the models are downloaded to rule out any network access.
