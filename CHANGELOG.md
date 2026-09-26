# Changelog

## 0.2.0 (2026-09-26)

Hardening for a first public release.

**Analysis**
- Tempo: one tempo per track whenever the beats support it, found with a phase-coherence fit
  that tolerates the beat tracker wandering or locking onto off-beats in breakdowns. Whole-number
  BPMs when they fit as well; drifting (live, tape) tracks report their average tempo.
- Downbeats every 2 beats are shown as 4/4 bars.
- Swing is measured against each part's own timing, so a part played ahead of the beat is no
  longer reported as swing.
- Sections: long sections split where the instrumentation changes; staged intros and outros
  named as such; "Silence" only when nothing plays; vocal sections no longer called Instrumental.
- Silent, too short, too long and undecodable files fail early with a plain explanation.
- Memory: long tracks (25 min) analysed in ~2.3 GB instead of ~6 GB (chunked rendering and filtering).
- Older analyses are flagged after an upgrade and can be refreshed in one click (stems are reused).

**App**
- Library: search, sort, arrangement thumbnails, retry failed tracks inline.
- Processing view: per-step progress, cancel, retry, re-run from scratch, open the last results.
- Track menu: rename, re-run the analysis, re-run from scratch, delete.
- Settings: Ableton export folder, separation model, storage, licenses.
- Click a chord or note to hear it; export any drum pattern as MIDI.
- Deep links to a view: `#/t/<id>?tab=drums` (or `guide`), `#/t/<id>?sel=bass`.
- Tracks without a clear beat or key say so instead of showing placeholder values; files that
  can't be analysed (silent, too short) offer Delete instead of a pointless Retry.
- Grid fixes (x2, /2, move bar 1) are relative and can be reset.
- Playback: loops clamp to the track, failed audio loads stop cleanly, the metronome is cancelled
  on pause and seek; shortcuts ignore Ctrl/Cmd/Alt.

**Robustness and security**
- The server only answers localhost (DNS-rebinding guard) and state-changing requests need an
  app header (CSRF guard); file serving is whitelisted per folder.
- Uploads are size-limited and validated with ffprobe; re-uploading the same audio reuses the track.
- Pipeline steps write atomically, resume after a crash or restart, and can be cancelled.
- Exports never delete folders the app didn't create.
- Cross-site API requests are refused (Sec-Fetch-Site), the UI can't be framed by other sites
  (CSP frame-ancestors, X-Frame-Options), the host check stays on whatever address the server
  binds to, and binding beyond localhost prints a warning.
- Library telemetry is switched off; `TRACK_ANATOMY_OFFLINE=1` rules out any network access once
  the models are downloaded. See SECURITY.md.

**Project**
- `python -m track_anatomy` CLI (serve, analyze, list, export); configuration via environment variables.
- setup.sh picks the PyTorch build for NVIDIA, Apple Silicon or CPU; pinned requirements; pyproject
  (the web UI ships inside the package, so `pip install` works too; data then goes to
  `~/.local/share/track-anatomy`).
- Unit and API tests, ruff, GitHub Actions CI (Python 3.11 and 3.12).

## 0.1.0

First working version: separation, drum split, beats and sections, note transcription, roles,
harmony, sound-design hints, web UI and Ableton Live Set export.
