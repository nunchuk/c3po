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
- An Amazon Bedrock API key with Claude Haiku 4.5 access

## Setup

### 1. Audio routing (BlackHole)

Meeting audio is captured by routing system sound through a virtual device.

```bash
brew install blackhole-2ch
brew install ffmpeg        # WhisperLiveKit decodes browser audio via ffmpeg
sudo killall coreaudiod    # makes the new device appear
```

> WhisperLiveKit requires a system **ffmpeg** binary (not a pip package). Without
> it the recorder connects but immediately errors with `ffmpeg_not_found` and no
> transcription appears.

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

### Launcher app (double-click instead of a terminal)

```bash
bash scripts/make-overlay-app.sh              # installs to ~/Applications
bash scripts/make-overlay-app.sh /Applications
```

Creates **C-3PO.app**: double-click it (or find it in Spotlight) and it probes for
a running server, starts one if needed, waits until it is ready, and opens the
page. If anything fails it shows a dialog with the diagnosis — a double-clicked
app has no terminal, so a silent failure would just look broken.

The app is a thin wrapper around `scripts/c3po-open.sh`; edit that script to
change behaviour, no rebuild needed. Logs: `~/.c3po-launch.log` (launcher) and
`~/.c3po-server.log` (server).

The icon is generated from `assets/c3po.svg` by `scripts/make-icon.py` (AppKit +
`sips` + `iconutil`, all built into macOS — no `rsvg-convert`/`inkscape` needed).
`make-overlay-app.sh` builds it automatically if missing.

Uninstall: `rm -rf ~/Applications/C-3PO.app`

In the page:
- Press the record button and start the meeting. Transcription + Chinese
  translation appear on the left (WhisperLiveKit's UI).
- Fill in **pre-meeting context** (topic, project background, your role, key terms)
  in the right panel and click **Save context**.
- Reply suggestions refresh automatically as the conversation moves. Click a card
  to copy it. Press **⌘J** to force an immediate refresh.

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
| `C3PO_NUM_SUGGESTIONS` | `3` | Number of short reply options |
| `C3PO_SHORT_MAX_TOKENS` | `220` | Max tokens for the fast short-replies call |
| `C3PO_LONG_MAX_TOKENS` | `320` | Max tokens for the full-answer call |
| `AWS_REGION` | `ap-northeast-1` | Bedrock region |
| `C3PO_PORT` | `8989` | Server port |
| `C3PO_HOST` | `127.0.0.1` | Bind address (numeric on purpose — `localhost` can make uvicorn bind IPv6 only) |
| `C3PO_DISPLAY_HOST` | `localhost` | Hostname shown in the URL and opened in the browser |

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
  for debugging the transcription/translation pipeline.

## Debugging "no subtitles appear"

Several unrelated faults all look identical from the page — nothing shows up. These
tools exist to tell them apart; work down the list to find where audio stops.

| Tool | Answers |
|---|---|
| `scripts/feed_asr.py <file.webm> ws://localhost:8989/asr` | Is the **server** pipeline healthy? It bypasses the browser entirely. Transcript + Chinese here means the fault is browser-side. |
| `/mictest` page | Can the **browser** capture from a given device? Shows the live level and the track the browser actually handed over. Pick BlackHole 2ch and watch the peak. |
| `C3PO_DUMP_AUDIO=/tmp/dump.webm` | What exactly did the browser **send**? Inspect it offline: `ffmpeg -i /tmp/dump.webm -af volumedetect -f null -`. A `mean_volume` near -90 dB means silence was streamed. |
| `/clientlog` (POST `{"msg": "..."}`) | Page-side diagnostics in the server log — the browser console is not readable from a terminal, and Chrome disables JavaScript-via-AppleScript by default. |

Useful server-log signals: `n_tokens_produced: 0` with a healthy
`n_chunks_received` means audio arrived but decoded to nothing; `no speech, stop`
means Whisper judged it silent. Whisper hallucinating a famous speech ("And so my
fellow Americans...") is a reliable tell for **silent input**, not a model problem.

Also note: `curl` to localhost needs `--noproxy '*'` when a proxy is configured,
otherwise it returns `HTTP 000` and a healthy server looks dead.
