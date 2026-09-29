"""Amazon Bedrock client wrapper for generating English reply suggestions.

Uses Claude Haiku 4.5 via the Anthropic SDK's AnthropicBedrock client with
structured output (verified working on Bedrock). Authentication resolves from
AWS_BEARER_TOKEN_BEDROCK in the environment; region from config.
"""

from __future__ import annotations

import json
import logging

from anthropic import AnthropicBedrock

from c3po.config import config
from c3po.prompts import SUGGESTIONS_SCHEMA, SYSTEM_PROMPT, build_user_message

logger = logging.getLogger(__name__)


class SuggestionClient:
    """Generates short English reply suggestions from meeting context."""

    def __init__(self) -> None:
        # api_key resolves from AWS_BEARER_TOKEN_BEDROCK in the environment.
        self._client = AnthropicBedrock(aws_region=config.aws_region)

    def suggest(
        self,
        context: str,
        earlier_transcript: str,
        recent_transcript: str,
        last_question: str | None,
        mode: str = "both",
    ) -> list[dict]:
        """Return a list of reply suggestions.

        Each item is a dict with keys: 'text', optional 'hint_zh', optional 'angle'.
        Returns an empty list on failure (caller keeps the previous batch visible).

        Args:
            earlier_transcript: Earlier meeting lines, sent as background so the
                model follows the conversation arc.
            recent_transcript: The most recent lines — the moment the reply must
                directly address.
            mode: 'short' for the quick replies only, 'long' for the full-answer
                script only, or 'both' for a single combined call. The Suggester
                fires 'short' and 'long' concurrently so the time-critical short
                cards render before the longer answer is finished.
        """
        user_message = build_user_message(
            context=context,
            earlier_transcript=earlier_transcript,
            recent_transcript=recent_transcript,
            last_question=last_question,
            num_suggestions=config.num_suggestions,
            mode=mode,
        )
        max_tokens = {
            "short": config.short_max_tokens,
            "long": config.long_max_tokens,
        }.get(mode, config.max_tokens)
        try:
            message = self._client.messages.create(
                model=config.bedrock_model_id,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                output_config={
                    "format": {"type": "json_schema", "schema": SUGGESTIONS_SCHEMA}
                },
            )
        except Exception:  # noqa: BLE001 - network/model errors must not crash the loop
            logger.exception("Bedrock suggestion request failed (mode=%s)", mode)
            return []

        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            data = json.loads(text)
            suggestions = data.get("suggestions", [])
            # Keep only well-formed entries.
            return [s for s in suggestions if isinstance(s, dict) and s.get("text")]
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Could not parse suggestion JSON (mode=%s): %r", mode, text[:200])
            return []
