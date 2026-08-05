"""`chunks.jsonl` — the handoff point.

STRATUM does not own the vector store. It emits chunks and stops.

Chunks break at transition and step boundaries, never at a token count. A chunk
that splits a state change in half produces a retrieved fragment describing a
world that never existed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from stratum.schema.common import Confidence, Modality, StratumModel


class ChunkKind(str, Enum):
    OVERVIEW = "overview"
    STEP = "step"
    TRANSITION = "transition"
    ENTITY = "entity"
    OPEN_QUESTION = "open_question"
    CONFLICT = "conflict"


class ChunkSource(StratumModel):
    run_id: str
    video: str
    timestamp_range_s: tuple[float, float] | None = None
    origin: str | None = None


class Chunk(StratumModel):
    """Self-contained by design: it repeats the context needed to stand alone,
    because standing alone is how it will be retrieved.

    Modality tags are written *inline in the text*, not only in metadata.
    Retrieval layers strip metadata; the text survives. A model reading the chunk
    must be able to see that 0.45 without access to a schema.
    """

    chunk_id: str
    kind: ChunkKind
    text: str
    source: ChunkSource
    keyframes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    modalities_present: list[Modality] = Field(default_factory=list)
    confidence: Confidence = 0.5
