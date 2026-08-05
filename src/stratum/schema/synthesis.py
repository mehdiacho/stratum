"""S7 output — the model-facing draft for synthesis.

Same draft/canonical split as S6: the model proposes, the pipeline assigns IDs and
verifies that every reference points at something that exists.
"""

from __future__ import annotations

from pydantic import Field

from stratum.schema.artifact import Conflict, Material, OpenQuestion, Step
from stratum.schema.common import Confidence, Modality, StratumModel


class DraftStep(StratumModel):
    title: str
    detail: str
    derived_from: list[str] = Field(
        default_factory=list, description="Transition ids this step was read out of."
    )
    confidence: Confidence = 0.5


class DraftMaterial(StratumModel):
    entity_label: str
    quantity: float | None = None
    unit: str | None = None
    modality: Modality
    confidence: Confidence
    note: str | None = None


class DraftOpenQuestion(StratumModel):
    """A first-class output, not an afterthought. What the video could not tell
    you is exactly what gets chased into text sources afterwards."""

    text: str
    why_it_matters: str
    related_entity_labels: list[str] = Field(default_factory=list)


class DraftConflict(StratumModel):
    """Where the audio and the picture disagreed. Recorded, not resolved — see D2."""

    heard: str
    seen: str
    keyframes: list[str] = Field(default_factory=list)
    note: str | None = None


class SynthesisDraft(StratumModel):
    overview: str
    steps: list[DraftStep] = Field(default_factory=list)
    materials: list[DraftMaterial] = Field(default_factory=list)
    open_questions: list[DraftOpenQuestion] = Field(default_factory=list)
    conflicts: list[DraftConflict] = Field(default_factory=list)


class SynthesisResult(StratumModel):
    """The canonical form, written to `synthesis.json`.

    Separate from the draft because every reference in here has been checked
    against something that actually exists: a step cannot cite a transition that
    was never recorded, and a conflict cannot cite a keyframe that was never
    retained.
    """

    overview: str = ""
    procedure: list[Step] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    dropped_references: list[str] = Field(
        default_factory=list,
        description="References the model produced that pointed at nothing. Kept "
        "visible: a model inventing transition ids is a signal, not noise.",
    )
