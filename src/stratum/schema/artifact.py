"""`artifact.json` — the canonical output.

Everything else in the run directory is either an input to this or a view of it.
`artifact.md` is a template rendering of this model and is never hand-written;
`chunks.jsonl` is derived from it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from stratum.schema.common import (
    SCHEMA_VERSION,
    Confidence,
    Modality,
    ProvenancedQuantity,
    ProvenancedValue,
    StratumModel,
)
from stratum.schema.media import SourceKind
from stratum.schema.transition import Transition


class ModalityClass(str, Enum):
    AUDIO_LED = "audio_led"
    VISUAL_LED = "visual_led"
    BALANCED = "balanced"
    VISUAL_ONLY = "visual_only"


class ProfileScope(str, Enum):
    WHOLE_VIDEO = "whole_video"
    SLIDING_WINDOW = "sliding_window"


class ModalityProfile(StratumModel):
    """Where the information lives in this video, measured rather than assumed.

    Phase 1 computes one profile for the whole video and records the
    simplification here; Phase 2 computes it over a sliding window.
    """

    cls: ModalityClass = Field(alias="class")
    speech_density_wpm: float = 0.0
    mean_asr_confidence: float | None = None
    keyframes_per_minute: float = 0.0
    mean_visual_distance: float | None = None
    grounding_score: float | None = Field(
        default=None,
        description="Skipped in Phase 1 — it costs model calls (05_BUILD_PLAN.md).",
    )
    note: str | None = None
    scope: ProfileScope = ProfileScope.WHOLE_VIDEO

    model_config = StratumModel.model_config | {"populate_by_name": True}


class VideoSource(StratumModel):
    kind: SourceKind = SourceKind.LOCAL_FILE
    filename: str
    duration_s: float
    fps_avg: float
    resolution: tuple[int, int]
    content_hash: str
    rotation: int = 0
    # Sidecar metadata (06_INGESTION_ROADMAP.md). The caption is often the densest
    # text in the entire artifact — a parts list the narrator never says out loud.
    origin: str | None = None
    title: str | None = None
    description: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None


class CostBreakdown(StratumModel):
    total_usd: float = 0.0
    by_stage: dict[str, float] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    images: int = 0
    audio_seconds: float = 0.0
    pricing_table_verified_on: str | None = None
    cap_usd: float | None = None
    cap_hit: bool = False


class RunInfo(StratumModel):
    id: str
    created_at: datetime
    completed_at: datetime | None = None
    stratum_version: str
    source: VideoSource
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    stages_completed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    partial: bool = Field(
        default=False,
        description="True when the run halted early — cost cap, wall-time cap, or abort.",
    )


class Entity(StratumModel):
    """Deduplicated across the video, with every mention linked back."""

    id: str
    label: str
    kind: str = "object"
    attributes: dict[str, ProvenancedValue] = Field(default_factory=dict)
    first_seen_kf: str | None = None
    last_seen_kf: str | None = None
    mentions: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class Step(StratumModel):
    id: str
    ordinal: int
    title: str
    detail: str = ""
    derived_from: list[str] = Field(default_factory=list)
    timestamp_range_s: tuple[float, float] | None = None
    keyframes: list[str] = Field(default_factory=list)
    confidence: Confidence = 0.5


class Material(StratumModel):
    entity: str
    label: str
    quantity: ProvenancedQuantity | None = None
    note: str | None = None


class OpenQuestion(StratumModel):
    id: str
    text: str
    why_it_matters: str = ""
    related: list[str] = Field(default_factory=list)


class ConflictResolution(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED_HEARD = "resolved_heard"
    RESOLVED_SEEN = "resolved_seen"


class Conflict(StratumModel):
    id: str
    heard: str
    seen: str
    keyframes: list[str] = Field(default_factory=list)
    resolution: ConflictResolution = ConflictResolution.UNRESOLVED
    note: str | None = None


class Artifact(StratumModel):
    """The canonical model. `artifact.json` is `Artifact.model_dump_json()` and
    nothing else."""

    schema_version: str = SCHEMA_VERSION
    run: RunInfo
    modality_profile: ModalityProfile
    overview: str = ""
    entities: list[Entity] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    procedure: list[Step] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        # Checked on read. When this changes, ship a migration in the same commit —
        # never a reader that silently tolerates both shapes.
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"artifact schema_version {v!r} != supported {SCHEMA_VERSION!r}; "
                "a migration is required"
            )
        return v

    # -- convenience used by the renderer and the chunker -------------------

    def entity_by_id(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)

    def transition_by_id(self, transition_id: str) -> Transition | None:
        return next((t for t in self.transitions if t.id == transition_id), None)

    @property
    def failed_transition_ids(self) -> list[str]:
        return [t.id for t in self.transitions if t.status.value == "failed"]

    def claims_by_modality(self, modality: Modality) -> list[str]:
        return [c.text for t in self.transitions for c in t.claims if c.modality is modality]
