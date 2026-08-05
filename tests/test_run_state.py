"""Manifest, resume planning and the cost ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from stratum.config.pricing import CostModel, Rates, Usage
from stratum.errors import CostCapExceeded
from stratum.run.ledger import CostLedger
from stratum.run.manifest import STAGE_ORDER, Manifest, StageStatus, plan_stages

FINGERPRINTS = {stage: f"fp-{stage}" for stage in STAGE_ORDER}


def _manifest() -> Manifest:
    return Manifest.new(
        run_id="deadbeef",
        source_path="x.mp4",
        stratum_version="0.1.0",
        schema_version="1.0",
        config_snapshot={},
    )


def _complete_all(manifest: Manifest) -> None:
    for stage in STAGE_ORDER:
        manifest.start(stage, FINGERPRINTS[stage])
        manifest.complete(stage)


# -- planning --------------------------------------------------------------


def test_a_fresh_run_runs_everything() -> None:
    to_run, _ = plan_stages(_manifest(), FINGERPRINTS)
    assert to_run == list(STAGE_ORDER)


def test_a_completed_run_runs_nothing() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    to_run, _ = plan_stages(manifest, FINGERPRINTS)
    assert to_run == []


def test_changing_select_config_invalidates_select_onward_only() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    changed = {**FINGERPRINTS, "S4": "different"}

    to_run, notes = plan_stages(manifest, changed)

    assert to_run == ["S4", "S5", "S6", "S7", "S8"]
    assert any("S4 re-runs" in n for n in notes)


def test_transcription_is_protected_from_a_config_change() -> None:
    """S2 is expensive and almost never needs redoing. A changed fingerprint
    warns and keeps the existing transcript."""
    manifest = _manifest()
    _complete_all(manifest)
    changed = {**FINGERPRINTS, "S2": "different-asr"}

    to_run, notes = plan_stages(manifest, changed)

    assert "S2" not in to_run
    assert to_run == []
    assert any("protected" in n for n in notes)
    assert manifest.warnings


def test_retranscribe_overrides_the_protection() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    changed = {**FINGERPRINTS, "S2": "different-asr"}

    to_run, _ = plan_stages(manifest, changed, allow_retranscribe=True)

    assert to_run == ["S2", "S3", "S4", "S5", "S6", "S7", "S8"]


def test_force_from_reruns_that_stage_and_everything_after() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    to_run, _ = plan_stages(manifest, FINGERPRINTS, force_from="S6")
    assert to_run == ["S6", "S7", "S8"]


def test_an_incomplete_stage_forces_everything_downstream() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    manifest.stages["S6"].status = StageStatus.FAILED

    to_run, _ = plan_stages(manifest, FINGERPRINTS)

    assert to_run == ["S6", "S7", "S8"]


def test_invalidate_from_resets_downstream_records() -> None:
    manifest = _manifest()
    _complete_all(manifest)
    reset = manifest.invalidate_from("S5")
    assert reset == ["S5", "S6", "S7", "S8"]
    assert manifest.stages["S4"].status is StageStatus.COMPLETED
    assert manifest.stages["S5"].status is StageStatus.PENDING


def test_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest.start("S0", "fp")
    manifest.complete("S0", cost_usd=0.5, stats={"candidates": 12})
    manifest.save(tmp_path / "manifest.json")

    loaded = Manifest.load(tmp_path / "manifest.json")

    assert loaded.stages["S0"].stats == {"candidates": 12}
    assert loaded.stages["S0"].cost_usd == 0.5
    assert loaded.stages_completed == ["S0"]


# -- ledger ----------------------------------------------------------------

PAID = CostModel(
    provider="p", model="m", rates=Rates(usd_per_1k_tokens_in=1.0, usd_per_1k_tokens_out=2.0)
)


def test_ledger_totals_and_per_stage_breakdown(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.db")
    ledger.record(stage="S6", cost_model=PAID, usage=Usage(tokens_in=1000, tokens_out=500))
    ledger.record(stage="S6", cost_model=PAID, usage=Usage(tokens_in=1000))
    ledger.record(stage="S7", cost_model=PAID, usage=Usage(tokens_out=1000))

    totals = ledger.totals()
    assert totals.calls == 3
    assert totals.usd == pytest.approx(1.0 + 1.0 + 1.0 + 2.0)
    assert ledger.stage_totals("S6").usd == pytest.approx(3.0)
    assert set(ledger.by_stage()) == {"S6", "S7"}


def test_cost_cap_is_a_hard_stop(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.db", cap_usd=1.5)
    ledger.record(stage="S6", cost_model=PAID, usage=Usage(tokens_in=1000))

    with pytest.raises(CostCapExceeded) as excinfo:
        ledger.record(stage="S6", cost_model=PAID, usage=Usage(tokens_in=1000))

    assert excinfo.value.cap_usd == 1.5
    # The offending call is still recorded: the artifact must reflect work that
    # was actually paid for.
    assert ledger.totals().calls == 2


def test_resetting_a_stage_stops_a_resume_double_counting(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.db")
    ledger.record(stage="S6", cost_model=PAID, usage=Usage(tokens_in=1000))
    ledger.record(stage="S2", cost_model=PAID, usage=Usage(tokens_in=1000))

    ledger.reset_stage("S6")

    assert ledger.stage_totals("S6").calls == 0
    assert ledger.stage_totals("S2").calls == 1
