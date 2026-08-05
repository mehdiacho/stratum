"""S8 — Emit.

Assemble the canonical artifact from everything in the run directory, then render
the two views of it.

`artifact.json` is the model. `artifact.md` and `chunks.jsonl` are derived, and
regenerating them costs nothing — which is the point of keeping the canonical form
separate from both.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from stratum import __version__
from stratum.pipeline.chunking import build_chunks
from stratum.pipeline.context import RunContext
from stratum.pipeline.entities import EntityRegistry
from stratum.pipeline.render import render_markdown
from stratum.schema.artifact import (
    Artifact,
    CostBreakdown,
    ModalityProfile,
    RunInfo,
    VideoSource,
)
from stratum.schema.common import SCHEMA_VERSION
from stratum.schema.media import SourceKind, VideoMeta
from stratum.schema.synthesis import SynthesisResult
from stratum.schema.transition import Transition

STAGE = "S8"


def _load_transitions(ctx: RunContext) -> list[Transition]:
    path = ctx.rundir.transitions
    if not path.is_file():
        return []
    return [
        Transition.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_artifact(
    ctx: RunContext, *, partial: bool = False, stages_completed: list[str] | None = None
) -> Artifact:
    """Assemble the canonical model. Pure enough to be called for a partial run
    after a cost cap, which is exactly when it matters most.

    `stages_completed` is overridable because S8 builds the artifact from inside
    S8: the manifest does not record emit as complete until the orchestrator says
    so, and an artifact that omits the stage that wrote it is just wrong.
    """
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    profile = ctx.read_model_or_none(ctx.rundir.modality_profile, ModalityProfile)
    synthesis = ctx.read_model_or_none(ctx.rundir.synthesis, SynthesisResult) or SynthesisResult()
    registry = EntityRegistry.load(ctx.rundir.entities)
    transitions = _load_transitions(ctx)

    if profile is None:
        # A run that stopped before S5 still gets an artifact. Saying "unclassified"
        # is honest; inventing a class would not be.
        profile = ModalityProfile(
            **{"class": "balanced"},
            note="Modality profiling did not run; this classification is a placeholder.",
        )

    totals = ctx.ledger.totals()
    by_stage = {stage: round(t.usd, 6) for stage, t in ctx.ledger.by_stage().items() if t.usd}

    source = VideoSource(
        kind=SourceKind.LOCAL_FILE,
        filename=meta.filename,
        duration_s=meta.duration_s,
        fps_avg=meta.fps_avg,
        resolution=(meta.display_width, meta.display_height),
        content_hash=f"sha256:{meta.content_hash}",
        rotation=meta.rotation,
        origin=ctx.video.origin,
        title=ctx.video.title,
        description=ctx.video.description,
        author=ctx.video.author,
        published_at=ctx.video.published_at,
        fetched_at=ctx.video.fetched_at,
    )

    run_info = RunInfo(
        id=ctx.rundir.run_id,
        created_at=ctx.manifest.created_at,
        completed_at=datetime.now(UTC),
        stratum_version=__version__,
        source=source,
        config_snapshot=ctx.config.snapshot(),
        cost=CostBreakdown(
            total_usd=round(totals.usd, 6),
            by_stage=by_stage,
            tokens_in=totals.tokens_in,
            tokens_out=totals.tokens_out,
            images=totals.images,
            audio_seconds=round(totals.audio_seconds, 2),
            pricing_table_verified_on=ctx.pricing.verified_on,
            cap_usd=ctx.ledger.cap_usd,
            cap_hit=ctx.manifest.cap_hit,
        ),
        stages_completed=stages_completed
        if stages_completed is not None
        else ctx.manifest.stages_completed,
        warnings=list(ctx.manifest.warnings),
        partial=partial or ctx.manifest.partial,
    )

    return Artifact(
        schema_version=SCHEMA_VERSION,
        run=run_info,
        modality_profile=profile,
        overview=synthesis.overview,
        entities=registry.all(),
        transitions=transitions,
        procedure=synthesis.procedure,
        materials=synthesis.materials,
        open_questions=synthesis.open_questions,
        conflicts=synthesis.conflicts,
    )


def write_outputs(ctx: RunContext, artifact: Artifact) -> dict[str, Any]:
    cfg = ctx.config.pipeline.emit

    ctx.rundir.artifact_json.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )

    markdown_written = False
    if cfg.write_markdown:
        ctx.rundir.artifact_md.write_text(
            render_markdown(artifact, template_name=cfg.markdown_template), encoding="utf-8"
        )
        markdown_written = True

    chunk_count = 0
    if cfg.write_chunks:
        chunks = build_chunks(artifact, include_transitions=cfg.chunk_transitions)
        with ctx.rundir.chunks.open("w", encoding="utf-8") as sink:
            for chunk in chunks:
                sink.write(json.dumps(chunk.model_dump(mode="json"), separators=(",", ":")))
                sink.write("\n")
        chunk_count = len(chunks)

    return {
        "artifact_json": True,
        "artifact_md": markdown_written,
        "chunks": chunk_count,
        "transitions": len(artifact.transitions),
        "entities": len(artifact.entities),
        "steps": len(artifact.procedure),
    }


def run(ctx: RunContext) -> dict[str, Any]:
    completed = [*ctx.manifest.stages_completed, STAGE]
    artifact = build_artifact(ctx, stages_completed=completed)
    stats = write_outputs(ctx, artifact)
    ctx.info(
        f"emitted artifact.json, artifact.md and {stats['chunks']} chunks to {ctx.rundir.root}"
    )
    return stats
