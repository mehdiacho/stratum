"""Configuration models.

Two rules hold this file together, both from CLAUDE.md:

1. Model identifiers and prices live in config, never in code. There is not a
   single model name in this module, only the shape of the slot one goes into.
2. Each stage depends on a declared slice of the config, and changing that slice
   invalidates that stage and everything after it. `stage_fingerprint` is what
   makes resumability correct rather than optimistic.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Posture(str, Enum):
    """A preset over the three backend slots, not a code path.

    MOCK is not in 02_BACKEND_ABSTRACTION.md. It exists because the Phase 0
    milestone is "runs end to end with all-mock backends", and that needs to be a
    selectable posture rather than a test-only fixture. See DECISIONS.md.
    """

    MOCK = "mock"
    HYBRID = "hybrid"
    CLOUD = "cloud"
    LOCAL = "local"


class StratumConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendConfig(StratumConfigModel):
    """Common shape for all three slots.

    `api_key_env` names an environment variable. The key itself never appears in
    config, in a run directory, or in a log line.
    """

    provider: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class TranscriberConfig(BackendConfig):
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "int8"
    language: str | None = Field(
        default=None, description="None means detect. Pinning it is faster and more accurate."
    )
    beam_size: int = 5


class EmbedderConfig(BackendConfig):
    device: Literal["auto", "cpu", "cuda"] = "auto"
    batch_size: int = 32
    dim: int = Field(default=512, description="Only consulted by mock/dummy embedders.")


class ReasonerConfig(BackendConfig):
    max_output_tokens: int = 800
    temperature: float = 0.2
    max_attempts: int = Field(
        default=3,
        description=(
            "Schema-enforced output with a repair retry. Persistent failure writes "
            "status=failed and the run continues — one bad keyframe must not "
            "destroy a run."
        ),
    )
    image_detail: str | None = None


class BackendsConfig(StratumConfigModel):
    transcriber: TranscriberConfig = Field(default_factory=TranscriberConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    reasoner: ReasonerConfig = Field(default_factory=ReasonerConfig)


class SampleConfig(StratumConfigModel):
    """S3. Never decode at source frame rate — this is the single most important
    efficiency decision in the pipeline."""

    sample_fps: float = Field(default=2.0, gt=0.0, le=30.0)
    long_edge_px: int = Field(default=1024, ge=64)
    jpeg_quality: int = Field(default=85, ge=1, le=100)
    detect_shots: bool = True
    adaptive_threshold: float = Field(
        default=3.0,
        description="PySceneDetect AdaptiveDetector — handles the gradual transitions "
        "that plain ContentDetector misses on fades and slow pans.",
    )
    min_scene_len_s: float = 0.6
    scene_detect_downscale: int = Field(
        default=2, ge=1, description="Detector-internal downscale factor. Speed only."
    )


class SelectConfig(StratumConfigModel):
    """S4. Three tiers, cheapest first."""

    phash_threshold: int = Field(
        default=8,
        ge=0,
        le=64,
        description="Hamming distance from the last RETAINED frame, not the previous "
        "candidate. Comparing to the previous candidate lets slow drift accumulate "
        "until the scene has completely changed without any single step crossing.",
    )
    embed_similarity_threshold: float = Field(default=0.94, ge=0.0, le=1.0)
    use_embedding_tier: bool = True
    use_sharpness_tier: bool = True
    sharpness_cluster_radius: int = Field(
        default=2,
        ge=0,
        description="How many collapsed neighbours either side to consider when "
        "picking the sharpest frame in a cluster.",
    )
    max_keyframes_per_minute: float = Field(default=12.0, gt=0.0)
    max_keyframes_total: int = Field(default=200, gt=0)
    threshold_search_max_iterations: int = Field(
        default=12,
        description="Budget capping binary-searches the tier-2 threshold. It never "
        "truncates — truncation deletes the end of the video, which is where the "
        "finished thing is.",
    )
    threshold_search_floor: float = Field(default=0.60, ge=0.0, le=1.0)


class WeighConfig(StratumConfigModel):
    """S5. Thresholds for classifying where the information lives."""

    audio_led_wpm: float = Field(
        default=110.0,
        description="Words per minute above which "
        "speech is dense enough to plausibly carry the video.",
    )
    sparse_speech_wpm: float = Field(default=45.0)
    min_asr_confidence: float = Field(default=0.55)
    visual_led_kfpm: float = Field(
        default=6.0,
        description="Keyframes retained per minute above which the picture is doing real work.",
    )
    enable_grounding_check: bool = Field(
        default=False,
        description="Costs model calls. Skipped in Phase 1 (05_BUILD_PLAN.md); the two "
        "cheap signals are enough to classify.",
    )
    grounding_sample_size: int = 10
    force_class: str | None = Field(
        default=None,
        description="Override the computed class. Exists for R6 — measuring whether the "
        "profile changes output quality or only the prompt.",
    )


class TraverseConfig(StratumConfigModel):
    """S6. The core."""

    prior_frames_in_context: int = Field(
        default=1,
        ge=0,
        le=2,
        description="0 halves image cost, 2 helps on difficult content. D1 — unresolved, "
        "to be settled empirically in Phase 2.",
    )
    transcript_window_margin_s: float = Field(default=3.0, ge=0.0)
    state_token_budget: int = Field(
        default=1200,
        gt=0,
        description="The running state is REPLACED each turn, never accumulated. If "
        "state_after exceeds this, it is compacted and the compaction is logged. "
        "Constant compaction means the budget is wrong.",
    )
    skip_unchanged: bool = Field(
        default=False,
        description="When the model reports no_meaningful_change, still record the "
        "transition. Turning this on drops it from the artifact, which hides the "
        "signal that S4 thresholds are too loose.",
    )


class SynthesiseConfig(StratumConfigModel):
    """S7."""

    max_failed_transition_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="D4 — currently no threshold, so a run with 20 of 80 failures still "
        "synthesises and produces a confidently wrong document. 1.0 preserves the "
        "specified behaviour; Phase 2 decides the real number.",
    )
    include_failed_in_context: bool = False


class EmitConfig(StratumConfigModel):
    """S8."""

    write_markdown: bool = True
    write_chunks: bool = True
    keep_candidates: bool = Field(
        default=False,
        description="Candidates are disposable after S4. Keyframes never are.",
    )
    markdown_template: str = "artifact.md.j2"
    chunk_transitions: bool = True


class PromptsConfig(StratumConfigModel):
    """Prompts are versioned files, never string literals. The filename is part of
    the stage fingerprint, so a prompt revision invalidates S6."""

    traverse: str = "s6_traverse.v1.md"
    synthesise: str = "s7_synthesise.v1.md"
    grounding: str = "s5_grounding.v1.md"
    directory: str | None = Field(
        default=None, description="Override the bundled prompts/ directory."
    )


class PipelineConfig(StratumConfigModel):
    sample: SampleConfig = Field(default_factory=SampleConfig)
    select: SelectConfig = Field(default_factory=SelectConfig)
    weigh: WeighConfig = Field(default_factory=WeighConfig)
    traverse: TraverseConfig = Field(default_factory=TraverseConfig)
    synthesise: SynthesiseConfig = Field(default_factory=SynthesiseConfig)
    emit: EmitConfig = Field(default_factory=EmitConfig)

    # S1 — silence detection. A silent video is a normal path, not an error.
    audio_sample_rate: int = 16000
    silence_dbfs_threshold: float = Field(
        default=-50.0,
        description="Mean volume below this with no detected speech means treat as silent.",
    )


class LimitsConfig(StratumConfigModel):
    max_cost_usd_per_video: float = Field(
        default=1.00,
        gt=0.0,
        description="A hard stop, not a warning. The orchestrator halts and writes a "
        "partial artifact with everything completed so far.",
    )
    max_wall_time_s: float = Field(default=3600.0, gt=0.0)
    pricing_staleness_warn_days: int = 90


class PathsConfig(StratumConfigModel):
    runs_dir: Path = Path("runs")
    pricing_table: Path | None = None


class Config(StratumConfigModel):
    posture: Posture = Posture.MOCK
    backends: BackendsConfig = Field(default_factory=BackendsConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)

    @model_validator(mode="after")
    def _validate_budget_sanity(self) -> Config:
        sel = self.pipeline.select
        if sel.threshold_search_floor >= sel.embed_similarity_threshold:
            raise ValueError(
                "select.threshold_search_floor must be below embed_similarity_threshold; "
                "the budget search raises the threshold from the configured value upward"
            )
        return self

    # -- stage fingerprints -------------------------------------------------

    def _slice_for(self, stage: str) -> dict[str, Any]:
        """The config a stage actually depends on.

        Declared explicitly rather than hashing the whole config, so that changing
        the keyframe threshold invalidates S4 onward but leaves the expensive
        transcription alone (01_ARCHITECTURE.md, Resumability).
        """
        p = self.pipeline
        b = self.backends
        match stage:
            case "S0":
                return {}
            case "S1":
                return {
                    "audio_sample_rate": p.audio_sample_rate,
                    "silence_dbfs_threshold": p.silence_dbfs_threshold,
                }
            case "S2":
                return {"transcriber": b.transcriber.model_dump(mode="json")}
            case "S3":
                return {"sample": p.sample.model_dump(mode="json")}
            case "S4":
                return {
                    "select": p.select.model_dump(mode="json"),
                    "embedder": b.embedder.model_dump(mode="json"),
                }
            case "S5":
                return {"weigh": p.weigh.model_dump(mode="json")}
            case "S6":
                return {
                    "traverse": p.traverse.model_dump(mode="json"),
                    "reasoner": b.reasoner.model_dump(mode="json"),
                    "prompt": self.prompts.traverse,
                }
            case "S7":
                return {
                    "synthesise": p.synthesise.model_dump(mode="json"),
                    "reasoner": b.reasoner.model_dump(mode="json"),
                    "prompt": self.prompts.synthesise,
                }
            case "S8":
                return {"emit": p.emit.model_dump(mode="json")}
            case _:
                raise ValueError(f"unknown stage {stage!r}")

    def stage_fingerprint(self, stage: str) -> str:
        payload = json.dumps(self._slice_for(stage), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def snapshot(self) -> dict[str, Any]:
        """Written into every run directory and into artifact.json.

        Nothing secret can end up here — keys are referenced by env-var name only.
        """
        return self.model_dump(mode="json")
