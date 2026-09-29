"""Feed a WebM audio file to a WhisperLiveKit /asr WebSocket, bypassing the browser.

Mimics the browser's MediaRecorder: connects, waits for the config message, then
streams the file in ~200ms-ish byte chunks, and prints any transcription/translation
the server sends back.

Usage:
    python3 scripts/feed_asr.py /tmp/c3po_test.webm ws://127.0.0.1:8001/asr
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets


async def main(path: str, url: str) -> int:
    data = open(path, "rb").read()
    print(f"feeding {len(data)} bytes from {path} to {url}", flush=True)

    async with websockets.connect(url, max_size=None) as ws:
        # Reader: print everything the server sends back.
        async def reader() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "config":
                        print(f"[config] {msg}", flush=True)
                        continue
                    if msg.get("type") == "ready_to_stop":
                        print("[ready_to_stop]", flush=True)
                        return
                    lines = msg.get("lines") or []
                    for ln in lines:
                        t = (ln.get("text") or "").strip()
                        tr = ln.get("translation")
                        if t or tr:
                            print(f"  LINE: {t!r}  | translation={tr!r}", flush=True)
                    bt = msg.get("buffer_transcription") or ""
                    if bt.strip():
                        print(f"  buffer: {bt!r}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[reader stopped] {exc}", flush=True)

        reader_task = asyncio.create_task(reader())

        # Stream in chunks, paced roughly like real-time so VAD/streaming behaves.
        chunk = 4096
        for i in range(0, len(data), chunk):
            await ws.send(data[i : i + chunk])
            await asyncio.sleep(0.05)

        # Give the server time to finish processing the tail.
        await asyncio.sleep(8)
        reader_task.cancel()
    return 0


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "/tmp/c3po_test.webm"
    endpoint = sys.argv[2] if len(sys.argv) > 2 else "ws://127.0.0.1:8001/asr"
    sys.exit(asyncio.run(main(audio, endpoint)))
