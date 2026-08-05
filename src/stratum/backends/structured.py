"""Schema enforcement with a repair retry.

Shared by every real adapter so the retry policy is written once. Assumption 4 in
07_OPEN_QUESTIONS.md is that frontier VLMs reliably produce schema-conformant
output across eighty sequential calls; it is mostly true, and "mostly" is why
this file exists.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from stratum.errors import SchemaViolation

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may be wrapped in prose or a
    code fence. Models do this even when told not to."""
    stripped = text.strip()
    fenced = _FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def parse(model: type[T], text: str) -> T:
    return model.model_validate_json(extract_json(text))


def repair_instruction(model: type[T], error: Exception) -> str:
    """What to send back when the first attempt did not parse.

    The schema is repeated rather than referenced, because the failing model has
    just demonstrated that it did not follow the one it was given.
    """
    return (
        "Your previous response did not parse against the required schema.\n\n"
        f"Error:\n{error}\n\n"
        "Return ONLY a single JSON object conforming to this JSON Schema. "
        "No prose, no code fence, no commentary.\n\n"
        f"{json.dumps(model.model_json_schema(), indent=2)}"
    )


def parse_with_repair(
    model: type[T],
    *,
    first: Callable[[], str],
    repair: Callable[[str], str],
    max_attempts: int = 3,
) -> tuple[T, int, str]:
    """Call `first`, then `repair` up to `max_attempts - 1` times.

    Returns `(parsed, attempts, raw_text)`. Raises `SchemaViolation` when every
    attempt failed — the caller writes `status: failed` for that keyframe and the
    run continues. One bad keyframe must not destroy a run.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    raw = first()
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return parse(model, raw), attempt, raw
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            raw = repair(repair_instruction(model, exc))

    raise SchemaViolation(
        f"{model.__name__} did not parse after {max_attempts} attempts: {last_error}"
    )
