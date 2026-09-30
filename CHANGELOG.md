# Changelog

> Format: `## [x.y.z] - YYYY-MM-DD` with `### Added / Changed / Fixed` sections.
> Work that is not part of a release goes under `[Unreleased]`.

## [Unreleased]

### Added — waveform and Save Transcript in the app toolbar (2026-09-30)

- **Waveform.** Hiding the page's button row also hid WLK's waveform, which lives
  inside its record button and was the only sign that audio is arriving. The
  toolbar now draws one next to Record / Stop: `panel.js` reads WLK's
  `analyser` (a top-level binding in `live_transcription.js`, visible to our
  script, so no fork) every 50ms while recording and posts 48 points. Measured
  in WKWebView: 484 frames over a ~25s recording (19.4/s). Samples are boosted
  ×4 and soft-clipped with tanh: raw speech peaks at only ~0.10 of full scale
  (p90), which drew a nearly flat line; displayed now at 0.39 (p90) / 0.89 (max).
- **Save Transcript** (toolbar, ⌘S) writes the English transcript to a `.txt`
  via a save panel, for meeting notes with Claude afterwards. The server keeps it
  in `TranscriptLog`, served at `GET /transcript`: per recording session the
  latest full `lines[].text` (+ uncommitted tail), since WLK resends every line
  and grows the current one in place. Sessions since the server started are kept,
  so a Stop / Record mid-meeting does not lose the first half.

### Fixed — reply suggestions only saw the first fragment of each sentence (2026-09-30)

`_ingest_front_data` appended a line the first time it appeared and never looked
at it again, but WLK grows the newest line in place. Replaying WLK-shaped payloads
through it, the suggestion buffer held `'Today we will'` for "Today we will
discuss the timeline." and flagged the fragment `'Can you'` as the question
instead of "Can you send me the estimates by Friday?". Its line counter also
lived on the shared state while WLK numbers lines from zero after Stop / Record,
so the start of every later session was skipped ("Second session starts here."
never reached the buffer).

Lines are now tracked per recording session and refreshed in place when their
text changes (`SuggestionState.update_line`). A refresh counts as new content
(fallback timer, question check) but not as a new line, so the new-lines trigger
still counts sentences. The same replay after the fix: full sentences, the full
question, and the second session's first line.

### Changed — newest-first transcript without speaker/time labels (2026-09-30)

- `#linesTranscript` is `column-reverse`, so the latest sentence sits under the
  toolbar. WLK writes `<p>meta<br/><div class='textcontent'>...</div></p>`; the
  parser closes the `<p>` at the `<div>`, so each line is sibling nodes and
  reversing them keeps each sentence with its translation. `tameScrolling`
  follows the top instead of the bottom and, while the reader is scrolled into
  history, shifts the position by the growth. Checked through WLK's own
  `renderLinesWithBuffer`: from 400px down, two new lines moved scrollTop to 582
  (text under the reader unchanged); from 30px, it eased back to 0.
- Hidden: speaker icon and number, time range, language chip, and silence rows
  (no diarization, so they carry nothing when reading). Lag indicators stay.
- In the app, WLK's routine status messages ("Finished processing audio! Ready
  to record again.") are hidden: the window subtitle already shows the state.
  Its error messages still show, as a small red footer. Every WLK error message
  contains "error", "invalid", "disconnected" or "could not", and none of its
  routine ones do; checked against all 21 `statusText` assignments in
  `live_transcription.js`.

### Added — C-3PO.app is a native window (2026-09-30)

c3po no longer needs a browser tab. `macos/C3PO.swift` (compiled by
`make-overlay-app.sh`) is an AppKit window with a macOS 26 toolbar — Record /
Stop, plus a Settings / Subtitles / Suggestions toggle group — hosting the server's
page in a WKWebView. It starts the server as its child through the new
`c3po-open.sh --serve` (same preflight and arm64 pinning, then `exec`, so SIGTERM
reaches uvicorn directly), shows startup progress in the window, and stops the
server on quit.

- The toolbar presses the page's own buttons through `window.c3poNative`, and the
  page reports their state back through a `c3po` message handler (`panel.js`). WLK
  keeps the only recording code path; `.c3po-native` just hides the button row.
- Swift rather than PyObjC or pywebview, both of which were tried: python.org
  Python's main bundle is `Python.app`, so a PyObjC window shows as "Python" in the
  menu bar, Dock and ⌘Tab; pywebview implements no
  `requestMediaCapturePermission`, so `getUserMedia` is silently refused. Electron
  would add a ~200 MB runtime for a toolbar that is not native.
- The bundle carries `NSMicrophoneUsageDescription` and an ad-hoc signature, so the
  microphone grant belongs to C-3PO.app and has a stable identity to attach to.
- Quitting (⌘Q or closing the window) asks first while recording, like the page's
  power button did. Before the model has loaded the app SIGKILLs the server:
  uvicorn only acts on SIGTERM after startup, so a SIGTERM there would stall the
  quit for up to a minute. View > Reload Page (⌘R, idle only) re-runs the page,
  e.g. after granting microphone access in System Settings.
- `C3PO_PARENT_PID`: the server compares `getppid()` against it every second and
  shuts down gracefully once the app is gone, so a crashed or force-quit app no
  longer leaves the server holding the port with the overlay up. Measured: after
  `kill -9` of the app, the server logged "Parent app ... is gone", stopped the
  overlay and exited within 1.0s.

### Changed — audio is captured as PCM, not MediaRecorder chunks (2026-09-30)

`pcm_input` is now on by default (`C3PO_PCM_INPUT=0` restores the old path), so
WLK's AudioWorklet streams raw 16 kHz PCM and the server no longer runs ffmpeg.

Required by the native window: WebKit's MediaRecorder accepts WLK's
`start(100)` but ignores the 100ms timeslice — measured 20 chunks in 20s, each
~3.7 KB of Opus, i.e. one-second chunks that would add up to a second of subtitle
lag. The worklet path posts 128-frame quanta: measured 375 messages/s at 48000
samples/s in WKWebView, unchanged while the window was minimized for 30s.
Chrome takes the same path (not measured there). No `/web` mount is needed:
`get_inline_ui_html()` already inlines the worklet and worker as Blob URLs.
The ffmpeg preflight check now only runs with `C3PO_PCM_INPUT=0`.

- BlackHole is stereo, and WLK's worklet forwards channel 0 only, whereas ffmpeg
  mixed both down. `_render_index` now makes the node downmix (L+R)/2
  (`channelCountMode: "explicit"`). A/B with `say` into BlackHole: 2 identical
  channels in, -28.3 dB either way, so centred speech loses nothing.
- WebKit starts capture only while the page is visible. With the test window
  covered by another window, one session sent 0 audio chunks and another started
  8.6s late (the sentence's first half was lost); in front, two runs in a row got
  the full 23.9s. Once running, capture continues hidden or minimized.
- End to end in WKWebView: "Hello everyone. Today we will discuss the migration
  timeline for the third quarter." came back with 大家好。今天我们将讨论第三季度的迁移时间表。

### Fixed — Script Editor opened when launching C-3PO.app (2026-09-30)

The startup notification added earlier today was the cause. `osascript -e
'display notification'` is attributed to Script Editor, and clicking the banner
launches it with an empty Untitled document. Unified log:

    07:21:36 usernoted: Connection com.apple.ScriptEditor2 with path: /usr/bin/osascript
    07:21:50 usernoted: Received response <NotificationRecord app:"com.apple.ScriptEditor2" ...
             <response action: contents ...>
    07:21:50 usernoted: Checking ... for launch of com.apple.ScriptEditor2

The notification is gone; the native window shows startup progress instead, so
nothing in the launch path uses osascript any more.

### Fixed — shutdown hung until SIGKILL while a page was connected (2026-09-30)

`c3po-quit.sh` kept escalating to SIGKILL, and a server started at 07:32 was still
alive hours later with its port already closed (invisible to the quit script,
which finds the server by its listening port). Its log ended at `Waiting for
background tasks to complete`: the `/suggestions` handler waited on its queue and
never noticed its page had gone, and uvicorn's graceful shutdown waits for every
handler, forever by default. SIGKILL then skipped the lifespan shutdown too.

The handler now also waits on `receive_text()` (the page never sends there), so it
ends when the socket closes; `timeout_graceful_shutdown=5` cancels anything else
that still blocks, so the lifespan shutdown always runs. This matters for the app:
its page is connected whenever it quits.

### Fixed — `requirements.txt` was missing boto3 (2026-09-30)

The subtitle translator moved to the Bedrock Converse API (boto3) in the overlay
commit, but boto3 was only present because it happened to be installed; a fresh
`pip3 install -r requirements.txt` would fail at import. Pinned to the installed
1.43.32.

### Fixed — the overlay stacked every partial version of a sentence (2026-09-30)

WLK grows the current line word by word and each version is translated and
appended, so one sentence showed up as a pile of near-duplicates ("Today we will"
/ "Today we will discuss" / ...). A record whose English extends the previous one
now replaces it. On a real 2-sentence test file: 11 records, 2 displayed.

### Added — power button in the page (2026-09-30)

Quitting still required leaving the page (Quit.app or the shell script), so closing
the browser kept leaking a running server holding port 8989.

A power button sits next to the subtitle toggle, reusing WLK's `.settings-toggle`
class so the circle, theme colours and hover state stay consistent with no
duplicated styling. It confirms first: quitting mid-meeting costs the ~80s model
reload to come back.

It posts to `/shutdown`, which **signals the process instead of exiting from the
request handler** — an inline exit would skip the lifespan shutdown handler, and
that handler is what terminates the overlay child. Skipping it orphans an
always-on-top window that cannot then be closed from the UI. The signal is
scheduled on the event loop after a 0.3s delay so the response flushes first,
letting the page distinguish a successful shutdown from a crash. Measured: 7s to
a full clean exit, with `Subtitle overlay stopped` confirming the handler ran.

### Fixed — a cold start looked like the app was not launching (2026-09-30)

Reported as "C-3PO.app does not start any more". It was in fact starting: the
server came up and answered on 8989 every time. The defect is that nothing says
so. The app is `LSUIElement`, so there is no Dock icon, no window and no terminal,
and the Whisper model takes ~80s to load before the port binds — 80 seconds of
total silence is indistinguishable from a failed launch.

- Post a macOS notification when startup begins, so the wait is visibly a wait.
  Non-blocking, unlike a dialog, and best-effort so a denied notification cannot
  stop the launch.
- `READY_TIMEOUT` 90s to 180s. 80s measured against a 90s limit left 10s of
  headroom; a machine under load would have tripped it and reported a failure for
  a server that was merely slow.
- Corrected the message and comment: the model loads on *every* start, not only
  the first, so "first run" was misleading about how long to expect.

### Added — a way to stop c3po (2026-09-30)

Closing the browser only drops the WebSocket: the server stayed up (~41 MB RSS,
holding port 8989) along with the subtitle-overlay child process. There was no
exit path short of hunting the PID by hand.

- `scripts/c3po-quit.sh` — `SIGTERM` so the server runs its shutdown handler,
  wait for a clean exit, escalate to `SIGKILL` only if it stalls, then sweep up an
  orphaned overlay and clear the overlay flag file. Idempotent: safe to run when
  nothing is running.
- `C-3PO Quit.app`, built by the same `make-overlay-app.sh`. The bundle-writing
  logic became a `build_app` function rather than a second copy of ~90 lines.

Finds the server via `lsof` on the port rather than `pgrep -f c3po.server`, which
also matches the launching shell and the quit script itself.

The grace period is 30s because a clean shutdown was **measured at 15s** once the
overlay child exists (the handler terminates it and waits up to 5s). The first
version used 10s and escalated to `SIGKILL` every single time — self-defeating,
since `SIGKILL` skips the handler and is exactly what orphans the always-on-top
overlay window. Worth noting the earlier 1s measurement was taken before the
overlay existed, so it no longer applied.

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

Kept deliberately: each one is what actually located a fault above, and the audio
path has several failure modes that all present identically as "no subtitles".

- `/mictest` page (`c3po/static/mictest.html`) — shows the live input level per
  audio device, to separate "the browser is capturing silence" from "the server
  pipeline is broken".
- `/clientlog` endpoint — lets the page report diagnostics into the server log,
  since the browser console is not reachable from a terminal and Chrome disables
  JavaScript execution via AppleScript by default.
- `C3PO_DUMP_AUDIO=<path>` — writes exactly what the browser sends to the ASR
  socket, so a silent or undecodable stream can be inspected with `ffmpeg`.
