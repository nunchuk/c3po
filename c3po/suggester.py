"""Background suggestion engine.

Maintains a rolling buffer of recent meeting lines and continuously regenerates
English reply suggestions as the conversation progresses, so a few fresh options
are always ready before the user is suddenly called on.

Refresh strategy (balanced defaults, all tunable in config):
  - debounce: at least `debounce_seconds` between two Bedrock calls
  - new lines: regenerate once `new_lines_trigger` new finalized lines arrive
  - question detection: a line ending in '?' or matching a marker fires immediately
  - fallback timer: regenerate after `fallback_seconds` if there is new content
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from c3po.config import config
from c3po.llm import SuggestionClient

logger = logging.getLogger(__name__)


@dataclass
class _Line:
    """One finalized transcript line (English source text)."""

    text: str
    speaker: int = -1


@dataclass
class SuggestionState:
    """Shared state between the /asr ingestion path and the background task."""

    context: str = ""
    lines: deque[_Line] = field(
        default_factory=lambda: deque(maxlen=config.buffer_max_lines)
    )
    latest_suggestions: list[dict] = field(default_factory=list)
    # Bumps whenever new finalized content arrives; the worker watches it.
    revision: int = 0
    # The revision number last sent to Bedrock.
    last_generated_revision: int = -1
    # Set when a likely question to the user is detected (forces immediate refresh).
    pending_question: str | None = None
    new_lines_since_gen: int = 0
    # Signalled whenever content changes so the worker's wait wakes up to
    # re-check its trigger conditions (otherwise new lines accumulate unseen
    # until the fallback timer elapses).
    content_event: asyncio.Event = field(default_factory=asyncio.Event)

    def add_line(self, text: str, speaker: int = -1) -> None:
        """Append a finalized line and update question/refresh signals."""
        text = text.strip()
        if not text:
            return
        self.lines.append(_Line(text=text, speaker=speaker))
        self.revision += 1
        self.new_lines_since_gen += 1
        if self._looks_like_question(text):
            self.pending_question = text
        # Wake the worker so it can evaluate the new-lines / question triggers.
        self.content_event.set()

    def _looks_like_question(self, text: str) -> bool:
        lowered = text.lower()
        if text.rstrip().endswith("?"):
            return True
        return any(marker in lowered for marker in config.question_markers)

    def recent_transcript(self) -> tuple[str, str]:
        """Split the recent window into (earlier_context, immediate) transcripts.

        The window is the last `context_window_lines` finalized lines. Its
        trailing `immediate_lines` are the moment the reply must address; the
        lines before them are returned separately as background so Haiku follows
        the meeting arc without the short replies drifting to an older topic.
        """
        window = list(self.lines)[-config.context_window_lines :]
        split = max(0, len(window) - config.immediate_lines)
        earlier = "\n".join(line.text for line in window[:split])
        immediate = "\n".join(line.text for line in window[split:])
        return earlier, immediate


class Suggester:
    """Runs the background refresh loop and exposes the latest suggestions."""

    def __init__(self) -> None:
        self.state = SuggestionState()
        self._client = SuggestionClient()
        self._task: asyncio.Task | None = None
        self._force_event = asyncio.Event()
        # asyncio.Queue of suggestion batches, consumed by the /suggestions WS.
        self._subscribers: set[asyncio.Queue] = set()

    # --- lifecycle ---

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # --- public API used by the server ---

    def set_context(self, context: str) -> None:
        self.state.context = context
        self.force_refresh()

    def force_refresh(self) -> None:
        """Request an immediate regeneration (manual hotkey or context change)."""
        self._force_event.set()

    def subscribe(self) -> asyncio.Queue:
        """Register a websocket subscriber; returns a queue of suggestion batches."""
        queue: asyncio.Queue = asyncio.Queue()
        # Push current snapshot immediately so a new client isn't blank.
        if self.state.latest_suggestions:
            queue.put_nowait(self._snapshot())
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # --- internals ---

    def _snapshot(self) -> dict:
        return {
            "type": "suggestions",
            "suggestions": self.state.latest_suggestions,
            "is_question": self.state.pending_question is not None,
        }

    def _broadcast(self) -> None:
        snapshot = self._snapshot()
        for queue in self._subscribers:
            queue.put_nowait(snapshot)

    async def _run(self) -> None:
        """Main refresh loop. Decides when to call Bedrock and broadcasts results."""
        last_gen_time = 0.0
        while True:
            try:
                await self._wait_for_trigger()
            except asyncio.CancelledError:
                raise

            # Debounce: never call Bedrock more often than debounce_seconds.
            elapsed = time.monotonic() - last_gen_time
            if elapsed < config.debounce_seconds:
                await asyncio.sleep(config.debounce_seconds - elapsed)

            if self.state.revision == self.state.last_generated_revision:
                # Nothing new since last generation (e.g. only a forced event with
                # no content change) — still allow forced refresh of context.
                if not self._force_event.is_set():
                    continue

            self._force_event.clear()
            await self._generate()
            last_gen_time = time.monotonic()

    async def _wait_for_trigger(self) -> None:
        """Block until a refresh condition is met.

        Returns when: a question is pending, enough new lines accumulated, the
        force event is set, or the fallback timer elapses with new content.

        Waits on both the force event (manual refresh) and the content event
        (a new line arrived); without the latter, new lines would accumulate
        unseen until the fallback timer elapsed.
        """
        while True:
            if self._force_event.is_set():
                return
            if self.state.pending_question is not None:
                return
            if self.state.new_lines_since_gen >= config.new_lines_trigger:
                return

            woke = await self._wait_any_event(timeout=config.fallback_seconds)
            self.state.content_event.clear()
            if not woke:
                # Fallback timer elapsed: refresh only if there is new content.
                if self.state.revision != self.state.last_generated_revision:
                    return
                # else loop and keep waiting
            # Woke from an event (force or new content): loop to re-check the
            # trigger conditions above.

    async def _wait_any_event(self, timeout: float) -> bool:
        """Wait until the force or content event fires, or the timeout elapses.

        Returns True if an event fired, False on timeout.
        """
        force = asyncio.ensure_future(self._force_event.wait())
        content = asyncio.ensure_future(self.state.content_event.wait())
        try:
            done, pending = await asyncio.wait(
                {force, content},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for fut in (force, content):
                if not fut.done():
                    fut.cancel()
        return bool(done)

    async def _generate(self) -> None:
        """Generate suggestions off the event loop and broadcast incrementally.

        The short replies and the full answer are fired as two concurrent Bedrock
        calls. The short replies are the time-critical path (what the user reaches
        for when suddenly called on), so they are awaited and broadcast first; the
        longer full-answer card merges in when its slower call returns. Each call's
        SDK invocation is blocking, so both run in worker threads.
        """
        revision = self.state.revision
        last_question = self.state.pending_question
        context = self.state.context
        earlier, immediate = self.state.recent_transcript()

        short_task = asyncio.create_task(
            asyncio.to_thread(
                self._client.suggest, context, earlier, immediate, last_question, "short"
            )
        )
        long_task = asyncio.create_task(
            asyncio.to_thread(
                self._client.suggest, context, earlier, immediate, last_question, "long"
            )
        )

        # Record bookkeeping up front so refresh triggers reset for this cycle.
        self.state.last_generated_revision = revision
        self.state.new_lines_since_gen = 0
        # Clear the question flag only after we've generated for it.
        if last_question is not None and self.state.pending_question == last_question:
            self.state.pending_question = None

        # Short replies first — render them the moment they arrive.
        short = await short_task
        if short:
            self.state.latest_suggestions = [
                s for s in short if s.get("kind") != "long"
            ]
        self._broadcast()

        # Full answer merges in when ready, replacing any prior long card and
        # keeping the short cards in place.
        long = await long_task
        if long:
            long_items = [s for s in long if s.get("kind") == "long"] or long
            shorts = [
                s for s in self.state.latest_suggestions if s.get("kind") != "long"
            ]
            self.state.latest_suggestions = shorts + long_items
            self._broadcast()
