"""Split transcript lines on punctuation, not just on long silences.

WhisperLiveKit only starts a new transcript line when it sees an explicit
Silence token (>5s of silence) UNLESS diarization is enabled — in the
non-diarization path (`TokensAlignment.get_lines`) punctuation is ignored, so a
continuous 25s utterance becomes one giant unreadable block. That also stops
NLLW from finalizing the translation (it finalizes per segment boundary), so the
Chinese ends up partial.

We run diarization OFF (its deps are heavy), so this monkey-patches
`TokensAlignment.get_lines` to ALSO break a line after any token that ends a
sentence (. ! ? 。！？). No WLK source edit; drop-in across upgrades; only active
when diarization is off.
"""

from __future__ import annotations

import logging

from c3po.config import config

logger = logging.getLogger("c3po")

# Don't break on punctuation until the line has at least this many tokens —
# avoids tiny fragment segments like "As a." / "I would." that Whisper produces.
_MIN_TOKENS_PER_LINE = config.min_tokens_per_line

# Length ceiling: force a line break after this many tokens even without
# punctuation, so run-on speech doesn't become one giant line.
_MAX_TOKENS_PER_LINE = config.max_tokens_per_line


def apply() -> bool:
    """Patch TokensAlignment.get_lines to split on sentence punctuation.

    Returns True if applied. Safe to call once at startup.
    """
    try:
        from whisperlivekit.tokens_alignment import TokensAlignment
        from whisperlivekit.timed_objects import Segment, SilentSegment, Silence
        from time import time
    except Exception:  # noqa: BLE001
        return False

    if getattr(TokensAlignment, "_c3po_segment_patched", False):
        return False

    def get_lines_punctuated(self, diarization=False, translation=False, current_silence=None):
        # Diarization path is unchanged — it already segments on punctuation.
        if diarization:
            segments, diarization_buffer = self.get_lines_diarization()
        else:
            diarization_buffer = ""
            for token in self.new_tokens:
                if isinstance(token, Silence):
                    if self.current_line_tokens:
                        self.validated_segments.append(Segment.from_tokens(self.current_line_tokens))
                        self.current_line_tokens = []
                    end_silence = token.end if token.has_ended else time() - self.beg_loop
                    if self.validated_segments and self.validated_segments[-1].is_silence():
                        self.validated_segments[-1].end = end_silence
                    else:
                        self.validated_segments.append(SilentSegment(start=token.start, end=end_silence))
                else:
                    self.current_line_tokens.append(token)
                    # c3po segmentation: break the line at sentence-ending
                    # punctuation, but ONLY once the line is long enough — Whisper
                    # sprinkles periods onto fragments ("As a." / "I would.") which
                    # otherwise become tiny unreadable segments (and a wasted
                    # Bedrock call each). Below the floor, keep accumulating so the
                    # fragment merges into the next clause. A token-count ceiling
                    # still force-breaks genuine run-on speech with no punctuation.
                    n = len(self.current_line_tokens)
                    has_punc = (
                        hasattr(token, "has_punctuation") and token.has_punctuation()
                    )
                    if (has_punc and n >= _MIN_TOKENS_PER_LINE) or n >= _MAX_TOKENS_PER_LINE:
                        self.validated_segments.append(Segment.from_tokens(self.current_line_tokens))
                        self.current_line_tokens = []

            segments = list(self.validated_segments)
            if self.current_line_tokens:
                segments.append(Segment.from_tokens(self.current_line_tokens))

        if current_silence:
            end_silence = current_silence.end if current_silence.has_ended else time() - self.beg_loop
            if segments and segments[-1].is_silence():
                segments[-1] = SilentSegment(start=segments[-1].start, end=end_silence)
            else:
                segments.append(SilentSegment(start=current_silence.start, end=end_silence))
        if translation:
            [self.add_translation(segment) for segment in segments if not segment.is_silence()]
        return segments, diarization_buffer, self.new_translation_buffer.text

    TokensAlignment.get_lines = get_lines_punctuated
    TokensAlignment._c3po_segment_patched = True
    logger.info("Applied punctuation-based segmentation patch to TokensAlignment.get_lines")
    return True
