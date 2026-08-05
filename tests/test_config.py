"""Config and pricing tests."""

from __future__ import annotations

import datetime as dt

import pytest

from stratum.config.loader import load_config
from stratum.config.models import Config, Posture
from stratum.config.pricing import CostModel, PricingTable, Rates, Usage
from stratum.errors import ConfigError


def test_mock_posture_pins_all_three_slots() -> None:
    cfg = load_config(None, posture=Posture.MOCK, environ={})
    assert cfg.backends.transcriber.provider == "mock"
    assert cfg.backends.embedder.provider == "mock"
    assert cfg.backends.reasoner.provider == "mock"


def test_hybrid_leaves_the_reasoner_unpinned() -> None:
    """Q1 is unanswered. A posture that silently picked a model would be
    resolving an open question on Mehdi's behalf."""
    cfg = load_config(None, posture=Posture.HYBRID, environ={})
    assert cfg.backends.reasoner.provider is None
    assert cfg.backends.transcriber.provider == "faster-whisper"


def test_unknown_posture_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown posture"):
        load_config(None, posture="magic", environ={})


def test_env_overrides_use_double_underscore_nesting() -> None:
    cfg = load_config(
        None,
        posture=Posture.MOCK,
        environ={"STRATUM_PIPELINE__SAMPLE__SAMPLE_FPS": "4.5"},
    )
    assert cfg.pipeline.sample.sample_fps == 4.5


def test_env_values_are_yaml_coerced() -> None:
    cfg = load_config(
        None,
        posture=Posture.MOCK,
        environ={"STRATUM_PIPELINE__SELECT__USE_EMBEDDING_TIER": "false"},
    )
    assert cfg.pipeline.select.use_embedding_tier is False


def test_explicit_overrides_beat_the_environment() -> None:
    cfg = load_config(
        None,
        posture=Posture.MOCK,
        environ={"STRATUM_LIMITS__MAX_COST_USD_PER_VIDEO": "5"},
        overrides={"limits": {"max_cost_usd_per_video": 0.25}},
    )
    assert cfg.limits.max_cost_usd_per_video == 0.25


def test_threshold_floor_must_sit_below_the_starting_threshold() -> None:
    """The budget search lowers the threshold. A floor above the start would make
    the search a no-op and the cap unenforceable."""
    with pytest.raises(ConfigError):
        load_config(
            None,
            posture=Posture.MOCK,
            environ={},
            overrides={"pipeline": {"select": {"threshold_search_floor": 0.99}}},
        )


def test_snapshot_carries_no_secret() -> None:
    cfg = load_config(
        None,
        posture=Posture.MOCK,
        environ={},
        overrides={"backends": {"reasoner": {"api_key_env": "SOME_PROVIDER_KEY"}}},
    )
    blob = repr(cfg.snapshot())
    assert "SOME_PROVIDER_KEY" in blob  # the NAME is fine
    assert "sk-" not in blob


# -- stage fingerprints ----------------------------------------------------


def _cfg(**overrides: object) -> Config:
    return load_config(None, posture=Posture.MOCK, environ={}, overrides=overrides or None)


def test_changing_a_select_threshold_does_not_invalidate_transcription() -> None:
    """Transcription is expensive and almost never needs redoing. Protect it."""
    base = _cfg()
    changed = _cfg(pipeline={"select": {"phash_threshold": 12}})

    assert base.stage_fingerprint("S2") == changed.stage_fingerprint("S2")
    assert base.stage_fingerprint("S3") == changed.stage_fingerprint("S3")
    assert base.stage_fingerprint("S4") != changed.stage_fingerprint("S4")


def test_changing_the_prompt_invalidates_traverse_only() -> None:
    base = _cfg()
    changed = _cfg(prompts={"traverse": "s6_traverse.v2.md"})

    assert base.stage_fingerprint("S6") != changed.stage_fingerprint("S6")
    assert base.stage_fingerprint("S4") == changed.stage_fingerprint("S4")
    assert base.stage_fingerprint("S7") == changed.stage_fingerprint("S7")


def test_changing_sample_fps_invalidates_sampling() -> None:
    base = _cfg()
    changed = _cfg(pipeline={"sample": {"sample_fps": 1.0}})
    assert base.stage_fingerprint("S3") != changed.stage_fingerprint("S3")
    assert base.stage_fingerprint("S2") == changed.stage_fingerprint("S2")


# -- pricing ---------------------------------------------------------------


def test_unknown_pricing_raises_rather_than_defaulting_to_free() -> None:
    """A silent zero would make an expensive run look free."""
    table = PricingTable.load()
    with pytest.raises(ConfigError, match="no pricing entry"):
        table.cost_model("some-provider-nobody-added", "some-model")


def test_wildcard_entry_covers_a_provider() -> None:
    table = PricingTable.load()
    model = table.cost_model("mock", "anything-at-all")
    assert model.is_free


def test_image_tokens_are_billed_as_input_tokens() -> None:
    model = CostModel(
        provider="p",
        model="m",
        rates=Rates(usd_per_1k_tokens_in=1.0, image_tokens=500),
    )
    # 1000 text tokens + 2 images x 500 image-tokens = 2000 billed input tokens
    assert model.price(Usage(tokens_in=1000, images=2)) == pytest.approx(2.0)


def test_staleness_warning_fires_past_the_threshold() -> None:
    table = PricingTable("2026-01-01", {})
    assert table.staleness_warning(90, today=dt.date(2026, 2, 1)) is None
    warning = table.staleness_warning(90, today=dt.date(2026, 8, 1))
    assert warning is not None and "verified" in warning


def test_missing_verified_on_is_reported_as_unverifiable() -> None:
    table = PricingTable(None, {})
    warning = table.staleness_warning(90)
    assert warning is not None and "unverifiable" in warning
