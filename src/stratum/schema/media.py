"""What `ffprobe` told us about the file, plus the sampling and selection records.

These are intermediate artifacts written into the run directory rather than parts
of `artifact.json`, but they are schema-enforced for the same reason everything
else is: S0-S5 are deterministic, and determinism you cannot assert is a rumour.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from stratum.schema.common import StratumModel


class SourceKind(str, Enum):
    LOCAL_FILE = "local_file"
    CORPUS = "corpus"


class VideoMeta(StratumModel):
    """Probe result. Note the two frame rates.

    Container fps and average real fps differ on variable-frame-rate phone
    footage, which is most of this corpus. Trust the average — the container
    value on a VFR file is a nominal ceiling, and using it to compute sampling
    intervals produces timestamps that drift against the audio.
    """

    filename: str
    path: str
    duration_s: float
    fps_container: float | None = None
    fps_avg: float
    width: int
    height: int
    rotation: int = Field(
        default=0,
        description=(
            "Degrees clockwise from the display matrix. Phone video usually carries "
            "this flag rather than rotated pixels; frames extracted without honouring "
            "it come out sideways and a VLM will confidently describe a sideways world."
        ),
    )
    has_audio_stream: bool = True
    video_codec: str | None = None
    audio_codec: str | None = None
    content_hash: str
    size_bytes: int

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    @property
    def is_variable_frame_rate(self) -> bool:
        if self.fps_container is None or self.fps_avg <= 0:
            return False
        return abs(self.fps_container - self.fps_avg) / self.fps_avg > 0.02


class Candidate(StratumModel):
    """One coarsely-sampled frame out of S3."""

    index: int
    timestamp_s: float
    path: str
    is_shot_start: bool = False


class SampleRecord(StratumModel):
    """S3 output. Written to the run dir so S4 is independently resumable."""

    sample_fps: float
    long_edge_px: int
    candidates: list[Candidate] = Field(default_factory=list)
    shot_boundaries_s: list[float] = Field(default_factory=list)
    detector: str | None = None
    note: str | None = None


class DropReason(str, Enum):
    PHASH = "phash_duplicate"
    EMBEDDING = "embedding_duplicate"
    SHARPNESS = "not_sharpest_in_cluster"


class Keyframe(StratumModel):
    """A retained frame. These are output, not scratch (03_OUTPUT_SCHEMA principle 4)."""

    id: str
    index: int
    path: str
    timestamp_s: float
    shot_boundary: bool = False
    sharpness: float = 0.0
    forced: bool = Field(
        default=False,
        description="First frame, last frame, or shot boundary — never dropped.",
    )
    represents_candidates: list[int] = Field(
        default_factory=list,
        description="Candidate indices this keyframe was selected out of.",
    )


class SelectionRecord(StratumModel):
    """S4 output, including the thresholds actually used.

    The final embedding threshold is recorded because budget capping searches for
    it rather than truncating, and a run is only reproducible if the search result
    is written down (01_ARCHITECTURE.md S4).
    """

    keyframes: list[Keyframe] = Field(default_factory=list)
    candidates_in: int = 0
    after_phash: int = 0
    after_embedding: int = 0
    phash_threshold: int = 0
    embed_similarity_threshold: float = 0.0
    embed_threshold_search_iterations: int = 0
    budget_applied: bool = False
    effective_budget: int | None = None
    mean_retained_distance: float | None = Field(
        default=None,
        description="Mean cosine distance between consecutive retained frames. S5 reads "
        "this as its visual-change-rate signal.",
    )
    note: str | None = None
