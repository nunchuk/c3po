# c3po

A local meeting assistant for MS Teams / Zoom on Apple Silicon. It does two things:

1. **Live English→Chinese translation** of what the other side says — fully local
   (WhisperLiveKit + `mlx-whisper` + NLLW/NLLB 600M). No audio leaves your machine.
2. **English reply suggestions** that refresh continuously in the background, so a
   few ready-to-use replies always sit in the panel — useful when you get suddenly
   called on. Generated on Amazon Bedrock (Claude Haiku 4.5); only short text
   snippets (recent transcript + your context) are sent, never audio.

The UI is WhisperLiveKit's own transcription page with a reply-suggestion panel
injected on the right.

## Requirements

- macOS on Apple Silicon (developed on M4 / 16GB)
- Python 3.10+
- [Homebrew](https://brew.sh)
- Xcode Command Line Tools, to build C-3PO.app (`xcode-select --install`)
- An Amazon Bedrock API key with Claude Haiku 4.5 access

## Setup

### 1. Audio routing (BlackHole)

Meeting audio is captured by routing system sound through a virtual device.

```bash
brew install blackhole-2ch
sudo killall coreaudiod    # makes the new device appear
```

> **ffmpeg** is only needed with `C3PO_PCM_INPUT=0` (MediaRecorder/WebM capture,
> decoded server-side): `brew install ffmpeg`. Without it that mode connects and
> then errors with `ffmpeg_not_found`. The default PCM capture does not use it.

Then create a **Multi-Output Device** so you can hear the meeting *and* feed it to c3po:

1. Open **Audio MIDI Setup** (in /Applications/Utilities).
2. Click **+** (bottom-left) → **Create Multi-Output Device**.
3. Check **exactly two** devices: the output you actually listen on (your
   headphones/speakers, e.g. **Creative T60**) **and** **BlackHole 2ch**. Set your
   listening device as the **Primary Device**. Do **not** check the virtual
   meeting devices (Microsoft Teams Audio, ZoomAudioDevice) or extra speakers —
   that causes echo/feedback.
4. Set this Multi-Output Device as the macOS **system output**. Note it does
   **not** appear in System Settings → Sound → Output. Select it either by:
   - right-clicking it in **Audio MIDI Setup** → **Use This Device For Sound Output**, or
   - the **menu-bar Sound control / Control Center**, where it does appear.

In the c3po web page, select **BlackHole 2ch** as the microphone — that is what
carries the meeting audio into the transcriber.

> Note: when you speak, the meeting app still uses your real microphone. BlackHole
> only carries what the *other* participants say (your system output).

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

### 3. Bedrock credentials

```bash
cp .env.example .env
# Edit .env: set AWS_BEARER_TOKEN_BEDROCK to your Bedrock API key.
```

Region defaults to `ap-northeast-1` (Tokyo) but the model id is the `global.` one,
which routes dynamically, so any region with Bedrock works. If a region cannot
reach the model, set `AWS_REGION=us-east-1` in `.env`.

Verify the connection:

```bash
PYTHONPATH=. python3 scripts/verify_bedrock.py
```

## Run

```bash
./run.sh
```

This starts the server and opens `http://localhost:8989`. The first run downloads
the Whisper (`small`) and NLLB (600M) models — a few minutes.

### C-3PO.app (native window)

```bash
bash scripts/make-overlay-app.sh              # installs to ~/Applications
bash scripts/make-overlay-app.sh /Applications
```

Builds **C-3PO.app**, a native macOS window (`macos/C3PO.swift`, compiled with
`swiftc`). Double-click it or find it in Spotlight: it attaches to a running
server or starts one (`scripts/c3po-open.sh --serve`), shows a spinner while the
Whisper model loads (45–80s, on every start), then shows the page in the window.
If startup fails, the window shows the end of `~/.c3po-server.log` with **Retry**
and **Open Log**.

The toolbar replaces the page's button row:

| Toolbar | Does |
|---|---|
| Record / Stop | Start / stop transcription |
| Waveform | Live input level while recording — a flat line means no audio is arriving |
| Save Transcript (also ⌘S) | Save the English transcript as a `.txt` file, e.g. to hand to Claude for meeting notes |
| Settings (gear) | WLK's settings panel — pick **BlackHole 2ch** as the microphone here once; it is remembered |
| Subtitles | Show / hide the floating subtitle overlay |
| Suggestions | Show / hide the reply-suggestion sidebar |

The transcript is **newest first**: the latest sentence always sits right under
the toolbar. Scroll down to read history; new lines then no longer move the text
you are reading, and scrolling back to the top resumes following. The state
(Ready, Recording 00:12, ...) is shown under the window title; WLK's own status
messages are hidden except errors (e.g. microphone denied, server gone), which
appear as a small red line at the bottom.

**Saved transcripts** contain every recording session since the server started
(sessions separated by a blank line), English only, without timestamps — a Stop /
Record mid-meeting clears the page but not the saved text. The same text is at
`http://localhost:8989/transcript`, also in browser mode.

**Microphone permission.** The page asks to let C-3PO use the microphone once it
has loaded — allow it. If you declined, enable C-3PO under System Settings →
Privacy & Security → Microphone, then View → Reload Page (⌘R); to be asked again
from scratch, `tccutil reset Microphone com.nunchuk.c3po`. The app is ad-hoc
signed and macOS ties the grant to the code hash, so rebuilding after a change to
`macos/C3PO.swift` asks again.

**Press Record with the window visible.** WebKit only starts capture while the
page is on screen; once recording, the window can sit behind Zoom or be
minimized. Quitting while recording asks for confirmation first.

Quitting the app (⌘Q, or closing the window) stops the server and the overlay.
If the app crashes or is force-quit, the server notices within a second and shuts
itself down (`C3PO_PARENT_PID`). A server that was already running when the app
started (e.g. from `./run.sh`) is left running on quit.

It also creates **C-3PO Quit.app** for stopping everything — see "Stopping c3po".

Changing `macos/C3PO.swift` needs a rebuild; startup checks and server behaviour
live in `scripts/c3po-open.sh`, which does not. Logs: `~/.c3po-server.log`,
`~/.c3po-quit.log`.

**Browser mode** still works: `bash scripts/c3po-open.sh` starts the server in the
background and opens the page in your default browser.

The icon is generated from `assets/c3po.svg` by `scripts/make-icon.py` (AppKit +
`sips` + `iconutil`, all built into macOS — no `rsvg-convert`/`inkscape` needed).
`make-overlay-app.sh` builds it automatically if missing.

Uninstall: `rm -rf ~/Applications/C-3PO.app`

In the window (or the browser page):
- Press Record (the toolbar in the app, the round button in a browser) and start
  the meeting. Transcription + Chinese translation appear on the left
  (WhisperLiveKit's UI).
- In the right panel, fill in **About me** (name, role, team, what you own — kept
  across meetings) and paste a **Meeting background** before each meeting (e.g.
  one you had an AI draft: agenda, participants, history, open questions), then
  **Save**. Both are saved to `~/Library/Application Support/c3po/context.json`;
  once saved the editor folds into one line (**Edit** reopens it).
- Two reply cards follow the conversation, each with its Chinese translation:
  **Quick reply** (1–2 sentences, first words in about a second) and **Full
  answer** (3–6 sentences to read out). Anything the model can't know — your
  progress, dates, numbers — comes as a `[placeholder]` for you to fill in rather
  than a guess. A finished question ("…?") refreshes them immediately; otherwise
  at most every 8s. Click a card to copy its English. **⌘J** forces a refresh.
  While the pointer is on the cards they stay put, even with Zoom in front;
  a newer reply waits until the pointer leaves.

Cost: each refresh is two Claude Haiku 4.5 calls of a few hundred to a few
thousand input tokens. Prompt caching applies only once the fixed part
(instructions + About me + Meeting background) reaches Haiku 4.5's 4096-token
minimum; below that it is silently skipped. The server log's `suggestion ...
cache read N` lines show whether it is being hit.

## Stopping c3po

**Closing the browser does not stop c3po.** That only drops the WebSocket — the
server keeps running (~41 MB), holds port 8989, and keeps the subtitle-overlay
child process alive.

Ways to quit, all ending in the same clean shutdown:

- **Quit C-3PO.app** (⌘Q, or close its window).
- **The power button in the page** (browser mode; the app hides it because quitting
  the app does the same), next to the subtitle toggle. It asks for confirmation
  first — quitting mid-meeting means waiting ~80s to get back.
- **C-3PO Quit.app** — double-click, no terminal needed.
- ```bash
  bash scripts/c3po-quit.sh
  ```

All of them send `SIGTERM` so the server runs its shutdown handler (stop the
suggester, terminate the overlay child, clear the overlay flag). The script waits
for a clean exit and only escalates to `SIGKILL` if that stalls. Shutdown takes
about a second (measured with the app's page still connected).

The page's button posts to `/shutdown`, which signals the process rather than
exiting from the request handler — exiting inline would skip the handler and leave
the always-on-top overlay orphaned. The response is sent before the signal, so the
page can report success instead of seeing a dropped connection.

Avoid `kill -9` / `pkill -9` on the server yourself: the handler never runs. The
overlay notices its parent is gone and exits on its own, and
`scripts/c3po-quit.sh` sweeps up anything left over.

## Configuration

All tunables live in `c3po/config.py` and can be overridden via environment
variables (loaded from `.env`). Common ones:

| Variable | Default | Meaning |
|---|---|---|
| `C3PO_WHISPER_MODEL` | `base.en` | Whisper model; `small`/`small.en` = more accurate but higher latency |
| `C3PO_DIARIZATION` | `0` | Speaker labels; `1` needs NeMo or diart installed (heavy) |
| `C3PO_MIN_TOKENS_PER_LINE` | `8` | Don't break at punctuation until a line has N tokens (avoids tiny fragments). Lower = more, shorter segments |
| `C3PO_MAX_TOKENS_PER_LINE` | `30` | Force a line break after N tokens even without punctuation (run-on speech) |
| `C3PO_BUFFER_TRIMMING_SEC` | `6` | Max seconds before a transcript segment is force-trimmed |
| `C3PO_BACKEND` | `faster-whisper` | ASR backend; `mlx-whisper` is faster but loses translation |
| `C3PO_DEBOUNCE_SECONDS` | `8` | Min seconds between Bedrock calls |
| `C3PO_FALLBACK_SECONDS` | `20` | Regenerate after this if there's new content |
| `C3PO_NEW_LINES_TRIGGER` | `2` | Regenerate after N new finalized lines |
| `C3PO_CONTEXT_WINDOW_LINES` | `24` | Recent lines sent to Bedrock per call (split into background + immediate tiers) |
| `C3PO_IMMEDIATE_LINES` | `6` | Of the window, the trailing lines the reply must directly address; the rest are background |
| `C3PO_SHORT_MAX_TOKENS` | `250` | Max tokens for the quick reply + its translation |
| `C3PO_LONG_MAX_TOKENS` | `800` | Max tokens for the full answer + its translation |
| `C3PO_SUGGESTION_TEMPERATURE` | `0.3` | Sampling temperature for replies (higher = more varied, more likely to fill in facts) |
| `C3PO_CONTEXT_FILE` | `~/Library/Application Support/c3po/context.json` | Where About me / Meeting background are saved |
| `AWS_REGION` | `ap-northeast-1` | Bedrock region |
| `C3PO_PORT` | `8989` | Server port |
| `C3PO_HOST` | `127.0.0.1` | Bind address (numeric on purpose — `localhost` can make uvicorn bind IPv6 only) |
| `C3PO_DISPLAY_HOST` | `localhost` | Hostname shown in the URL and opened in the browser |
| `C3PO_PCM_INPUT` | `1` | Stream raw PCM from an AudioWorklet. `0` = MediaRecorder/WebM (needs ffmpeg; in the app's WebKit it arrives in ~1s chunks, adding subtitle lag) |

## Notes

- **Backend choice.** Default is `faster-whisper` (CTranslate2) because it is the
  only backend where NLLW EN→ZH translation works, and it's comfortably real-time
  on M4 (~1.25s latency). The `mlx-whisper` backend transcribes a bit faster but
  (a) its SimulStreaming token format breaks NLLW so **no Chinese translation
  appears**, and (b) it needs a GPU-stream monkey-patch (`c3po/mlx_thread_fix.py`,
  applied automatically when selected) to avoid `There is no Stream(gpu, N) in
  current thread`. Only set `C3PO_BACKEND=mlx-whisper` if you want max
  transcription speed and don't need translation.
- **SSL.** On corporate networks that intercept TLS, the server points urllib at
  `/etc/ssl/cert.pem` so model downloads succeed (override via `C3PO_SSL_CERT_FILE`).

## Verification scripts

- `scripts/verify_bedrock.py` — checks Bedrock connectivity and the model id.
- `scripts/verify_translation.py` — checks the local NLLB EN→ZH translation path.
- `scripts/feed_asr.py` — feeds a WebM file straight to `/asr` (no browser), handy
  for debugging the transcription/translation pipeline. Run the server with
  `C3PO_PCM_INPUT=0` for it: the default PCM mode does not decode WebM.

## Debugging "no subtitles appear"

Several unrelated faults all look identical from the page — nothing shows up. These
tools exist to tell them apart; work down the list to find where audio stops.

| Tool | Answers |
|---|---|
| `scripts/feed_asr.py <file.webm> ws://localhost:8989/asr` | Is the **server** pipeline healthy? It bypasses the browser entirely. Transcript + Chinese here means the fault is browser-side. |
| `/mictest` page | Can the **browser** capture from a given device? Shows the live level and the track the browser actually handed over. Pick BlackHole 2ch and watch the peak. |
| `C3PO_DUMP_AUDIO=/tmp/dump.pcm` | What exactly did the page **send**? By default that is raw 16 kHz mono s16le: `ffmpeg -f s16le -ar 16000 -ac 1 -i /tmp/dump.pcm -af volumedetect -f null -` (WebM with `C3PO_PCM_INPUT=0`: `ffmpeg -i /tmp/dump.webm ...`). A `mean_volume` near -90 dB means silence was streamed. |
| `/clientlog` (POST `{"msg": "..."}`) | Page-side diagnostics in the server log — the browser console is not readable from a terminal, and Chrome disables JavaScript-via-AppleScript by default. |

Useful server-log signals: `n_tokens_produced: 0` with a healthy
`n_chunks_received` means audio arrived but decoded to nothing; `no speech, stop`
means Whisper judged it silent. Whisper hallucinating a famous speech ("And so my
fellow Americans...") is a reliable tell for **silent input**, not a model problem.

Also note: `curl` to localhost needs `--noproxy '*'` when a proxy is configured,
otherwise it returns `HTTP 000` and a healthy server looks dead.
