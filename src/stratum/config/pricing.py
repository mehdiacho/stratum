"""The pricing table and the cost model built from it.

Cost that is not visible is cost that is not controlled. Every backend call
returns a `Usage`; the ledger turns it into money using one of these.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import Field

from stratum.errors import ConfigError
from stratum.schema.common import StratumModel

_BUNDLED = Path(__file__).with_name("pricing.yaml")


class Rates(StratumModel):
    usd_per_1k_tokens_in: float = 0.0
    usd_per_1k_tokens_out: float = 0.0
    usd_per_image: float = 0.0
    image_tokens: int = Field(
        default=0,
        description="Per-image input tokens, for providers that bill images as tokens "
        "rather than flat. Per-image cost dominates the bill in S6 — one image per "
        "call, eighty calls per video.",
    )
    usd_per_audio_minute: float = 0.0


@dataclass(frozen=True)
class Usage:
    """What one backend call consumed. Returned by every metered call."""

    tokens_in: int = 0
    tokens_out: int = 0
    images: int = 0
    audio_seconds: float = 0.0
    wall_time_s: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            images=self.images + other.images,
            audio_seconds=self.audio_seconds + other.audio_seconds,
            wall_time_s=self.wall_time_s + other.wall_time_s,
        )


@dataclass(frozen=True)
class CostModel:
    """A provider/model pair plus its rates. Attached to every backend."""

    provider: str
    model: str | None
    rates: Rates
    verified_on: str | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model or '*'}"

    @property
    def is_free(self) -> bool:
        r = self.rates
        return (
            r.usd_per_1k_tokens_in == 0.0
            and r.usd_per_1k_tokens_out == 0.0
            and r.usd_per_image == 0.0
            and r.usd_per_audio_minute == 0.0
        )

    def price(self, usage: Usage) -> float:
        r = self.rates
        billed_tokens_in = usage.tokens_in + usage.images * r.image_tokens
        return (
            billed_tokens_in / 1000.0 * r.usd_per_1k_tokens_in
            + usage.tokens_out / 1000.0 * r.usd_per_1k_tokens_out
            + usage.images * r.usd_per_image
            + usage.audio_seconds / 60.0 * r.usd_per_audio_minute
        )


class PricingTable:
    def __init__(self, verified_on: str | None, entries: dict[str, Rates]) -> None:
        self.verified_on = verified_on
        self.entries = entries

    @classmethod
    def load(cls, path: Path | None = None) -> PricingTable:
        target = path or _BUNDLED
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise ConfigError(f"cannot read pricing table {target}: {exc}") from exc
        entries: dict[str, Rates] = {}
        for key, value in (raw.get("entries") or {}).items():
            entries[key] = Rates.model_validate(value or {})
        return cls(verified_on=raw.get("verified_on"), entries=entries)

    def cost_model(self, provider: str, model: str | None) -> CostModel:
        rates = (self.entries.get(f"{provider}/{model}") if model else None) or self.entries.get(
            f"{provider}/*"
        )
        if rates is None:
            # Unknown pricing is reported as unknown, not as free. A silent zero
            # here would make an expensive run look free.
            raise ConfigError(
                f"no pricing entry for {provider}/{model or '*'}. Add one to "
                "pricing.yaml with a verified_on date in the same commit as the adapter."
            )
        return CostModel(provider=provider, model=model, rates=rates, verified_on=self.verified_on)

    def staleness_days(self, *, today: dt.date | None = None) -> int | None:
        if not self.verified_on:
            return None
        try:
            verified = dt.date.fromisoformat(self.verified_on)
        except ValueError:
            return None
        return ((today or dt.date.today()) - verified).days

    def staleness_warning(
        self, warn_after_days: int, *, today: dt.date | None = None
    ) -> str | None:
        days = self.staleness_days(today=today)
        if days is None:
            return "pricing table has no verified_on date; cost figures are unverifiable"
        if days > warn_after_days:
            return (
                f"pricing table was last verified {days} days ago "
                f"({self.verified_on}); cost figures may be wrong"
            )
        return None
