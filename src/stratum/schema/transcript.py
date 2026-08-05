"""The normalised transcript. Every Transcriber backend returns exactly this,
whatever shape the provider actually emits.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from stratum.schema.common import Confidence, StratumModel


class SpeechQuality(str, Enum):
    """How much the transcript can be trusted as a witness to the frames.

    `low` is not a failure. Song lyrics over a build video are noise, and knowing
    they are noise is useful — S5 uses this to push the profile toward visual_led.
    """

    GOOD = "good"
    LOW = "low"
    NONE = "none"


class TranscriptWord(StratumModel):
    """Word-level timing is required, not optional. S6 aligns a transcript window
    to each keyframe, and segment granularity is too coarse when keyframes land
    two seconds apart (01_ARCHITECTURE.md S2)."""

    start: float
    end: float
    text: str
    confidence: Confidence | None = None


class TranscriptSegment(StratumModel):
    id: int
    start: float
    end: float
    text: str
    confidence: Confidence | None = None
    speaker: str | None = Field(
        default=None,
        description=(
            "Diarisation hook. Explicitly out of scope for Phase 1 "
            "(00_PRODUCT_BRIEF.md §4) — nothing populates this."
        ),
    )


class Transcript(StratumModel):
    language: str | None = None
    duration_s: float = 0.0
    has_speech: bool = True
    speech_quality: SpeechQuality = SpeechQuality.GOOD
    segments: list[TranscriptSegment] = Field(default_factory=list)
    words: list[TranscriptWord] = Field(default_factory=list)
    backend: str | None = None
    model: str | None = None

    @classmethod
    def silent(cls, duration_s: float, *, backend: str | None = None) -> Transcript:
        """The silent-video path is a normal path, not an error."""
        return cls(
            language=None,
            duration_s=duration_s,
            has_speech=False,
            speech_quality=SpeechQuality.NONE,
            backend=backend,
        )

    def words_between(
        self, start_s: float, end_s: float, *, margin_s: float = 0.0
    ) -> list[TranscriptWord]:
        """Words overlapping [start_s - margin, end_s + margin].

        Overlap rather than containment: a word straddling the boundary is part of
        what was being said at that moment and dropping it truncates mid-sentence.
        """
        lo = start_s - margin_s
        hi = end_s + margin_s
        return [w for w in self.words if w.end >= lo and w.start <= hi]

    def text_between(self, start_s: float, end_s: float, *, margin_s: float = 0.0) -> str:
        words = self.words_between(start_s, end_s, margin_s=margin_s)
        if words:
            return " ".join(w.text.strip() for w in words if w.text.strip())
        # Fall back to segments when a backend gave us no word timings.
        lo, hi = start_s - margin_s, end_s + margin_s
        return " ".join(
            s.text.strip() for s in self.segments if s.end >= lo and s.start <= hi
        ).strip()

    @property
    def word_count(self) -> int:
        if self.words:
            return len(self.words)
        return sum(len(s.text.split()) for s in self.segments)

    @property
    def mean_confidence(self) -> float | None:
        scored = [w.confidence for w in self.words if w.confidence is not None]
        if not scored:
            scored = [s.confidence for s in self.segments if s.confidence is not None]
        if not scored:
            return None
        return sum(scored) / len(scored)
