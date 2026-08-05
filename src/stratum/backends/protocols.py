"""The three protocols. There are exactly three points where a model is invoked.

**No pipeline stage may import a provider SDK or branch on which backend is
active.** If a stage needs to know, the abstraction is wrong — fix the
abstraction, not the stage.

Two deliberate departures from the sketch in 02_BACKEND_ABSTRACTION.md, both
recorded in DECISIONS.md:

1. Every call returns a result object carrying a `Usage`, rather than the bare
   value. Cost is a non-negotiable and threading it back through a side channel
   would be the thing that quietly stops working.
2. `Reasoner` has a second method, `synthesise`. S7 is a model call too; the
   original sketch only described S6.

Prompts are rendered by the stage, from versioned files, and arrive here as text.
Adapters do model plumbing and nothing else — otherwise every new adapter
reimplements prompt logic and they drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stratum.config.pricing import CostModel, Usage
from stratum.schema.synthesis import SynthesisDraft
from stratum.schema.transcript import Transcript
from stratum.schema.transition import TransitionDraft

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


# --------------------------------------------------------------------------
# Transcriber
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TranscribeResult:
    transcript: Transcript
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, wav_path: Path, *, language: str | None = None) -> TranscribeResult: ...

    @property
    def cost_model(self) -> CostModel: ...

    @property
    def name(self) -> str: ...


# --------------------------------------------------------------------------
# Embedder
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbedResult:
    vectors: np.ndarray
    """Shape (n, d), L2-normalised. Cosine similarity is then a dot product."""

    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Embedder(Protocol):
    def embed_images(self, paths: list[Path]) -> EmbedResult: ...

    @property
    def cost_model(self) -> CostModel: ...

    @property
    def name(self) -> str: ...


# --------------------------------------------------------------------------
# Reasoner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonRequest:
    """One S6 call: this keyframe, the state we were in, what was being said.

    `images` is ordered oldest-first with the current keyframe LAST, so a model
    reading them in order sees before-then-after.
    """

    system_prompt: str
    user_prompt: str
    images: list[Path]
    max_output_tokens: int = 800
    temperature: float = 0.2
    max_attempts: int = 3
    # Carried for logging, mocks and debugging. Adapters must not need these.
    keyframe_id: str = ""
    timestamp_s: float = 0.0


@dataclass(frozen=True)
class ReasonResponse:
    draft: TransitionDraft
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    raw: str | None = None


@dataclass(frozen=True)
class SynthesiseRequest:
    system_prompt: str
    user_prompt: str
    max_output_tokens: int = 4000
    temperature: float = 0.2
    max_attempts: int = 3


@dataclass(frozen=True)
class SynthesiseResponse:
    draft: SynthesisDraft
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    raw: str | None = None


@runtime_checkable
class Reasoner(Protocol):
    def analyse(self, req: ReasonRequest) -> ReasonResponse: ...

    def synthesise(self, req: SynthesiseRequest) -> SynthesiseResponse: ...

    @property
    def cost_model(self) -> CostModel: ...

    @property
    def name(self) -> str: ...
