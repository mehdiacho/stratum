"""End-to-end pipeline tests on mock backends.

The first test in this file is the Phase 0 milestone from 05_BUILD_PLAN.md:
`stratum process` runs end to end with all-mock backends and emits a valid,
schema-conformant artifact full of fake content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import requires_ffmpeg
from stratum.config.models import Config
from stratum.pipeline.orchestrator import process
from stratum.run.manifest import STAGE_ORDER, Manifest
from stratum.schema.artifact import Artifact
from stratum.schema.chunks import Chunk
from stratum.schema.common import Modality
from stratum.schema.media import SampleRecord, SelectionRecord, VideoMeta
from stratum.schema.transcript import Transcript
from stratum.sources.protocols import VideoRef

pytestmark = [requires_ffmpeg, pytest.mark.slow]


def _load_artifact(path: Path) -> Artifact:
    return Artifact.model_validate_json(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The milestone
# --------------------------------------------------------------------------


def test_end_to_end_on_mocks_emits_a_valid_artifact(
    video_with_audio: Path, mock_config: Config
) -> None:
    result = process(VideoRef(path=video_with_audio), mock_config)

    assert result.stages_run == list(STAGE_ORDER)
    assert not result.partial

    artifact = _load_artifact(result.artifact_path)

    assert artifact.schema_version == "1.0"
    assert artifact.run.stages_completed == list(STAGE_ORDER)
    assert artifact.transitions, "a run with keyframes must produce transitions"
    assert artifact.procedure, "synthesis must produce a procedure"
    assert artifact.entities, "entities must be tracked across the video"
    assert artifact.open_questions, "open questions are a first-class output"

    assert result.rundir.artifact_md.is_file()
    assert result.rundir.chunks.is_file()

    # Keyframes are output, not scratch.
    kept = list(result.rundir.keyframes.glob("kf_*.jpg"))
    assert len(kept) == len(artifact.transitions)


def test_every_claim_carries_a_modality_and_a_source(
    video_with_audio: Path, mock_config: Config
) -> None:
    """Non-negotiable 1. If a code path can emit a claim without a modality tag,
    that path is a bug."""
    result = process(VideoRef(path=video_with_audio), mock_config)
    artifact = _load_artifact(result.artifact_path)

    claims = [c for t in artifact.transitions for c in t.claims]
    assert claims

    for claim in claims:
        assert isinstance(claim.modality, Modality)
        assert 0.0 <= claim.confidence <= 1.0
        assert claim.source_kf, f"{claim.id} has no source keyframe"
        if claim.modality is Modality.HEARD:
            assert claim.transcript_span is not None, (
                f"{claim.id} is tagged heard but is not traceable to a transcript span"
            )


def test_sidecar_description_produces_stated_claims(
    video_silent: Path, mock_config: Config, tmp_path: Path
) -> None:
    """The caption is often the densest text in the artifact. Claims drawn from it
    are `stated` — not `heard`, which would be a falsehood in the one field the
    whole design rests on."""
    ref = VideoRef(
        path=video_silent,
        title="NFT channel build",
        description="4x 1.5 m PVC channels, 15 cm spacing, 400 L/h pump.",
        author="@someone",
        origin="https://example.invalid/post/1",
    )
    result = process(ref, mock_config)
    artifact = _load_artifact(result.artifact_path)

    modalities = {c.modality for t in artifact.transitions for c in t.claims}
    assert Modality.STATED in modalities
    assert artifact.run.source.description == ref.description
    assert artifact.run.source.origin == ref.origin


# --------------------------------------------------------------------------
# Awkward inputs. Each one is real and each one will otherwise be discovered
# in production.
# --------------------------------------------------------------------------


def test_silent_video_is_a_normal_path(video_silent: Path, mock_config: Config) -> None:
    result = process(VideoRef(path=video_silent), mock_config)

    assert result.stages_run == list(STAGE_ORDER)
    artifact = _load_artifact(result.artifact_path)
    assert artifact.modality_profile.cls.value == "visual_only"

    # No claim may be tagged `heard` when there is nothing to hear.
    heard = [c for t in artifact.transitions for c in t.claims if c.modality is Modality.HEARD]
    assert heard == []

    transcript = Transcript.model_validate_json(
        result.rundir.transcript.read_text(encoding="utf-8")
    )
    assert transcript.has_speech is False


def test_rotation_metadata_is_baked_into_extracted_frames(
    video_rotated: Path, mock_config: Config
) -> None:
    """Phone video carries a rotation flag rather than rotated pixels. A frame
    extracted without honouring it comes out sideways, and a vision model will
    describe a sideways world with complete confidence."""
    result = process(VideoRef(path=video_rotated), mock_config)

    meta = VideoMeta.model_validate_json(result.rundir.video_meta.read_text(encoding="utf-8"))
    assert meta.rotation == 90
    # The stored pixels are landscape; the displayed image is portrait.
    assert meta.width > meta.height
    assert meta.display_height > meta.display_width

    from PIL import Image

    frame = next(iter(sorted(result.rundir.keyframes.glob("kf_*.jpg"))))
    with Image.open(frame) as img:
        assert img.height > img.width, "extracted frame is still sideways"


def test_variable_frame_rate_uses_the_average(video_vfr: Path, mock_config: Config) -> None:
    """Container fps is a nominal ceiling on a VFR file. Using it to compute
    sampling intervals produces timestamps that drift against the audio."""
    result = process(VideoRef(path=video_vfr), mock_config)
    meta = VideoMeta.model_validate_json(result.rundir.video_meta.read_text(encoding="utf-8"))

    assert meta.is_variable_frame_rate, "the fixture is not actually VFR"
    assert meta.fps_container is not None and meta.fps_container > meta.fps_avg > 0
    assert _load_artifact(result.artifact_path).run.source.fps_avg == meta.fps_avg


def test_video_shorter_than_one_sampling_interval(video_tiny: Path, mock_config: Config) -> None:
    """0.3 seconds at 2 fps. It must not crash, and it must say what happened."""
    slow = mock_config.model_copy(deep=True)
    slow.pipeline.sample.sample_fps = 2.0

    result = process(VideoRef(path=video_tiny), slow)

    assert result.artifact_path.is_file()
    artifact = _load_artifact(result.artifact_path)
    assert artifact.schema_version == "1.0"
    # Either it found one frame or it found none; both are valid, and neither is
    # allowed to be an exception.
    assert len(artifact.transitions) <= 2


def test_video_with_no_shot_boundaries(video_static: Path, mock_config: Config) -> None:
    """A single unchanging colour. S3 finds no cuts and S4 has nothing to
    distinguish — the degenerate case."""
    result = process(VideoRef(path=video_static), mock_config)

    sample = SampleRecord.model_validate_json(
        result.rundir.sample_record.read_text(encoding="utf-8")
    )
    selection = SelectionRecord.model_validate_json(
        result.rundir.selection_record.read_text(encoding="utf-8")
    )

    assert sample.candidates, "sampling should still produce candidates"
    assert sample.shot_boundaries_s == []
    # Everything is identical, so dedup should collapse it to the forced frames.
    assert len(selection.keyframes) <= 3
    assert result.artifact_path.is_file()


# --------------------------------------------------------------------------
# Determinism, resumability, budgets
# --------------------------------------------------------------------------


def test_deterministic_stages_are_byte_identical_across_runs(
    video_with_audio: Path, mock_config: Config, tmp_path: Path
) -> None:
    """S0-S5 are deterministic. That determinism is what makes the model stages
    debuggable, so it is asserted rather than assumed."""
    first = tmp_path / "runs_a"
    second = tmp_path / "runs_b"

    result_a = process(VideoRef(path=video_with_audio), mock_config, runs_dir=first)
    result_b = process(VideoRef(path=video_with_audio), mock_config, runs_dir=second)

    for name in ("video_meta", "sample_record", "selection_record", "modality_profile"):
        a = getattr(result_a.rundir, name).read_text(encoding="utf-8")
        b = getattr(result_b.rundir, name).read_text(encoding="utf-8")
        assert a == b, f"{name} differed between two runs of the same input"

    # The transcript carries a backend name but must otherwise match.
    ta = Transcript.model_validate_json(result_a.rundir.transcript.read_text(encoding="utf-8"))
    tb = Transcript.model_validate_json(result_b.rundir.transcript.read_text(encoding="utf-8"))
    assert ta.words == tb.words


def test_the_same_file_resolves_to_the_same_run_and_short_circuits(
    video_silent: Path, mock_config: Config
) -> None:
    first = process(VideoRef(path=video_silent), mock_config)
    second = process(VideoRef(path=video_silent), mock_config)

    assert first.run_id == second.run_id
    assert first.rundir.root == second.rundir.root
    assert second.stages_run == [], "a completed run must not redo work"


def test_killing_the_run_mid_traverse_resumes_rather_than_restarts(
    video_with_audio: Path, mock_config: Config
) -> None:
    """Resumability is a secondary acceptance criterion in 00_PRODUCT_BRIEF."""
    result = process(VideoRef(path=video_with_audio), mock_config)
    rundir = result.rundir

    lines = rundir.transitions.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3

    # Simulate a kill after keyframe 2: truncate the record and the carried state,
    # and mark S6 onward incomplete.
    keep = 2
    rundir.transitions.write_text("\n".join(lines[:keep]) + "\n", encoding="utf-8")
    state = json.loads(rundir.traverse_state.read_text(encoding="utf-8"))
    state["next_index"] = keep
    rundir.traverse_state.write_text(json.dumps(state), encoding="utf-8")

    manifest = Manifest.load(rundir.manifest)
    manifest.invalidate_from("S6")
    manifest.save(rundir.manifest)

    resumed = process(VideoRef(path=video_with_audio), mock_config)

    assert resumed.stages_run == ["S6", "S7", "S8"]
    assert resumed.stage_stats["S6"]["resumed_from_index"] == keep
    assert resumed.stage_stats["S6"]["transitions"] == len(lines)


def test_cost_cap_halts_the_run_and_writes_a_partial_artifact(
    video_with_audio: Path, mock_config: Config
) -> None:
    """The cap turns a bad threshold from a bill into a log line."""
    costed = mock_config.model_copy(deep=True)
    # Give the mock reasoner a real price, then cap below what a full run costs.
    costed.backends.reasoner.provider = "priced-mock"
    costed.limits.max_cost_usd_per_video = 0.02

    from stratum.config.pricing import PricingTable, Rates

    original = PricingTable.load

    def patched(path: Path | None = None) -> PricingTable:
        table = original(path)
        table.entries["priced-mock/*"] = Rates(usd_per_1k_tokens_in=0.01)
        return table

    PricingTable.load = staticmethod(patched)  # type: ignore[method-assign]
    try:
        from stratum.backends import registry

        registry.MOCK_PROVIDERS = frozenset({"mock", "priced-mock"})  # type: ignore[misc]
        result = process(VideoRef(path=video_with_audio), costed)
    finally:
        PricingTable.load = original  # type: ignore[method-assign]
        registry.MOCK_PROVIDERS = frozenset({"mock"})  # type: ignore[misc]

    assert result.cap_hit
    assert result.partial
    assert result.artifact_path.is_file(), "a capped run must still leave an artifact"

    artifact = _load_artifact(result.artifact_path)
    assert artifact.run.partial
    assert artifact.run.cost.cap_hit
    assert "S8" not in artifact.run.stages_completed


def test_failed_transitions_are_recorded_not_hidden(
    video_with_audio: Path, mock_config: Config
) -> None:
    """One bad keyframe must not destroy a run, and the gap must be visible."""
    flaky = mock_config.model_copy(deep=True)
    flaky.backends.reasoner.options = {"fail_every_n": 2}

    result = process(VideoRef(path=flaky and video_with_audio), flaky)
    artifact = _load_artifact(result.artifact_path)

    failed = [t for t in artifact.transitions if t.status.value == "failed"]
    assert failed, "the mock was told to fail and nothing failed"
    assert all(t.error for t in failed)
    assert result.stages_run == list(STAGE_ORDER), "the run must continue past a failure"

    # The gap becomes an open question rather than being silently smoothed over.
    assert any("could not be analysed" in q.text for q in artifact.open_questions)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


def test_chunks_carry_modality_inline_in_the_text(
    video_with_audio: Path, mock_config: Config
) -> None:
    """Retrieval layers strip metadata; the text survives. A model reading a
    retrieved chunk must see the confidence without a schema."""
    result = process(VideoRef(path=video_with_audio), mock_config)

    chunks = [
        Chunk.model_validate_json(line)
        for line in result.rundir.chunks.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert chunks

    kinds = {c.kind.value for c in chunks}
    assert "overview" in kinds
    assert "step" in kinds
    assert "open_question" in kinds

    tagged = [c for c in chunks if "[seen," in c.text or "[inferred," in c.text]
    assert tagged, "no chunk carried an inline modality tag"

    for chunk in chunks:
        assert chunk.source.run_id == result.run_id
        assert chunk.chunk_id.startswith("run_")


def test_markdown_renders_and_shows_provenance(video_with_audio: Path, mock_config: Config) -> None:
    """If a person reading the Markdown cannot tell measured from guessed, the
    document is dangerous - someone will buy 20 mm fittings."""
    result = process(VideoRef(path=video_with_audio), mock_config)
    markdown = result.rundir.artifact_md.read_text(encoding="utf-8")

    assert markdown.startswith("# ")
    assert "## Procedure" in markdown
    assert "## Open questions" in markdown
    assert "**seen**" in markdown or "**inferred**" in markdown
    assert "keyframes/kf_" in markdown


def test_markdown_sections_are_not_run_together(
    video_with_audio: Path, mock_config: Config
) -> None:
    """Jinja trims the newline after a block tag, so a template line ending in
    `{% endif %}` silently glues the next section onto the end of a list. It
    happened; this stops it happening again unnoticed."""
    result = process(VideoRef(path=video_with_audio), mock_config)
    lines = result.rundir.artifact_md.read_text(encoding="utf-8").splitlines()

    for index, line in enumerate(lines):
        if line.startswith("#") and index > 0:
            assert lines[index - 1].strip() == "", (
                f"heading {line!r} on line {index + 1} is not preceded by a blank line"
            )

    # And no list item should have absorbed the following heading.
    for line in lines:
        if line.startswith("- "):
            assert "##" not in line, f"list item swallowed a heading: {line!r}"


def test_artifact_json_is_the_canonical_model_and_round_trips(
    video_silent: Path, mock_config: Config
) -> None:
    result = process(VideoRef(path=video_silent), mock_config)
    raw = json.loads(result.artifact_path.read_text(encoding="utf-8"))

    artifact = Artifact.model_validate(raw)
    assert json.loads(artifact.model_dump_json()) == json.loads(
        Artifact.model_validate(json.loads(artifact.model_dump_json())).model_dump_json()
    )


def test_cost_is_visible_per_stage(video_with_audio: Path, mock_config: Config) -> None:
    result = process(VideoRef(path=video_with_audio), mock_config)
    artifact = _load_artifact(result.artifact_path)
    # Mocks are free, so the totals are zero — but the accounting must exist.
    assert artifact.run.cost.total_usd == 0.0
    assert artifact.run.cost.images > 0, "S6 sent images and they were not counted"
    assert artifact.run.cost.pricing_table_verified_on
