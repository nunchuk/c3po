"""Central configuration for c3po. All tunables live here.

Values can be overridden via environment variables (loaded from .env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    """Runtime configuration, populated from environment with sane defaults."""

    # --- Server ---
    # Bind address stays numeric: passing "localhost" makes uvicorn resolve the
    # name and it may bind IPv6 ::1 only, which breaks IPv4 clients.
    host: str = os.getenv("C3PO_HOST", "127.0.0.1")
    port: int = int(os.getenv("C3PO_PORT", "8989"))
    # Hostname shown in the URL / opened in the browser.
    display_host: str = os.getenv("C3PO_DISPLAY_HOST", "localhost")

    @property
    def url(self) -> str:
        """Base URL to show the user and open in the browser."""
        return f"http://{self.display_host}:{self.port}"

    # --- WhisperLiveKit (speech-to-text + EN->ZH translation, fully local) ---
    # Whisper model size; "small" balances speed/accuracy on Apple M4. Drop to
    # "base" if CPU/memory is tight.
    # "base.en" (English-only) keeps streaming latency low on CPU (~2.5s avg on
    # M4) and is more accurate than multilingual "base" for English. "small" lags
    # ~12s real-time. Bump to "small"/"small.en" only if accuracy matters more.
    whisper_model: str = os.getenv("C3PO_WHISPER_MODEL", "base.en")
    source_language: str = os.getenv("C3PO_SOURCE_LANG", "en")
    # NLLB target language code for Simplified Chinese.
    target_language: str = os.getenv("C3PO_TARGET_LANG", "zho_Hans")
    # Speaker diarization (who said what). OFF by default: it needs a heavy extra
    # dependency (NVIDIA NeMo for Sortformer, or diart+pyannote with an HF token)
    # and adds latency. Enable with C3PO_DIARIZATION=1 after installing a backend.
    diarization: bool = os.getenv("C3PO_DIARIZATION", "0") == "1"

    # How long (seconds) a transcript segment can grow before it's force-trimmed
    # into a new line. WLK's default is 15s, which makes one giant unreadable
    # block and stops NLLW from finalizing the translation. Lower = more frequent
    # line breaks AND more frequent translation finalization.
    buffer_trimming_sec: float = float(os.getenv("C3PO_BUFFER_TRIMMING_SEC", "6"))

    # Don't break a line at punctuation until it has at least this many tokens —
    # prevents tiny fragments ("As a.", "I would.", "Yeah.") from each becoming
    # their own segment (and their own Bedrock translation call).
    min_tokens_per_line: int = int(os.getenv("C3PO_MIN_TOKENS_PER_LINE", "8"))

    # Force a transcript line break after this many tokens even without
    # punctuation (length fallback for run-on speech). ~one long sentence.
    max_tokens_per_line: int = int(os.getenv("C3PO_MAX_TOKENS_PER_LINE", "30"))
    # faster-whisper (CTranslate2): the only backend where NLLW translation
    # works. The MLX backend transcribes fast but its SimulStreaming token
    # format breaks NLLW (no Chinese output), and it also needs the
    # mlx_thread_fix GPU-stream patch. Since EN->ZH translation is a core
    # requirement, faster-whisper is the default; it's plenty fast on M4
    # (~1.25s latency, comfortably real-time). Set C3PO_BACKEND=mlx-whisper
    # only if you want max transcription speed and DON'T need translation.
    backend: str = os.getenv("C3PO_BACKEND", "faster-whisper")

    # Corporate networks (Amazon) intercept SSL, so urllib model downloads fail
    # with CERTIFICATE_VERIFY_FAILED. The system bundle has the corp CA.
    ssl_cert_file: str = os.getenv("C3PO_SSL_CERT_FILE", "/etc/ssl/cert.pem")

    # --- Amazon Bedrock (English reply suggestions) ---
    # Claude Haiku 4.5, pinned to Tokyo via the "jp." regional inference profile.
    # The "global." id routes dynamically and was measured landing in us-west-2
    # (1076ms median) instead of Tokyo; "jp." keeps it in-region at 866ms for the
    # same model and identical quality.
    bedrock_model_id: str = os.getenv(
        "C3PO_BEDROCK_MODEL_ID",
        "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
    # Bedrock region. C3PO_AWS_REGION takes precedence over the ambient
    # AWS_REGION: a shell-level AWS_REGION (e.g. us-west-2) silently wins over
    # .env because load_dotenv() does not override existing env vars, which
    # previously pinned Bedrock to us-west-2 (1076ms) instead of Tokyo (866ms).
    aws_region: str = os.getenv(
        "C3PO_AWS_REGION", os.getenv("AWS_REGION", "ap-northeast-1")
    )

    # AWS profile for the boto3 (Converse API) translation client. Reply
    # suggestions still authenticate via AWS_BEARER_TOKEN_BEDROCK, but boto3
    # needs real credentials. Set C3PO_AWS_PROFILE when the ambient AWS_PROFILE
    # is unusable.
    aws_profile: str | None = os.getenv("C3PO_AWS_PROFILE") or None
    max_tokens: int = int(os.getenv("C3PO_MAX_TOKENS", "400"))

    # Subtitle translation model. Kept separate from the suggestion model: this
    # workload is latency-critical and short, suggestions need reliable
    # structured output.
    #
    # Measured streaming time-to-first-token in Tokyo, translating real meeting
    # sentences; TTFT is what the user perceives for subtitles:
    #   nova-micro     330ms  - REJECTED: transliterates product names
    #                           (spelled out in Chinese characters, or translated literally)
    #   nova-2-lite    411ms  - chosen: keeps them intact
    #   nova-lite      472ms  - same transliteration problem as micro
    #   claude-haiku   711ms  - best quality, 300ms slower
    # Set C3PO_TRANSLATE_MODEL_ID to the Haiku id if translation quality on a
    # specific meeting's jargon matters more than latency.
    translate_model_id: str = os.getenv(
        "C3PO_TRANSLATE_MODEL_ID",
        "jp.amazon.nova-2-lite-v1:0",
    )

    # Stream the translation so Chinese appears token-by-token instead of only
    # after the full sentence completes. Measured time-to-first-token is roughly
    # half the full-completion latency, so this halves perceived subtitle delay.
    translate_streaming: bool = os.getenv("C3PO_TRANSLATE_STREAMING", "1") == "1"

    # --- Floating subtitle overlay (native macOS window) ---
    # Finalized "english<TAB>chinese" pairs are appended here; the overlay tails
    # the file. Only completed translations are written, never streaming
    # partials, or the overlay would flicker through half sentences.
    subtitle_file: str = os.getenv("C3PO_SUBTITLE_FILE", "/tmp/c3po-subtitles.txt")

    # Launch the overlay as a child process alongside the server.
    overlay_enabled: bool = os.getenv("C3PO_OVERLAY", "1") == "1"

    # Presence of this file means "overlay visible". The web button creates and
    # removes it; the overlay polls it on its existing refresh timer. A flag
    # file is used rather than signals because POSIX signal handlers interact
    # badly with the PyObjC run loop.
    overlay_visible_flag: str = os.path.expanduser(
        "~/Library/Application Support/meeting-translator/overlay-visible"
    )

    # The suggestion call is split into a fast short-replies call and a slower
    # full-answer call so the time-critical short cards render first (see
    # Suggester._generate). Separate, tighter token budgets keep each call as
    # small as possible; `max_tokens` remains the fallback for a combined call.
    short_max_tokens: int = int(os.getenv("C3PO_SHORT_MAX_TOKENS", "220"))
    long_max_tokens: int = int(os.getenv("C3PO_LONG_MAX_TOKENS", "320"))

    # --- Rolling meeting buffer ---
    # How many recent transcript lines to keep as context for suggestions.
    buffer_max_lines: int = int(os.getenv("C3PO_BUFFER_MAX_LINES", "40"))
    # How many recent lines to actually send to Bedrock per suggestion call.
    # These are split into two tiers in the prompt (see build_user_message): the
    # trailing `immediate_lines` are the "reply to THIS" moment, and the lines
    # before them are sent as background so Haiku understands the meeting arc
    # without the short replies drifting to an older topic.
    context_window_lines: int = int(os.getenv("C3PO_CONTEXT_WINDOW_LINES", "24"))
    # Of the window, how many trailing lines are the immediate moment the reply
    # must address. The remaining lines are framed as background context.
    immediate_lines: int = int(os.getenv("C3PO_IMMEDIATE_LINES", "6"))

    # --- Background suggestion refresh strategy (balanced defaults) ---
    # Minimum seconds between two Bedrock calls (debounce).
    debounce_seconds: float = float(os.getenv("C3PO_DEBOUNCE_SECONDS", "8"))
    # Fallback timer: regenerate after this many seconds if there is new content.
    fallback_seconds: float = float(os.getenv("C3PO_FALLBACK_SECONDS", "20"))
    # Regenerate once this many new finalized lines have accumulated.
    new_lines_trigger: int = int(os.getenv("C3PO_NEW_LINES_TRIGGER", "2"))
    # Number of reply options to request.
    num_suggestions: int = int(os.getenv("C3PO_NUM_SUGGESTIONS", "3"))

    # Substrings that flag the meeting may be turning to the user (case-insensitive).
    # The user's name(s) can be added here at runtime via the context panel.
    question_markers: tuple[str, ...] = field(
        default_factory=lambda: (
            "what do you think",
            "any thoughts",
            "your opinion",
            "do you have",
            "would you",
            "can you",
            "what about you",
            "over to you",
        )
    )


config = Config()
