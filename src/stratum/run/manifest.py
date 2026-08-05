"""`manifest.json` — run state and the thing that makes resumability correct.

Each stage records its completion together with a fingerprint of the config slice
it depends on. Re-running skips completed stages whose fingerprint is unchanged;
a changed fingerprint invalidates that stage and every stage after it.

S2 is protected. Transcription is expensive and almost never needs redoing, so a
changed transcriber fingerprint raises a warning and keeps the existing transcript
unless the caller explicitly asks for it to be redone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from stratum.schema.common import StratumModel

STAGE_ORDER: tuple[str, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")

STAGE_NAMES: dict[str, str] = {
    "S0": "ingest",
    "S1": "audio",
    "S2": "transcribe",
    "S3": "sample",
    "S4": "select",
    "S5": "weigh",
    "S6": "traverse",
    "S7": "synthesise",
    "S8": "emit",
}

PROTECTED_STAGES: frozenset[str] = frozenset({"S2"})
"""Stages whose output is expensive enough that invalidation requires an explicit ask."""


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageRecord(StratumModel):
    stage: str
    name: str
    status: StageStatus = StageStatus.PENDING
    fingerprint: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    cost_usd: float = 0.0
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


class Manifest(StratumModel):
    run_id: str
    stratum_version: str
    schema_version: str
    created_at: datetime
    updated_at: datetime
    source_path: str
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False
    cap_hit: bool = False

    # -- construction -------------------------------------------------------

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        source_path: str,
        stratum_version: str,
        schema_version: str,
        config_snapshot: dict[str, Any],
    ) -> Manifest:
        now = datetime.now(UTC)
        return cls(
            run_id=run_id,
            stratum_version=stratum_version,
            schema_version=schema_version,
            created_at=now,
            updated_at=now,
            source_path=source_path,
            config_snapshot=config_snapshot,
            stages={s: StageRecord(stage=s, name=STAGE_NAMES[s]) for s in STAGE_ORDER},
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now(UTC)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    # -- stage state --------------------------------------------------------

    def record(self, stage: str) -> StageRecord:
        if stage not in self.stages:
            self.stages[stage] = StageRecord(stage=stage, name=STAGE_NAMES.get(stage, stage))
        return self.stages[stage]

    def start(self, stage: str, fingerprint: str) -> None:
        rec = self.record(stage)
        rec.status = StageStatus.RUNNING
        rec.fingerprint = fingerprint
        rec.started_at = datetime.now(UTC)
        rec.finished_at = None
        rec.duration_s = None
        rec.error = None

    def complete(
        self, stage: str, *, cost_usd: float = 0.0, stats: dict[str, Any] | None = None
    ) -> None:
        rec = self.record(stage)
        rec.status = StageStatus.COMPLETED
        rec.finished_at = datetime.now(UTC)
        if rec.started_at:
            rec.duration_s = (rec.finished_at - rec.started_at).total_seconds()
        rec.cost_usd = cost_usd
        rec.error = None
        if stats:
            rec.stats = stats

    def fail(self, stage: str, error: str) -> None:
        rec = self.record(stage)
        rec.status = StageStatus.FAILED
        rec.finished_at = datetime.now(UTC)
        if rec.started_at:
            rec.duration_s = (rec.finished_at - rec.started_at).total_seconds()
        rec.error = error

    def skip(self, stage: str, reason: str) -> None:
        rec = self.record(stage)
        rec.status = StageStatus.SKIPPED
        rec.finished_at = datetime.now(UTC)
        rec.stats = {**rec.stats, "skipped_because": reason}

    def invalidate_from(self, stage: str) -> list[str]:
        """Reset `stage` and everything after it. Returns what was reset."""
        if stage not in STAGE_ORDER:
            raise ValueError(f"unknown stage {stage!r}")
        start = STAGE_ORDER.index(stage)
        reset: list[str] = []
        for s in STAGE_ORDER[start:]:
            rec = self.record(s)
            if rec.status is not StageStatus.PENDING:
                reset.append(s)
            rec.status = StageStatus.PENDING
            rec.started_at = None
            rec.finished_at = None
            rec.duration_s = None
            rec.cost_usd = 0.0
            rec.error = None
            rec.stats = {}
        return reset

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- queries ------------------------------------------------------------

    def is_satisfied(self, stage: str, fingerprint: str) -> bool:
        rec = self.stages.get(stage)
        if rec is None:
            return False
        if rec.status not in (StageStatus.COMPLETED, StageStatus.SKIPPED):
            return False
        return rec.fingerprint == fingerprint

    @property
    def stages_completed(self) -> list[str]:
        return [
            s
            for s in STAGE_ORDER
            if self.stages.get(s)
            and self.stages[s].status in (StageStatus.COMPLETED, StageStatus.SKIPPED)
        ]

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.stages.values())

    def cost_by_stage(self) -> dict[str, float]:
        return {
            STAGE_NAMES.get(s, s): self.stages[s].cost_usd
            for s in STAGE_ORDER
            if s in self.stages and self.stages[s].cost_usd
        }


def plan_stages(
    manifest: Manifest,
    fingerprints: dict[str, str],
    *,
    force_from: str | None = None,
    allow_retranscribe: bool = False,
) -> tuple[list[str], list[str]]:
    """Decide which stages to run.

    Returns `(to_run, notes)`. A stage runs if it is not satisfied, or if any
    earlier stage is going to run — downstream state derived from regenerated
    input is not valid state.
    """
    notes: list[str] = []
    to_run: list[str] = []
    dirty = False

    forced_index = STAGE_ORDER.index(force_from) if force_from else None

    for index, stage in enumerate(STAGE_ORDER):
        fingerprint = fingerprints[stage]
        forced = forced_index is not None and index >= forced_index

        if forced:
            to_run.append(stage)
            dirty = True
            continue

        satisfied = manifest.is_satisfied(stage, fingerprint)

        if satisfied and not dirty:
            continue

        if satisfied and dirty:
            to_run.append(stage)
            notes.append(f"{stage} re-runs: an earlier stage was re-run")
            continue

        rec = manifest.stages.get(stage)
        changed_config = (
            rec is not None
            and rec.status is StageStatus.COMPLETED
            and rec.fingerprint != fingerprint
        )

        if changed_config and stage in PROTECTED_STAGES and not allow_retranscribe:
            # Protect the expensive thing. Keep the existing output, say so loudly.
            notes.append(
                f"{stage} ({STAGE_NAMES[stage]}) config changed but its output is "
                "protected and was kept; pass --retranscribe to redo it"
            )
            manifest.warn(f"{stage} config changed after completion; existing output retained")
            continue

        if changed_config:
            notes.append(f"{stage} re-runs: its config changed")

        to_run.append(stage)
        dirty = True

    return to_run, notes
