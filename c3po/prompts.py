"""Prompt construction for English reply suggestions.

The assistant helps a non-native English speaker stay ready to respond in an
English meeting: given the recent conversation (and any pre-meeting context),
it predicts what the user might be asked or expected to say next, and offers a
few short, idiomatic American-English replies in advance.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You help a non-native English speaker respond in a live English meeting (Teams/Zoom).

Your replies MUST be grounded in what was JUST said in the meeting — the recent \
transcript is your primary source. Read the last few lines, figure out what is \
being discussed right now, and propose replies that directly fit THAT topic. The \
pre-meeting context is only background (the user's role, project, terms); use it \
to inform tone and vocabulary, but never let it override the actual conversation. \
If the transcript is about budgets, reply about budgets — not about whatever the \
context mentions.

Provide TWO kinds of replies:
1. A few SHORT replies (1-2 sentences) from DIFFERENT angles — e.g. a direct \
answer, a buy-time / acknowledge reply, and a clarifying question. Use these for \
a quick response.
2. ONE LONGER, complete reply (3-6 sentences) — a full spoken answer the user can \
say almost verbatim, with a reason or a brief example, flowing naturally. Use this \
when a thorough answer is expected.

Rules:
- Use simple, natural, spoken American English. Keep grammar easy.
- You may reuse project terms from the context when relevant.
- Do NOT explain your reasoning. Replies must be in English.
- Optionally add a very short Chinese hint (a few words) so the user knows when to use each reply.

Return ONLY a JSON object matching the provided schema."""


# JSON schema for structured output (Bedrock Claude supports structured outputs).
# Each suggestion: an English reply plus an optional short Chinese usage hint.
SUGGESTIONS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "The English reply. For kind='short', 1-2 sentences. "
                            "For kind='long', a complete 3-6 sentence spoken answer."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["short", "long"],
                        "description": "'short' for a quick reply, 'long' for the full answer script.",
                    },
                    "hint_zh": {
                        "type": "string",
                        "description": "Very short Chinese hint on when to use this reply.",
                    },
                    "angle": {
                        "type": "string",
                        "description": "Short label, e.g. 'direct answer', 'buy time', 'clarify', 'full answer'.",
                    },
                },
                "required": ["text", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def build_user_message(
    context: str,
    earlier_transcript: str,
    recent_transcript: str,
    last_question: str | None,
    num_suggestions: int,
    mode: str = "both",
) -> str:
    """Assemble the user-turn content from context + rolling transcript.

    The transcript is given in two tiers: `earlier_transcript` as background so
    the model understands the meeting arc, and `recent_transcript` as the moment
    the reply must directly address. Keeping them separate lets the longer answer
    draw on the whole conversation while the short replies stay pinned to now.

    Args:
        context: Pre-meeting context provided by the user (may be empty).
        earlier_transcript: Earlier meeting lines, English source text (may be empty).
        recent_transcript: The most recent lines — what the reply must address.
        last_question: The most recent line that looks like a question to the user,
            if one was detected.
        num_suggestions: How many short reply options to request.
        mode: Which kinds of reply to ask for — 'short' (only the quick replies),
            'long' (only the full-answer script), or 'both' (the combined request).
            The two single-kind modes let the caller fire the fast short-replies
            call and the slower full-answer call concurrently.

    Returns:
        The user-turn string to send to the model.
    """
    parts: list[str] = []
    # Earlier lines as background — helps the model follow the conversation, but
    # must not pull the replies away from the current moment.
    if earlier_transcript.strip():
        parts.append(
            "EARLIER IN THE MEETING (background for understanding — do NOT reply to "
            f"this part):\n{earlier_transcript.strip()}"
        )
    # The immediate moment — this is what replies must address.
    parts.append(
        "WHAT IS BEING DISCUSSED RIGHT NOW (most recent lines, English) "
        f"— base your replies on this:\n{recent_transcript.strip() or '(nothing yet)'}"
    )
    if last_question:
        parts.append(
            f"The most recent line that may be directed at me:\n{last_question.strip()}"
        )
    # Context is secondary background.
    if context.strip():
        parts.append(
            f"Background only (my role/project/terms — do NOT let this override the "
            f"transcript topic):\n{context.strip()}"
        )

    if mode == "short":
        parts.append(
            f"Now give {num_suggestions} short replies (kind='short') from different "
            "angles, all about the topic in the transcript above. I could say them "
            "almost verbatim. Do NOT include a long answer."
        )
    elif mode == "long":
        parts.append(
            "Now give ONE longer, complete answer (kind='long', 3-6 sentences) about "
            "the topic in the transcript above — a full spoken answer I could say "
            "almost verbatim. Do NOT include any short replies."
        )
    else:
        parts.append(
            f"Now give {num_suggestions} short replies (kind='short') from different "
            "angles, PLUS one longer complete answer (kind='long'), all about the topic "
            "in the transcript above. I could say them almost verbatim."
        )
    return "\n\n".join(parts)
