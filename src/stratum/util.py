"""Small shared helpers."""

from __future__ import annotations


def format_timestamp(seconds: float) -> str:
    """3:04, or 1:02:33 past an hour. Used in prompts, Markdown and chunk text so
    an agent can cite back into the video."""
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "unknown"
    return format_timestamp(seconds)


def approx_tokens(text: str) -> int:
    """A rough character-based token estimate.

    Deliberately not a real tokeniser: the state budget is a guard rail, and
    pulling in a tokeniser would tie the base install to one provider's
    vocabulary. Four characters per token is close enough to catch runaway state.
    """
    return max(1, len(text) // 4)


def truncate_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + " ..."
