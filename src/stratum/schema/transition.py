"""State transitions — the unit of output.

An instructional video is a sequence of *changes*, not a sequence of images. The
model is asked "given that we were here, what is different now, and what action
produced the difference?" and its answer lands in `TransitionDraft`. The pipeline
then assigns stable IDs and produces the canonical `Transition`.

The draft/canonical split is deliberate: the model never invents an ID, and the
canonical record never depends on the model having got its bookkeeping right.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from stratum.schema.common import Claim, Confidence, Modality, StratumModel
from stratum.schema.media import Keyframe


class TransitionStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    RETRIED = "retried"
    SKIPPED = "skipped"


class TranscriptWeight(str, Enum):
    """How much the transcript window counted for, set by the S5 profile."""

    PRIMARY = "primary"
    EQUAL = "equal"
    HINT = "hint"
    OMITTED = "omitted"


# --------------------------------------------------------------------------
# What the model returns. Schema-enforced; parse failures retry with a repair
# instruction; persistent failures write status=failed and the run continues.
# --------------------------------------------------------------------------


class DraftClaim(StratumModel):
    text: str
    modality: Modality
    confidence: Confidence
    transcript_span: tuple[float, float] | None = None


class DraftEntity(StratumModel):
    """An entity as the model reports it. `label` is the identity — the pipeline
    derives the stable id from it, so the model never chooses one."""

    label: str
    kind: str = "object"
    attrs: dict[str, str] = Field(default_factory=dict)
    modality: Modality
    confidence: Confidence
    note: str | None = None


class TransitionDraft(StratumModel):
    """The S6 reasoner response, before IDs are assigned.

    `delta` is the load-bearing field and the known failure mode of the S6 prompt
    is a model that fills it with a description of the image instead of the
    change. See prompts/s6_traverse.v1.md.
    """

    delta: str
    action_inferred: str | None = None
    action_confidence: Confidence = 0.5
    entities_added: list[DraftEntity] = Field(default_factory=list)
    entities_changed: list[DraftEntity] = Field(default_factory=list)
    entities_removed: list[str] = Field(default_factory=list)
    claims: list[DraftClaim] = Field(default_factory=list)
    uncertainty: str | None = None
    state_after: str = Field(
        description=(
            "The running state as of this keyframe. Replaces the previous state "
            "entirely — it is never appended to, or context grows without bound."
        )
    )
    no_meaningful_change: bool = Field(
        default=False,
        description=(
            "Set when this frame shows the same state as the last one. An honest "
            "'nothing changed' is worth more than an invented delta, and it is the "
            "signal that S4's thresholds are too loose."
        ),
    )


# --------------------------------------------------------------------------
# Canonical
# --------------------------------------------------------------------------


class ActionInferred(StratumModel):
    value: str
    confidence: Confidence


class TranscriptWindow(StratumModel):
    start_s: float
    end_s: float
    text: str
    weight: TranscriptWeight


class Transition(StratumModel):
    """One record per keyframe, appended to transitions.jsonl as S6 progresses so
    a crash at keyframe 60 of 80 loses nothing."""

    id: str
    keyframe: Keyframe
    delta: str = ""
    action_inferred: ActionInferred | None = None
    entities_added: list[str] = Field(default_factory=list)
    entities_changed: list[str] = Field(default_factory=list)
    entities_removed: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    uncertainty: str | None = None
    transcript_window: TranscriptWindow | None = None
    no_meaningful_change: bool = False
    status: TransitionStatus = TransitionStatus.OK
    error: str | None = Field(
        default=None,
        description="Populated when status is failed. A gap in the record must be visible.",
    )
    attempts: int = 1

    @classmethod
    def failed(
        cls, *, transition_id: str, keyframe: Keyframe, error: str, attempts: int
    ) -> Transition:
        return cls(
            id=transition_id,
            keyframe=keyframe,
            delta="",
            status=TransitionStatus.FAILED,
            error=error,
            attempts=attempts,
        )
