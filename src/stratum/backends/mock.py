"""Mock backends.

`MockReasoner` is mandatory, not a convenience (CLAUDE.md). Without it every test
costs money and needs a network, and the pipeline logic never gets tested
properly.

All three are deterministic: the same input produces byte-identical output, so
S0-S5 determinism assertions hold with mocks in place and a diff in the artifact
means a real change.

`MockEmbedder` is worth a second look. It does not fake a distance metric — it
computes one, from a downsampled grayscale image. Similar frames genuinely come
out similar, so S4's tier-2 dedup behaves realistically in tests instead of
exercising a random-number generator.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from stratum.backends.protocols import (
    EmbedResult,
    ReasonRequest,
    ReasonResponse,
    SynthesiseRequest,
    SynthesiseResponse,
    TranscribeResult,
)
from stratum.config.pricing import CostModel, Usage
from stratum.errors import SchemaViolation
from stratum.schema.common import Modality
from stratum.schema.synthesis import (
    DraftConflict,
    DraftMaterial,
    DraftOpenQuestion,
    DraftStep,
    SynthesisDraft,
)
from stratum.schema.transcript import (
    SpeechQuality,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from stratum.schema.transition import DraftClaim, DraftEntity, TransitionDraft

_TRANSCRIPT_TAG = re.compile(r"<transcript[^>]*>(.*?)</transcript>", re.DOTALL)
_DESCRIPTION_TAG = re.compile(r"<description>(.*?)</description>", re.DOTALL)


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


# --------------------------------------------------------------------------
# Transcriber
# --------------------------------------------------------------------------

_MOCK_LEXICON = [
    "so",
    "we",
    "take",
    "this",
    "piece",
    "here",
    "and",
    "line",
    "it",
    "up",
    "with",
    "the",
    "other",
    "one",
    "then",
    "just",
    "push",
    "it",
    "in",
    "until",
    "it",
    "seats",
    "properly",
    "you",
    "can",
    "see",
    "the",
    "mark",
    "on",
    "the",
    "side",
    "that",
    "tells",
    "you",
    "how",
    "far",
    "it",
    "goes",
    "and",
    "the",
    "spacing",
    "between",
    "them",
    "is",
    "fifteen",
    "centimetres",
    "which",
    "matters",
    "more",
    "than",
    "people",
    "think",
]


class MockTranscriber:
    """Emits deterministic, timestamped, plausible-shaped speech.

    It is not pretending to hear the audio. It reads the WAV header for duration
    and lays canned words across it, which is exactly enough to exercise
    alignment, windowing and the S5 speech-density signal.
    """

    def __init__(
        self,
        cost_model: CostModel,
        *,
        model: str | None = None,
        words_per_minute: float = 90.0,
        silent: bool = False,
    ) -> None:
        self._cost_model = cost_model
        self._model = model or "mock-asr-v1"
        self.words_per_minute = words_per_minute
        self.silent = silent

    @property
    def name(self) -> str:
        return f"mock:{self._model}"

    @property
    def cost_model(self) -> CostModel:
        return self._cost_model

    @staticmethod
    def _wav_duration_s(wav_path: Path) -> float:
        with contextlib.suppress(OSError, wave.Error), wave.open(str(wav_path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return frames / float(rate)
        return 0.0

    def transcribe(self, wav_path: Path, *, language: str | None = None) -> TranscribeResult:
        duration = self._wav_duration_s(wav_path)
        if self.silent or duration <= 0.0:
            return TranscribeResult(
                transcript=Transcript.silent(duration, backend=self.name),
                usage=Usage(audio_seconds=duration),
            )

        word_count = max(1, int(duration / 60.0 * self.words_per_minute))
        step = duration / word_count
        rng = np.random.default_rng(_seed(wav_path.name, duration))

        words: list[TranscriptWord] = []
        for i in range(word_count):
            start = round(i * step, 3)
            end = round(min(duration, start + step * 0.8), 3)
            words.append(
                TranscriptWord(
                    start=start,
                    end=end,
                    text=_MOCK_LEXICON[i % len(_MOCK_LEXICON)],
                    confidence=round(float(0.75 + 0.2 * rng.random()), 3),
                )
            )

        segments: list[TranscriptSegment] = []
        per_segment = 12
        for seg_index, offset in enumerate(range(0, len(words), per_segment)):
            block = words[offset : offset + per_segment]
            confidences = [w.confidence for w in block if w.confidence is not None]
            segments.append(
                TranscriptSegment(
                    id=seg_index,
                    start=block[0].start,
                    end=block[-1].end,
                    text=" ".join(w.text for w in block),
                    confidence=round(sum(confidences) / len(confidences), 3)
                    if confidences
                    else None,
                )
            )

        transcript = Transcript(
            language=language or "en",
            duration_s=duration,
            has_speech=True,
            speech_quality=SpeechQuality.GOOD,
            segments=segments,
            words=words,
            backend=self.name,
            model=self._model,
        )
        return TranscribeResult(transcript=transcript, usage=Usage(audio_seconds=duration))


# --------------------------------------------------------------------------
# Embedder
# --------------------------------------------------------------------------


class MockEmbedder:
    """A real, tiny, deterministic image embedding.

    Grayscale, resized to `grid x grid`, mean-centred and L2-normalised. It has no
    semantic understanding at all — it will not spot that two shots of the same
    bench from different angles are the same scene, which is precisely what tier 2
    exists to catch. That limitation is the honest difference between this and a
    real encoder, and it is why the local SigLIP backend still has to be built.
    """

    def __init__(self, cost_model: CostModel, *, model: str | None = None, grid: int = 16) -> None:
        self._cost_model = cost_model
        self._model = model or "mock-embed-v1"
        self.grid = grid

    @property
    def name(self) -> str:
        return f"mock:{self._model}"

    @property
    def cost_model(self) -> CostModel:
        return self._cost_model

    @property
    def dim(self) -> int:
        return self.grid * self.grid

    def embed_images(self, paths: list[Path]) -> EmbedResult:
        if not paths:
            return EmbedResult(vectors=np.zeros((0, self.dim), dtype=np.float32))

        rows = np.zeros((len(paths), self.dim), dtype=np.float32)
        for i, path in enumerate(paths):
            with Image.open(path) as img:
                small = img.convert("L").resize((self.grid, self.grid), Image.Resampling.BILINEAR)
            vec = np.asarray(small, dtype=np.float32).reshape(-1) / 255.0
            vec = vec - float(vec.mean())
            norm = float(np.linalg.norm(vec))
            rows[i] = vec / norm if norm > 1e-8 else vec
        return EmbedResult(vectors=rows, usage=Usage(images=len(paths)))


# --------------------------------------------------------------------------
# Reasoner
# --------------------------------------------------------------------------

_MOCK_ACTIONS = (
    "positioning a component",
    "joining two parts",
    "measuring and marking",
    "tightening a fitting",
    "testing for leaks",
    "filling the reservoir",
)

_MOCK_ENTITIES = (
    ("PVC grow channel", "material"),
    ("90 degree elbow", "material"),
    ("submersible pump", "equipment"),
    ("reservoir tub", "equipment"),
    ("net pot", "material"),
    ("silicone sealant", "consumable"),
    ("cordless drill", "tool"),
)


class MockReasoner:
    """Canned but structurally complete responses.

    Every field the real reasoner must populate is populated, including all four
    modalities and the `no_meaningful_change` signal, so the stages downstream are
    genuinely exercised rather than merely executed.

    `fail_every_n` deliberately raises `SchemaViolation` on a schedule. The failure
    path — write `status: failed`, keep going, surface the gap in the artifact —
    is load-bearing and untested code that only runs during incidents is code that
    does not work.
    """

    def __init__(
        self,
        cost_model: CostModel,
        *,
        model: str | None = None,
        fail_every_n: int = 0,
        tokens_in_per_call: int = 900,
        tokens_out_per_call: int = 260,
    ) -> None:
        self._cost_model = cost_model
        self._model = model or "mock-vlm-v1"
        self.fail_every_n = fail_every_n
        self.tokens_in_per_call = tokens_in_per_call
        self.tokens_out_per_call = tokens_out_per_call
        self._calls = 0

    @property
    def name(self) -> str:
        return f"mock:{self._model}"

    @property
    def cost_model(self) -> CostModel:
        return self._cost_model

    # -- S6 -----------------------------------------------------------------

    def analyse(self, req: ReasonRequest) -> ReasonResponse:
        self._calls += 1
        if self.fail_every_n and self._calls % self.fail_every_n == 0:
            raise SchemaViolation(
                f"mock reasoner: simulated persistent schema failure on call {self._calls}"
            )

        heard = self._tagged(_TRANSCRIPT_TAG, req.user_prompt)
        stated = self._tagged(_DESCRIPTION_TAG, req.user_prompt)

        rng = np.random.default_rng(_seed(req.keyframe_id, req.timestamp_s))
        index = int(rng.integers(0, len(_MOCK_ENTITIES)))
        label, kind = _MOCK_ENTITIES[index]
        action = _MOCK_ACTIONS[int(rng.integers(0, len(_MOCK_ACTIONS)))]
        unchanged = bool(rng.random() < 0.08)

        claims: list[DraftClaim] = [
            DraftClaim(
                text=f"A {label.lower()} is visible in the working area",
                modality=Modality.SEEN,
                confidence=round(float(0.6 + 0.3 * rng.random()), 2),
            ),
            DraftClaim(
                text=f"The operator appears to be {action}",
                modality=Modality.INFERRED,
                confidence=round(float(0.3 + 0.3 * rng.random()), 2),
            ),
        ]
        if heard:
            span_start = max(0.0, req.timestamp_s - 3.0)
            claims.append(
                DraftClaim(
                    text=f"Narration at this point: {heard[:120]}",
                    modality=Modality.HEARD,
                    confidence=0.85,
                    transcript_span=(round(span_start, 2), round(req.timestamp_s + 3.0, 2)),
                )
            )
        if stated:
            claims.append(
                DraftClaim(
                    text=f"The supplied description mentions: {stated[:120]}",
                    modality=Modality.STATED,
                    confidence=0.9,
                )
            )

        if unchanged:
            delta = "No meaningful change from the previous keyframe; same arrangement, same parts."
        else:
            delta = (
                f"A {label.lower()} has been introduced to the workspace and moved into "
                f"position relative to the existing assembly."
            )

        draft = TransitionDraft(
            delta=delta,
            action_inferred=None if unchanged else action,
            action_confidence=round(float(0.4 + 0.4 * rng.random()), 2),
            entities_added=[]
            if unchanged
            else [
                DraftEntity(
                    label=label,
                    kind=kind,
                    attrs={"apparent_condition": "new"},
                    modality=Modality.SEEN,
                    confidence=round(float(0.6 + 0.3 * rng.random()), 2),
                )
            ],
            entities_changed=[],
            entities_removed=[],
            claims=claims,
            uncertainty=None
            if unchanged
            else "Exact dimensions are not legible at this resolution and are not stated.",
            state_after=(
                f"As of {req.timestamp_s:.1f}s: assembly in progress on a flat surface. "
                f"Most recently introduced: {label.lower()}. Last action: {action}."
            ),
            no_meaningful_change=unchanged,
        )
        return ReasonResponse(
            draft=draft,
            usage=Usage(
                tokens_in=self.tokens_in_per_call,
                tokens_out=self.tokens_out_per_call,
                images=len(req.images),
            ),
            attempts=1,
        )

    # -- S7 -----------------------------------------------------------------

    def synthesise(self, req: SynthesiseRequest) -> SynthesiseResponse:
        transition_ids = re.findall(r"\btr_\d{4}\b", req.user_prompt)
        keyframe_ids = re.findall(r"\bkf_\d{4}\b", req.user_prompt)

        steps: list[DraftStep] = []
        per_step = max(1, len(transition_ids) // 4 or 1)
        for ordinal, offset in enumerate(range(0, len(transition_ids), per_step), start=1):
            block = transition_ids[offset : offset + per_step]
            if not block:
                continue
            steps.append(
                DraftStep(
                    title=f"Stage {ordinal} of the build",
                    detail=(
                        "Parts are brought into position and joined. Derived from "
                        f"{len(block)} observed transitions."
                    ),
                    derived_from=block,
                    confidence=0.6,
                )
            )
            if ordinal >= 6:
                break

        draft = SynthesisDraft(
            overview=(
                "A build proceeding in stages on a flat working surface. Components are "
                "introduced, positioned and joined in sequence."
            ),
            steps=steps,
            materials=[
                DraftMaterial(
                    entity_label=label,
                    quantity=float(1 + (i % 3)),
                    unit="unit",
                    modality=Modality.SEEN,
                    confidence=0.7,
                    note="Counted from what is visible on the bench.",
                )
                for i, (label, _kind) in enumerate(_MOCK_ENTITIES[:4])
            ],
            open_questions=[
                DraftOpenQuestion(
                    text="Pump specification is never shown or stated.",
                    why_it_matters="Determines head height and flow rate for the whole system.",
                    related_entity_labels=["submersible pump"],
                ),
                DraftOpenQuestion(
                    text="Nutrient solution composition is not discussed.",
                    why_it_matters="Without it the build cannot actually be operated.",
                    related_entity_labels=[],
                ),
            ],
            conflicts=(
                [
                    DraftConflict(
                        heard="the narration says 20 mm",
                        seen="the fitting appears to read 25 mm",
                        keyframes=keyframe_ids[:1],
                        note="Unresolved; the pipeline records conflicts rather than picking.",
                    )
                ]
                if keyframe_ids
                else []
            ),
        )
        return SynthesiseResponse(
            draft=draft,
            usage=Usage(tokens_in=4200, tokens_out=800),
            attempts=1,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _tagged(pattern: re.Pattern[str], prompt: str) -> str:
        match = pattern.search(prompt)
        if not match:
            return ""
        return " ".join(match.group(1).split())
