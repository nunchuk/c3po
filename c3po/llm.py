"""Amazon Bedrock client wrapper for generating English reply suggestions.

Uses Claude Haiku 4.5 via the Anthropic SDK's AnthropicBedrock client, streamed
so the first words of a reply reach the panel within about a second.
Authentication resolves from AWS_BEARER_TOKEN_BEDROCK in the environment;
region from config.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from anthropic import AnthropicBedrock

from c3po.config import config
from c3po.prompts import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)


class SuggestionClient:
    """Streams one English reply (with its Chinese translation) per call."""

    def __init__(self) -> None:
        # api_key resolves from AWS_BEARER_TOKEN_BEDROCK in the environment.
        self._client = AnthropicBedrock(aws_region=config.aws_region)

    def stream_reply(
        self,
        kind: str,
        me: str,
        meeting: str,
        earlier_transcript: str,
        recent_transcript: str,
        last_question: str | None,
        on_text: Callable[[str], None],
    ) -> str:
        """Stream a 'quick' or 'full' reply, calling `on_text` with the text so far.

        Blocking -- run it in a worker thread. Returns the complete text, or ""
        on failure (the caller keeps the previous card visible).
        """
        # Explicit breakpoint: the legacy Bedrock integration rejects top-level
        # automatic caching. It is a no-op until the prefix reaches Haiku 4.5's
        # 4096-token minimum (see prompts.py).
        system = [{
            "type": "text",
            "text": build_system_prompt(me, meeting),
            "cache_control": {"type": "ephemeral"},
        }]
        user = build_user_message(earlier_transcript, recent_transcript, last_question, kind)
        max_tokens = config.short_max_tokens if kind == "quick" else config.long_max_tokens

        started = time.perf_counter()
        first = None
        text = ""
        try:
            with self._client.messages.stream(
                model=config.bedrock_model_id,
                max_tokens=max_tokens,
                # Low temperature: at the default 1.0 the quick reply sometimes
                # filled in progress nobody had mentioned.
                temperature=config.suggestion_temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for chunk in stream.text_stream:
                    if first is None:
                        first = time.perf_counter() - started
                    text += chunk
                    on_text(text)
                usage = stream.get_final_message().usage
        except Exception:  # noqa: BLE001 - network/model errors must not crash the loop
            logger.exception("Bedrock suggestion stream failed (kind=%s)", kind)
            return ""
        logger.info(
            "suggestion %s: first %.0fms, total %.0fms, input %d, cache read %d, cache write %d",
            kind, (first or 0) * 1000, (time.perf_counter() - started) * 1000,
            usage.input_tokens, usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0,
        )
        return text
