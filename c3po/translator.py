"""English->Chinese translation via Amazon Bedrock (Claude Haiku 4.5).

Replaces WhisperLiveKit's built-in NLLW translation. NLLW couples translation to
transcript segmentation by timestamp overlap, so finer line-splitting broke its
output. Translating each finalized English sentence independently on Bedrock
decouples the two: transcription/segmentation and translation no longer fight,
the Chinese comes out complete and more fluent, and NLLB's ~1.5GB is freed.

A per-line cache avoids re-translating the same text as WLK resends the growing
`lines` list on every update. Translation runs in the background; the transcript
is forwarded to the browser immediately and the Chinese is filled in as it lands.
"""

from __future__ import annotations

import asyncio
import logging

import boto3

from c3po.config import config

logger = logging.getLogger("c3po")

_SYSTEM = (
    "You are a real-time meeting translator. Translate the English meeting "
    "transcript into natural, fluent Simplified Chinese. Output ONLY the Chinese "
    "translation — no explanations, no pinyin, no English."
)


class Translator:
    """Translates finalized English lines to Chinese, with caching.

    Uses the Bedrock Converse API (boto3) rather than the Anthropic SDK so that
    Amazon Nova models are usable here — Nova is measurably faster than Claude
    for this short-translation workload (see config.translate_model_id).
    """

    def __init__(self) -> None:
        session = (
            boto3.Session(profile_name=config.aws_profile)
            if config.aws_profile
            else boto3.Session()
        )
        self._client = session.client(
            "bedrock-runtime", region_name=config.aws_region
        )
        self._system = [{"text": _SYSTEM}]
        # text -> Chinese translation. While streaming, this holds a growing
        # partial; `_done` marks which keys are complete.
        self._cache: dict[str, str] = {}
        self._done: set[str] = set()
        self._inflight: set[str] = set()

    def get_cached(self, text: str) -> str | None:
        """Return the cached translation for `text`, or None if not translated yet."""
        return self._cache.get(text.strip())

    def _finalize(self, text: str, zh: str) -> None:
        """Mark a translation complete and append it to the subtitle file.

        `_done` membership is the single guard against duplicate writes: WLK
        resends the whole `lines` list continuously, so without it the same
        sentence would be appended on every update.
        """
        self._done.add(text)
        if not config.subtitle_file:
            return
        try:
            with open(config.subtitle_file, "a", encoding="utf-8") as fh:
                # Tab-separated so the overlay can split english/chinese. Any
                # tab or newline inside the text itself would corrupt the
                # record, so collapse them to spaces.
                en = " ".join(text.split())
                zh_clean = " ".join(zh.split())
                fh.write(f"{en}\t{zh_clean}\n")
        except OSError:
            logger.warning("Failed to append to subtitle file", exc_info=True)

    async def ensure(self, text: str) -> None:
        """Kick off translation for `text` if not already done/in-flight."""
        key = text.strip()
        # Note: a key present in `_cache` but not in `_done` is a streaming
        # partial whose call is still in flight — `_inflight` already covers it.
        if not key or key in self._done or key in self._inflight:
            return
        self._inflight.add(key)
        try:
            if config.translate_streaming:
                # Partial Chinese is written into the cache as it arrives, so the
                # next WLK update already shows a growing translation instead of
                # waiting for the whole sentence.
                await asyncio.to_thread(self._translate_streaming, key)
            else:
                zh = await asyncio.to_thread(self._translate, key)
                if zh:
                    self._cache[key] = zh
                    self._finalize(key, zh)
        finally:
            self._inflight.discard(key)

    def _translate_streaming(self, text: str) -> None:
        """Blocking streaming Bedrock call — run via asyncio.to_thread.

        Writes progressively longer partials into the cache. The cache dict is
        only ever assigned whole strings, so a concurrent reader either sees the
        previous partial or the newer one, never a torn value.
        """
        try:
            response = self._client.converse_stream(
                modelId=config.translate_model_id,
                system=self._system,
                messages=[{"role": "user", "content": [{"text": text}]}],
                inferenceConfig={"maxTokens": config.max_tokens},
            )
            acc = ""
            for event in response["stream"]:
                delta = event.get("contentBlockDelta")
                if not delta:
                    continue
                acc += delta["delta"].get("text", "")
                stripped = acc.strip()
                if stripped:
                    self._cache[text] = stripped
            final = self._cache.get(text)
            if final:
                self._finalize(text, final)
        except Exception:  # noqa: BLE001 - never crash the transcript path
            logger.exception("Bedrock streaming translation failed")
            # Drop the partial so the line is retried on a later WLK update.
            self._cache.pop(text, None)

    def _translate(self, text: str) -> str:
        """Blocking Bedrock call — run via asyncio.to_thread."""
        try:
            response = self._client.converse(
                modelId=config.translate_model_id,
                system=self._system,
                messages=[{"role": "user", "content": [{"text": text}]}],
                inferenceConfig={"maxTokens": config.max_tokens},
            )
        except Exception:  # noqa: BLE001 - never crash the transcript path
            logger.exception("Bedrock translation failed")
            return ""
        blocks = response["output"]["message"]["content"]
        return "".join(b.get("text", "") for b in blocks).strip()
