"""Break Whisper repetition loops in SimulStreaming.

SimulStreaming feeds its own output back into the decoder: the words already
decoded for the 20s audio window, plus older text as a prompt. Once Whisper
hallucinates a short phrase on degraded audio (a Zoom network stall produced
" No."), that phrase dominates its own prompt and every later decode repeats it,
however clear the speech that follows. WLK only resets the decoder after >=5s of
silence, which a live meeting never provides, so the transcript stays stuck on
"No. No. No." for as long as people keep talking.

This monkey-patches `SimulStreamingOnlineProcessor.process_iter` to watch the
recent output and, when it collapses to one or two distinct words, drop that
batch and reset the decoder the same way WLK's long-silence path does. No WLK
source edit; drop-in across upgrades.
"""

from __future__ import annotations

import logging
import re
from collections import deque

from c3po.config import config

logger = logging.getLogger("c3po")

# A loop is this many consecutive output words with at most _MAX_DISTINCT
# distinct values -- "No." x12, or "Thank you." x6, another common Whisper loop.
_WINDOW_WORDS = config.loop_guard_words
_MAX_DISTINCT = 2


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation, so " No." and "no," compare equal."""
    return re.sub(r"[^\w']", "", text.lower())


def apply() -> bool:
    """Patch SimulStreamingOnlineProcessor.process_iter to break repetition loops.

    Returns True if applied. Safe to call once at startup.
    """
    if _WINDOW_WORDS <= 0:
        return False
    try:
        from whisperlivekit.simul_whisper.backend import SimulStreamingOnlineProcessor
    except Exception:  # noqa: BLE001
        return False

    if getattr(SimulStreamingOnlineProcessor, "_c3po_loop_guard_patched", False):
        return False

    original = SimulStreamingOnlineProcessor.process_iter

    def process_iter_guarded(self, is_last=False):
        tokens, processed_upto = original(self, is_last=is_last)

        recent = getattr(self, "_c3po_recent_words", None)
        if recent is None:
            recent = self._c3po_recent_words = deque(maxlen=_WINDOW_WORDS)
        for token in tokens:
            word = _normalize(token.text)
            if word:
                recent.append(word)

        if len(recent) == _WINDOW_WORDS and len(set(recent)) <= _MAX_DISTINCT:
            logger.warning(
                "Repetition loop detected (last %d words: %s); resetting the decoder",
                _WINDOW_WORDS, " ".join(sorted(set(recent))),
            )
            recent.clear()
            # Same reset as WLK's long-silence path: drop the audio window, the
            # decoded words and the prompt. Audio after this point restarts at
            # zero inside the decoder, so shift its timestamps to the current
            # stream position or they would jump back to the session start.
            self.model.refresh_segment(complete=True)
            self.model.global_time_offset = self.end
            return [], processed_upto
        return tokens, processed_upto

    SimulStreamingOnlineProcessor.process_iter = process_iter_guarded
    SimulStreamingOnlineProcessor._c3po_loop_guard_patched = True
    logger.info("Applied repetition-loop guard to SimulStreamingOnlineProcessor.process_iter")
    return True
