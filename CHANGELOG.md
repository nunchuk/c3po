# Changelog

> Format: `## [x.y.z] - YYYY-MM-DD` with `### Added / Changed / Fixed` sections.
> Work that is not part of a release goes under `[Unreleased]`.

## [Unreleased]

### Added — macOS launcher app (2026-09-29)

Double-click launcher so starting c3po no longer means opening a terminal and
remembering the module invocation.

- `scripts/c3po-open.sh` — the actual logic: probe for a running server, preflight
  checks (`.venv`, `.env` with a non-empty key, `ffmpeg` on PATH, port not taken by
  something unresponsive), start the server, poll until ready, open the browser.
- `scripts/make-overlay-app.sh` — builds `~/Applications/C-3PO.app` as a thin
  wrapper around that script, modelled on `cdd-kirocrew/scripts/make-dashboard-app.sh`
  (`LSUIElement=1`, forced UTF-8 locale, failure dialog via `osascript`,
  `plutil -lint`, `lsregister -f`).
- `scripts/make-icon.py` + `assets/c3po.svg` — SVG to `.icns` using only AppKit
  (`NSImage` reads SVG directly), `sips` and `iconutil`. No `rsvg-convert` /
  `inkscape` / `cairosvg` dependency.

Startup waits by polling, not a fixed sleep: the first run binds the port only
after loading the Whisper model, measured at 80s on this machine.

### Fixed — GUI launch died on an architecture mismatch (2026-09-29)

Launching from the terminal worked; double-clicking the app failed with
`ImportError: ... incompatible architecture (have 'arm64', need 'x86_64')` on
`pydantic_core`.

Cause: `.venv/bin/python` is a **universal binary**. From a terminal it runs
arm64, but a GUI-launched app can inherit an x86_64 preference and start the same
binary in x86_64 mode — where the arm64-only wheels cannot load. Fix: run the
server under `arch -arm64`.

The first attempt at this fix was itself wrong, in a way worth recording: it
gated on `uname -m`, which reports `x86_64` under Rosetta — false in exactly the
case that needs the fix, so the app kept failing while the terminal kept working.
Detect the hardware with `sysctl -n hw.optional.arm64`, which reports the real
machine either way.

Also sets `PATH=/opt/homebrew/bin:...` in the launcher: GUI-launched apps do not
inherit Homebrew's path, and c3po requires the `ffmpeg` binary.

### Fixed — transcription produced no tokens at all (2026-09-29)

Audio arrived (652 chunks / 79s in one session) but `n_tokens_produced` stayed 0
and the only transcript output was Whisper's classic silence hallucination ("And
so my fellow Americans...").

Three independent faults, found in this order:

1. **`faster-whisper` 1.2.1 against `numpy` 2.2.6** — the mel-spectrogram matmul
   overflows (`divide by zero` / `overflow` / `invalid value encountered in
   matmul`), yielding NaN features. Confirmed by feeding a clean 440 Hz sine wave,
   which failed the same way, ruling out the audio path. Pinned `numpy<2` in
   `requirements.txt`.
2. **WLK's UI hardcodes `ws://localhost:8000/asr`** — after the port moved to
   8989 the recorder connected to nothing and sessions died after 1–2s. Rewritten
   in `_render_index()` to derive from `location.host`, so it also survives a
   tunnel or LAN IP.
3. **Chrome's audio processing muted the BlackHole loopback** — Chrome treats
   BlackHole as a microphone and its auto gain control decides the signal is
   noise. Captured what the browser actually sent and measured the decay:
   -45 dB at 0–2s down to -91 dB (digital silence) by 16s. `_render_index()` now
   injects `echoCancellation: false, noiseSuppression: false,
   autoGainControl: false` into every `getUserMedia` call.

### Changed — server address is now `localhost:8989` (2026-09-29)

Port 8000 to 8989, and the displayed hostname is `localhost`.

Bind address and display address are deliberately separate: `config.host` stays
numeric (`127.0.0.1`) because handing uvicorn a hostname can make it bind IPv6
`::1` only, breaking IPv4 clients. `config.display_host` is what appears in the
URL and opens in the browser. Both `localhost:8989` and `127.0.0.1:8989` work.

### Added — debugging aids (2026-09-29)

Temporary, kept for now pending a decision to remove:

- `/mictest` page (`c3po/static/mictest.html`) — shows the live input level per
  audio device, to separate "the browser is capturing silence" from "the server
  pipeline is broken".
- `/clientlog` endpoint — lets the page report diagnostics into the server log,
  since the browser console is not reachable from a terminal and Chrome disables
  JavaScript execution via AppleScript by default.
- `C3PO_DUMP_AUDIO=<path>` — writes exactly what the browser sends to the ASR
  socket, so a silent or undecodable stream can be inspected with `ffmpeg`.
