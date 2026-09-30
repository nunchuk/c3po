"""Prompt construction for English reply suggestions.

The assistant helps a non-native English speaker stay ready to respond in an
English meeting: given what the other participants just said, plus who the user
is and what the meeting is about, it drafts a quick reply and a fuller answer,
each followed by a Chinese translation so the user can judge it at a glance.

Prompt caching: the system prompt holds everything that stays fixed for a
meeting (instructions, About me, Meeting background) and carries the cache
breakpoint; the transcript goes in the user turn after it. Claude Haiku 4.5 only
caches a prefix of at least 4096 tokens -- shorter ones are silently not cached
(measured on Bedrock: a 370-token prefix never cached, a 4938-token one was read
from cache on the second call).
"""

from __future__ import annotations

import re

_INSTRUCTIONS = """You help me, a non-native English speaker, reply out loud in a live English meeting (Teams/Zoom).

The transcript contains only what the OTHER participants said; my own voice is \
not captured. Base every reply on what was JUST said -- the transcript is the \
primary source. Use ABOUT ME and MEETING BACKGROUND to make replies concrete \
(my role, my projects, names, dates, numbers), but never let them override what \
is actually being discussed.

Never invent facts. The ONLY things you know are what is written in the \
transcript, ABOUT ME and MEETING BACKGROUND -- you do not know my progress, what \
is done, dates, numbers or names beyond that. I will say these replies out loud, \
so a made-up detail becomes a false statement in front of my team. Wherever a \
reply needs something you don't know, write a short placeholder in square \
brackets for me to fill in.
  Bad:  "The API work is done and QA starts Monday."  (nobody said that)
  Good: "[What's done] is done, and [next step] starts [date]."

When asked for the QUICK reply: 1-2 short sentences I can say right away -- a \
direct answer if the facts are known, otherwise the headline with placeholders \
(e.g. "Overall we're [on track / behind]; the main open item is [item].") or a \
natural way to acknowledge and buy a moment.
When asked for the FULL answer: one paragraph of 3-6 sentences I could read out \
almost verbatim. Build it only from what you know. For a status update or facts \
you don't have, give me the structure with placeholders instead of guessing, \
e.g. "We've finished [milestone] and we're now on [current step]. The main risk \
is [risk]; I'll [next step] by [date]." -- so I only fill in the blanks.

Style: simple, natural, spoken American English; easy grammar; no filler.

Output format: the English reply as a single paragraph; then a new line that \
starts with 中文： followed by its Simplified Chinese translation. Nothing else \
-- no labels, no quotes, no explanation. For example:
Sure -- overall we're [on track], and I'll share the details by [date].
中文：当然——总体上我们[进展顺利]，我会在[日期]前分享细节。"""


def build_system_prompt(me: str, meeting: str) -> str:
    """The fixed part of every suggestion request for this meeting.

    Kept byte-identical between calls so the prefix can be cached: nothing
    per-request (time, transcript) may go in here.
    """
    return (
        f"{_INSTRUCTIONS}\n\n"
        f"ABOUT ME:\n{me.strip() or '(not provided)'}\n\n"
        f"MEETING BACKGROUND:\n{meeting.strip() or '(not provided)'}"
    )


def build_user_message(
    earlier_transcript: str,
    recent_transcript: str,
    last_question: str | None,
    kind: str,
) -> str:
    """Assemble the per-request user turn.

    The transcript is given in two tiers: `earlier_transcript` as background so
    the model follows the meeting arc, and `recent_transcript` as the moment the
    reply must directly address.

    Args:
        earlier_transcript: Earlier meeting lines, English (may be empty).
        recent_transcript: The most recent lines -- what the reply must address.
        last_question: The latest line that looks like a question, if any.
        kind: 'quick' or 'full'.
    """
    parts: list[str] = []
    if earlier_transcript.strip():
        parts.append(
            "EARLIER IN THE MEETING (background -- do NOT reply to this part):\n"
            f"{earlier_transcript.strip()}"
        )
    parts.append(
        "WHAT WAS JUST SAID (reply to this):\n"
        f"{recent_transcript.strip() or '(nothing yet)'}"
    )
    if last_question:
        parts.append(f"The latest line that may be directed at me:\n{last_question.strip()}")
    parts.append("Give the QUICK reply." if kind == "quick" else "Give the FULL answer.")
    return "\n\n".join(parts)


_MARKERS = ("中文：", "中文:")
# A line that is not part of the reply: an echoed format description
# ("<the English reply ...>") or a bare label ("English:", "Line 1:").
_NOT_REPLY = re.compile(r"^(<.*|(english|en|line \d+)\s*[:：]?)$", re.IGNORECASE)
_LABEL = re.compile(r"^(english|en)\s*[:：]\s*", re.IGNORECASE)


def split_reply(text: str) -> tuple[str, str]:
    """Split a (possibly still streaming) reply into (english, chinese).

    The model writes the English, then a line starting with 中文：. Splits on
    that marker, and tolerates the ways a reply drifts from the format:
    an echoed format line or label is dropped, and a marker that is still
    streaming in ("...\\n中") is kept out of the English.
    """
    for marker in _MARKERS:
        head, found, tail = text.partition(marker)
        if found:
            return _english(head), tail.strip()
    head = text.rstrip()
    for partial in ("中文", "中"):
        if head.endswith("\n" + partial) or head == partial:
            head = head[: -len(partial)]
            break
    return _english(head), ""


def _english(head: str) -> str:
    lines = [line.strip() for line in head.strip().splitlines()]
    kept = [_LABEL.sub("", line) for line in lines if line and not _NOT_REPLY.match(line)]
    return " ".join(kept)
