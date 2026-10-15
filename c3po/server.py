"""c3po FastAPI server.

Single process that:
  - embeds WhisperLiveKit's speech-to-text pipeline (see WLK basic_server.py) and
    translates each transcript line to Chinese on Bedrock (translator.py)
  - serves WLK's built-in UI with an injected reply-suggestion panel
  - runs a background task that continuously regenerates English reply suggestions
    on Amazon Bedrock (Claude Haiku 4.5) and pushes them to the panel via WebSocket

The transcription/translation UI is WLK's own; we only add the suggestion panel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from c3po.config import config

# Point urllib/requests at the system cert bundle BEFORE importing whisperlivekit,
# so its model downloads survive corporate SSL interception. Must run before the
# whisperlivekit import below triggers any network access.
if config.ssl_cert_file and os.path.exists(config.ssl_cert_file):
    os.environ.setdefault("SSL_CERT_FILE", config.ssl_cert_file)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", config.ssl_cert_file)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, PlainTextResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from whisperlivekit import (  # noqa: E402
    AudioProcessor,
    TranscriptionEngine,
    get_inline_ui_html,
)
from whisperlivekit.config import WhisperLiveKitConfig  # noqa: E402

from c3po import loop_guard, mlx_thread_fix, segment_fix  # noqa: E402
from c3po.suggester import Suggester  # noqa: E402
from c3po.translator import Translator  # noqa: E402

# Patch MLX so transcription works on WLK's worker threads (no-op for non-MLX).
if config.backend == "mlx-whisper":
    mlx_thread_fix.apply()

# Split transcript lines on sentence punctuation. Safe now that translation is
# done by c3po (Bedrock) per-sentence rather than WLK's NLLW — there's no longer
# a timestamp-coupled translation to break. No-op if diarization is on.
if not config.diarization:
    segment_fix.apply()

# Reset the decoder when Whisper locks into repeating one phrase ("No. No. No."),
# which WLK only recovers from after >=5s of silence.
loop_guard.apply()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("c3po")
logger.setLevel(logging.INFO)

# Shared singletons, initialized in lifespan.
transcription_engine: TranscriptionEngine | None = None
suggester: Suggester | None = None
translator: Translator | None = None
overlay_process: subprocess.Popen | None = None

_PANEL_DIR = Path(__file__).parent / "static"


# --- Floating subtitle overlay (child process) ---


def _set_overlay_visible(visible: bool) -> None:
    """Create or remove the flag file the overlay polls."""
    path = config.overlay_visible_flag
    if visible:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    elif os.path.exists(path):
        os.remove(path)


def _start_overlay() -> subprocess.Popen | None:
    """Launch the overlay in its own process.

    PyObjC needs the main thread for its run loop and uvicorn already owns ours,
    so the overlay cannot live in this process.
    """
    # Truncate: the overlay only reads the tail, but a stale file from the last
    # session would briefly show subtitles from a meeting that already ended.
    try:
        open(config.subtitle_file, "w").close()
    except OSError:
        logger.warning("Could not truncate %s", config.subtitle_file, exc_info=True)

    _set_overlay_visible(False)   # start hidden; the web button reveals it
    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "c3po.overlay",
                "--subtitle-file", config.subtitle_file,
                "--visible-flag", config.overlay_visible_flag,
                # So a SIGKILL'd server does not leave the window orphaned.
                "--parent-pid", str(os.getpid()),
            ],
            cwd=str(Path(__file__).parent.parent),
        )
        logger.info("Subtitle overlay started (pid=%s)", proc.pid)
        return proc
    except OSError:
        logger.warning("Failed to start the subtitle overlay", exc_info=True)
        return None


async def _exit_with_parent(parent_pid: int) -> None:
    """Shut down once the app that started the server is gone.

    C-3PO.app stops the server on a normal quit; this covers a crash or force
    quit. Compares getppid() instead of probing the PID: an orphan is
    re-parented to launchd, so a changed parent is unambiguous even if the PID
    is reused. Sends SIGTERM rather than exiting, so the lifespan shutdown still
    runs and terminates the overlay (same path as /shutdown).
    """
    while os.getppid() == parent_pid:
        await asyncio.sleep(1.0)
    logger.info("Parent app (pid=%s) is gone; shutting down", parent_pid)
    os.kill(os.getpid(), signal.SIGTERM)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the shared transcription engine and start the suggestion loop."""
    global transcription_engine, suggester, translator, overlay_process

    # No target_language: WLK only transcribes. c3po translates each finalized
    # English sentence on Bedrock (see translator.py), which frees NLLB's ~1.5GB
    # and decouples translation from segmentation.
    wlk_config = WhisperLiveKitConfig.from_kwargs(
        model_size=config.whisper_model,
        lan=config.source_language,
        backend=config.backend,
        diarization=config.diarization,
        buffer_trimming_sec=config.buffer_trimming_sec,
        pcm_input=config.pcm_input,
        host=config.host,
        port=config.port,
    )
    transcription_engine = TranscriptionEngine(config=wlk_config)

    suggester = Suggester()
    suggester.start()
    translator = Translator()
    if config.overlay_enabled:
        overlay_process = _start_overlay()
    watchdog = (
        asyncio.create_task(_exit_with_parent(config.parent_pid))
        if config.parent_pid else None
    )
    logger.info("c3po ready: whisper=%s translate=%s backend=%s pcm_input=%s",
                config.whisper_model, config.translate_model_id, config.backend,
                config.pcm_input)
    try:
        yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if suggester is not None:
            await suggester.stop()
        if overlay_process is not None and overlay_process.poll() is None:
            overlay_process.terminate()
            try:
                overlay_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                overlay_process.kill()
            logger.info("Subtitle overlay stopped")
        _set_overlay_visible(False)


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _render_index() -> str:
    """Return WLK's UI with the c3po suggestion panel injected before </body>."""
    html = get_inline_ui_html()

    # WLK's UI hardcodes `ws://localhost:8000/asr`. When c3po runs on any other
    # port the recorder connects to nothing and the session dies after a second,
    # with no transcript. Rewrite it to the port we actually serve on, derived
    # from the page's own origin so it also works over a tunnel or LAN IP.
    html = html.replace(
        'let websocketUrl = "ws://localhost:8000/asr";',
        'let websocketUrl = `${location.protocol === "https:" ? "wss" : "ws"}'
        '://${location.host}/asr`;',
        1,
    )

    # Meeting audio arrives through BlackHole, which Chrome treats as a
    # microphone and then "cleans up": auto gain control decides the loopback
    # signal is noise and fades it out (measured: -45 dB -> -91 dB within
    # seconds), so the transcript stays empty. Disable the three voice-input
    # processors for every capture path in WLK's UI.
    raw_audio = (
        "{ audio: { deviceId: { exact: selectedMicrophoneId },"
        " echoCancellation: false, noiseSuppression: false,"
        " autoGainControl: false } }"
    )
    html = html.replace(
        "{ audio: { deviceId: { exact: selectedMicrophoneId } } }",
        raw_audio,
    )
    html = html.replace(
        ": { audio: true };",
        ": { audio: { echoCancellation: false, noiseSuppression: false,"
        " autoGainControl: false } };",
    )

    # BlackHole delivers stereo. WLK's PCM worklet forwards channel 0 only,
    # whereas the ffmpeg path mixed both channels down; make the node downmix
    # (L+R)/2 itself so audio panned to one side is not dropped.
    html = html.replace(
        "{ numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1 }",
        '{ numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1,'
        ' channelCountMode: "explicit", channelInterpretation: "speakers" }',
    )

    panel_css = (_PANEL_DIR / "panel.css").read_text(encoding="utf-8")
    panel_js = (_PANEL_DIR / "panel.js").read_text(encoding="utf-8")
    injection = f"<style>{panel_css}</style>\n<script>{panel_js}</script>\n"
    if "</body>" in html:
        return html.replace("</body>", injection + "</body>", 1)
    return html + injection


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_render_index())


class ClientLog(BaseModel):
    msg: str


@app.post("/clientlog")
async def clientlog(entry: ClientLog) -> dict:
    """Diagnostic sink: surface browser-side messages in the server log."""
    logger.info("[browser] %s", entry.msg)
    return {"ok": True}


@app.get("/mictest")
async def mictest() -> HTMLResponse:
    """Diagnostic page: shows the live input level per audio device.

    Use it when no transcript appears, to tell "the browser is capturing
    silence" apart from "the server pipeline is broken".
    """
    path = _PANEL_DIR / "mictest.html"
    if not path.exists():
        return HTMLResponse("mictest.html not found", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# --- Context panel API ---


class ContextBody(BaseModel):
    me: str = ""
    meeting: str = ""


@app.get("/context")
async def get_context() -> dict:
    """The saved About me / Meeting background, so the panel can refill them."""
    assert suggester is not None
    return {"me": suggester.state.me, "meeting": suggester.state.meeting}


@app.post("/context")
async def set_context(body: ContextBody) -> dict:
    assert suggester is not None
    suggester.set_context(body.me, body.meeting)
    return {"ok": True}


@app.post("/refresh")
async def force_refresh() -> dict:
    """Manual immediate regeneration (panel button / hotkey)."""
    assert suggester is not None
    suggester.force_refresh()
    return {"ok": True}


# --- Overlay show/hide (page button) ---


class OverlayBody(BaseModel):
    visible: bool


def _overlay_state() -> dict:
    running = overlay_process is not None and overlay_process.poll() is None
    return {
        "visible": os.path.exists(config.overlay_visible_flag),
        "running": running,
    }


@app.get("/overlay")
async def get_overlay() -> dict:
    """Current overlay state, so the page can restore its button after reload."""
    return _overlay_state()


@app.post("/overlay")
async def set_overlay(body: OverlayBody) -> dict:
    _set_overlay_visible(body.visible)
    return _overlay_state()


# --- Shutdown (page power button) ---


@app.post("/shutdown")
async def shutdown() -> dict:
    """Stop the server from the page.

    Closing the browser only drops the WebSocket -- the process keeps running and
    holds the port, so there has to be a way out from the UI itself.

    Signals the process rather than calling sys.exit: SIGTERM is what uvicorn
    listens for, and it runs the lifespan shutdown handler, which stops the
    suggester and terminates the overlay child. Exiting straight from the request
    would skip that and orphan the always-on-top overlay window.

    Scheduled on the event loop instead of sent inline so this response is
    delivered first -- otherwise the page sees a dropped connection and cannot
    tell a successful shutdown from a crash.
    """
    logger.info("Shutdown requested from the page")

    async def _terminate() -> None:
        await asyncio.sleep(0.3)   # let the response flush
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_terminate())
    return {"stopping": True}


# --- Suggestions WebSocket (server -> panel) ---


@app.websocket("/suggestions")
async def suggestions_ws(websocket: WebSocket) -> None:
    assert suggester is not None
    await websocket.accept()
    queue = suggester.subscribe()

    async def forward() -> None:
        while True:
            await websocket.send_json(await queue.get())

    # Watch for the disconnect instead of only waiting on the queue. A handler
    # parked on queue.get() never notices its page went away, and uvicorn's
    # graceful shutdown waits for every handler: the server then hung at
    # "Waiting for background tasks to complete" until SIGKILL, skipping the
    # lifespan shutdown. The page never sends here, so receive() just blocks
    # until the socket closes.
    sender = asyncio.create_task(forward())
    try:
        await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        # Collect its outcome (a failed send on a closed socket is expected) so
        # asyncio does not log "Task exception was never retrieved".
        await asyncio.gather(sender, return_exceptions=True)
        suggester.unsubscribe(queue)


# --- Meeting transcript (Save Transcript in C-3PO.app) ---


class TranscriptLog:
    """The meeting's English transcript, served as plain text by /transcript.

    WLK resends every line on each update and grows the current line in place,
    so each recording session keeps the latest full version instead of
    appending lines as they first appear. Sessions since the server started
    stay in order: a Stop/Record mid-meeting clears the page, not the export.
    """

    def __init__(self) -> None:
        self._sessions: list[list[str]] = []

    def new_session(self) -> list[str]:
        session: list[str] = []
        self._sessions.append(session)
        return session

    @staticmethod
    def update(session: list[str], payload: dict) -> None:
        texts = [(line.get("text") or "").strip() for line in payload.get("lines") or []]
        # Words heard but not yet committed to a line; WLK flushes them at stop.
        texts.append((payload.get("buffer_transcription") or "").strip())
        session[:] = [t for t in texts if t]

    def text(self) -> str:
        return "\n\n".join("\n".join(s) for s in self._sessions if s)


transcript_log = TranscriptLog()


@app.get("/transcript")
async def get_transcript() -> PlainTextResponse:
    """English only, one line per transcript line, sessions separated by a blank line."""
    return PlainTextResponse(transcript_log.text())


# --- ASR WebSocket (browser mic -> WLK; also feeds the rolling buffer) ---


def _ingest_front_data(payload: dict, tracked: list) -> None:
    """Keep the suggester's buffer in step with WLK's transcript lines.

    WLK resends the full `lines` list on every update and grows the newest line
    in place ("Today we will" -> "Today we will discuss the timeline."). A line
    seen for the first time is appended; one already buffered is refreshed when
    its text changes, so suggestions see whole sentences rather than the first
    fragment of each. `buffer_transcription` (the uncommitted tail) is ignored.

    `tracked` belongs to one recording session and maps each line index to its
    buffer entry (None while the line has no words yet). It must not be shared:
    WLK numbers lines from zero again after Stop / Record, which made a shared
    counter skip the start of every later session.
    """
    assert suggester is not None
    state = suggester.state
    for index, line in enumerate(payload.get("lines") or []):
        text = (line.get("text") or "").strip()
        if index == len(tracked):
            tracked.append(None)
        entry = tracked[index]
        if entry is not None:
            state.update_line(entry, text)
        elif text:
            tracked[index] = state.add_line(text, speaker=line.get("speaker", -1))


def _inject_translations(payload: dict) -> None:
    """Fill each line's `translation` from the Bedrock translator cache, and
    kick off translation for any line not yet translated.

    WLK no longer translates (no target_language), so `lines[].translation` is
    absent; we populate it. Cached Chinese is injected immediately; uncached
    lines get a background translation that shows up on the next WLK update
    (WLK resends the full lines list continuously).
    """
    assert translator is not None
    for line in payload.get("lines") or []:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        zh = translator.get_cached(text)
        if zh:
            line["translation"] = zh
        else:
            # Schedule translation; result lands in cache for a later update.
            asyncio.create_task(translator.ensure(text))


async def _handle_results(websocket: WebSocket, results_generator,
                          session: list[str]) -> None:
    """Forward WLK results to the browser AND tap them into the buffer."""
    tracked: list = []  # this session's lines in the suggester buffer
    try:
        async for response in results_generator:
            payload = response.to_dict()
            transcript_log.update(session, payload)
            _ingest_front_data(payload, tracked)
            _inject_translations(payload)
            await websocket.send_json(payload)
        await websocket.send_json({"type": "ready_to_stop"})
    except WebSocketDisconnect:
        logger.info("ASR results consumer: client disconnected")
    except Exception:  # noqa: BLE001
        logger.exception("Error while handling ASR results")


@app.websocket("/asr")
async def asr_ws(websocket: WebSocket) -> None:
    assert transcription_engine is not None
    audio_processor = AudioProcessor(transcription_engine=transcription_engine)
    await websocket.accept()
    logger.info("ASR WebSocket opened")

    # WLK's frontend waits for this config message to decide how to send audio
    # (AudioWorklet/PCM vs MediaRecorder/WebM). Mirror basic_server.py — without
    # it the recorder never starts streaming and FFmpeg times out.
    try:
        await websocket.send_json(
            {"type": "config", "useAudioWorklet": bool(getattr(
                transcription_engine.config, "pcm_input", False))}
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to send config to ASR client", exc_info=True)

    results_generator = await audio_processor.create_tasks()
    results_task = asyncio.create_task(
        _handle_results(websocket, results_generator, transcript_log.new_session())
    )

    # Debug aid: set C3PO_DUMP_AUDIO=/path/to/dump.webm to save exactly what the
    # browser sends, so a silent/undecodable stream can be inspected offline.
    dump_path = os.getenv("C3PO_DUMP_AUDIO")
    dump = open(dump_path, "wb") if dump_path else None

    try:
        while True:
            message = await websocket.receive_bytes()
            if dump:
                dump.write(message)
                dump.flush()
            await audio_processor.process_audio(message)
    except KeyError as exc:
        if "bytes" in str(exc):
            logger.info("ASR client closed the connection")
        else:
            logger.exception("Unexpected KeyError in ASR loop")
    except WebSocketDisconnect:
        logger.info("ASR WebSocket disconnected")
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error in ASR loop")
    finally:
        if dump:
            dump.close()
            logger.info("Audio dump written to %s", dump_path)
        if not results_task.done():
            results_task.cancel()
            try:
                await results_task
            except asyncio.CancelledError:
                pass
        await audio_processor.cleanup()
        logger.info("ASR WebSocket cleaned up")


def main() -> None:
    """CLI entry point."""
    import uvicorn

    print(f"c3po: {config.url}")
    uvicorn.run(
        "c3po.server:app",
        host=config.host,
        port=config.port,
        lifespan="on",
        log_level="info",
        # Backstop for any handler that still blocks shutdown: cancel it after
        # 5s so the lifespan shutdown (suggester, overlay) always runs.
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
