"""Primitives shared by every part of the output schema.

The single most important type in this file is `Modality`. Provenance on every
claim is the product (CLAUDE.md, non-negotiable 1). If a code path can produce
an assertion without a modality tag, that path is a bug.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
"""0-1, from the model. Ordinal, not calibrated. Rank with it; do not do arithmetic on it."""


class Modality(str, Enum):
    """Where a claim came from. Exactly four values, no others.

    `stated` was added to the three in 03_OUTPUT_SCHEMA.md when D3
    (07_OPEN_QUESTIONS.md) was decided on 4 Aug 2026. Author-written caption text
    arriving via `SidecarMetadataSource` is neither heard in the audio nor seen in
    a frame, and tagging it `heard` would put a falsehood into the one field the
    whole artifact rests on. See DECISIONS.md.
    """

    HEARD = "heard"
    """Stated in the audio. Traceable to a transcript span."""

    SEEN = "seen"
    """Directly visible in a keyframe."""

    INFERRED = "inferred"
    """Neither stated nor plainly visible; concluded from context."""

    STATED = "stated"
    """Written by the author — caption, post text, supplied description."""


FACTUAL_MODALITIES = frozenset({Modality.HEARD, Modality.SEEN, Modality.STATED})
"""What an agent filters to when it wants facts rather than hypotheses."""


class StratumModel(BaseModel):
    """Base for every schema model.

    `extra="forbid"` is deliberate. A model backend that invents a field should
    fail loudly and get repaired, not have the extra silently dropped — silently
    dropping is exactly how provenance fields disappear.
    """

    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class ProvenancedValue(StratumModel):
    """A value that knows where it came from. Used for entity attributes."""

    value: str
    modality: Modality
    confidence: Confidence
    source_kf: str | None = Field(
        default=None, description="Keyframe id this value was established at, if any."
    )
    transcript_span: tuple[float, float] | None = Field(
        default=None, description="Seconds [start, end] when modality is `heard`."
    )


class ProvenancedQuantity(StratumModel):
    """A counted or measured value with provenance. Kept distinct from
    ProvenancedValue so quantities stay numeric and comparable."""

    value: float
    unit: str | None = None
    modality: Modality
    confidence: Confidence
    source_kf: str | None = None


class Claim(StratumModel):
    """An assertion the pipeline is willing to make, and its warrant."""

    id: str
    text: str
    modality: Modality
    confidence: Confidence
    transcript_span: tuple[float, float] | None = None
    source_kf: str | None = None
    entities: list[str] = Field(default_factory=list)

    def is_factual(self) -> bool:
        return self.modality in FACTUAL_MODALITIES


def json_ready(value: Any) -> Any:
    """Normalise a pydantic dump for stable on-disk JSON."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    return value
