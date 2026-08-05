"""The orchestrator.

Runs the stages in order and can be told to start from any of them. It owns the
three things no individual stage should: what to skip on a resume, when to stop
because the run has cost too much, and writing a partial artifact when it does.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stratum import __version__
from stratum.config.models import Config
from stratum.config.pricing import PricingTable
from stratum.errors import CostCapExceeded, StageError, WallTimeExceeded
from stratum.pipeline.context import RunContext
from stratum.pipeline.prompts import prompts_dir
from stratum.pipeline.stages import STAGE_MODULES
from stratum.pipeline.stages.s8_emit import build_artifact, write_outputs
from stratum.run.directory import RunDir, content_hash
from stratum.run.ledger import CostLedger
from stratum.run.manifest import STAGE_ORDER, Manifest, plan_stages
from stratum.schema.common import SCHEMA_VERSION
from stratum.sources.protocols import VideoRef

log = logging.getLogger("stratum")


@dataclass
class RunResult:
    run_id: str
    rundir: RunDir
    stages_run: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_by_stage: dict[str, float] = field(default_factory=dict)
    wall_time_s: float = 0.0
    partial: bool = False
    cap_hit: bool = False
    stage_stats: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def artifact_path(self) -> Path:
        return self.rundir.artifact_json


def process(
    video: VideoRef,
    config: Config,
    *,
    runs_dir: Path | None = None,
    force_from: str | None = None,
    allow_retranscribe: bool = False,
    only_stages: set[str] | None = None,
) -> RunResult:
    """Process one video end to end.

    Idempotent: the same file resolves to the same run directory by content hash,
    and completed stages whose config is unchanged are skipped.
    """
    started = time.monotonic()

    run_id = content_hash(video.path)
    root = (runs_dir or config.paths.runs_dir).expanduser()
    rundir = RunDir.for_run(root, run_id).create()

    pricing = PricingTable.load(config.paths.pricing_table)
    stale = pricing.staleness_warning(config.limits.pricing_staleness_warn_days)

    if rundir.manifest.is_file():
        manifest = Manifest.load(rundir.manifest)
        manifest.config_snapshot = config.snapshot()
    else:
        manifest = Manifest.new(
            run_id=run_id,
            source_path=str(video.path),
            stratum_version=__version__,
            schema_version=SCHEMA_VERSION,
            config_snapshot=config.snapshot(),
        )
    manifest.partial = False
    manifest.cap_hit = False
    if stale:
        manifest.warn(stale)

    ledger = CostLedger(rundir.ledger, cap_usd=config.limits.max_cost_usd_per_video)

    ctx = RunContext(
        config=config,
        rundir=rundir,
        manifest=manifest,
        ledger=ledger,
        pricing=pricing,
        video=video,
        prompts_dir=prompts_dir(config.prompts.directory),
    )

    fingerprints = {stage: config.stage_fingerprint(stage) for stage in STAGE_ORDER}
    to_run, notes = plan_stages(
        manifest,
        fingerprints,
        force_from=force_from,
        allow_retranscribe=allow_retranscribe,
    )
    if only_stages:
        to_run = [s for s in to_run if s in only_stages]

    result = RunResult(run_id=run_id, rundir=rundir, notes=list(notes))
    result.stages_skipped = [s for s in STAGE_ORDER if s not in to_run]

    for note in notes:
        log.info(note)

    manifest.save(rundir.manifest)

    for stage in to_run:
        elapsed = time.monotonic() - started
        if elapsed > config.limits.max_wall_time_s:
            manifest.partial = True
            result.partial = True
            manifest.warn(str(WallTimeExceeded(elapsed, config.limits.max_wall_time_s)))
            log.warning("wall-time cap reached before %s; stopping", stage)
            break

        ctx.stage = stage
        # Clear this stage's ledger rows first so a resume never double-counts.
        ledger.reset_stage(stage)
        manifest.start(stage, fingerprints[stage])
        manifest.save(rundir.manifest)

        module = STAGE_MODULES[stage]
        try:
            stats = module.run(ctx)
        except CostCapExceeded as exc:
            manifest.fail(stage, str(exc))
            manifest.partial = True
            manifest.cap_hit = True
            manifest.warn(
                f"{exc}. The run stopped here and this artifact is partial — nothing "
                "was truncated silently."
            )
            result.partial = True
            result.cap_hit = True
            log.error("%s", exc)
            break
        except Exception as exc:
            manifest.fail(stage, f"{type(exc).__name__}: {exc}")
            manifest.save(rundir.manifest)
            raise StageError(stage, f"{type(exc).__name__}: {exc}") from exc

        stage_cost = ledger.stage_totals(stage).usd
        manifest.complete(stage, cost_usd=stage_cost, stats=stats)
        manifest.save(rundir.manifest)
        result.stages_run.append(stage)
        result.stage_stats[stage] = stats

    # A partial run still gets an artifact, containing everything completed so far.
    if result.partial and rundir.video_meta.is_file():
        ctx.stage = "S8"
        try:
            artifact = build_artifact(ctx, partial=True)
            write_outputs(ctx, artifact)
            log.info("wrote a partial artifact with everything completed so far")
        except Exception as exc:
            log.warning("could not write a partial artifact: %s", exc)

    totals = ledger.totals()
    result.cost_usd = totals.usd
    result.cost_by_stage = {s: t.usd for s, t in ledger.by_stage().items()}
    result.wall_time_s = time.monotonic() - started
    result.warnings = list(manifest.warnings)

    manifest.save(rundir.manifest)
    return result
