"""The cost ledger.

Every model call is metered. Every run prints a per-stage breakdown. The per-video
cap is a hard stop, not a warning.

SQLite rather than a JSON blob because the ledger is appended to eighty times in a
single stage and must survive a kill at any point between two of them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stratum.config.pricing import CostModel, Usage
from stratum.errors import CostCapExceeded

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    stage         TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    model         TEXT,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    images        INTEGER NOT NULL DEFAULT 0,
    audio_seconds REAL    NOT NULL DEFAULT 0,
    wall_time_s   REAL    NOT NULL DEFAULT 0,
    usd           REAL    NOT NULL DEFAULT 0,
    unit          TEXT
);
CREATE INDEX IF NOT EXISTS calls_stage ON calls(stage);
"""


@dataclass(frozen=True)
class LedgerTotals:
    usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    images: int = 0
    audio_seconds: float = 0.0
    calls: int = 0


class CostLedger:
    """Append-only within a stage; a stage that re-runs clears its own rows first
    so a resume never double-counts."""

    def __init__(self, path: Path, *, cap_usd: float | None = None) -> None:
        self.path = path
        self.cap_usd = cap_usd
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- writing ------------------------------------------------------------

    def record(
        self,
        *,
        stage: str,
        cost_model: CostModel,
        usage: Usage,
        unit: str | None = None,
    ) -> float:
        """Meter one call. Returns its cost, and raises once the cap is crossed.

        The cap is checked *after* recording, so the artifact reflects work that
        was actually paid for. Raising before recording would lose the last call.
        """
        usd = cost_model.price(usage)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO calls (ts, stage, provider, model, tokens_in, tokens_out, "
                "images, audio_seconds, wall_time_s, usd, unit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    stage,
                    cost_model.provider,
                    cost_model.model,
                    usage.tokens_in,
                    usage.tokens_out,
                    usage.images,
                    usage.audio_seconds,
                    usage.wall_time_s,
                    usd,
                    unit,
                ),
            )
            conn.commit()
        if self.cap_usd is not None:
            spent = self.totals().usd
            if spent > self.cap_usd:
                raise CostCapExceeded(spent, self.cap_usd)
        return usd

    def reset_stage(self, stage: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM calls WHERE stage = ?", (stage,))
            conn.commit()

    # -- reading ------------------------------------------------------------

    def _totals_query(self, where: str = "", params: tuple[object, ...] = ()) -> LedgerTotals:
        sql = (
            "SELECT COALESCE(SUM(usd),0) AS usd, COALESCE(SUM(tokens_in),0) AS ti, "
            "COALESCE(SUM(tokens_out),0) AS to_, COALESCE(SUM(images),0) AS im, "
            "COALESCE(SUM(audio_seconds),0) AS aud, COUNT(*) AS n FROM calls " + where
        )
        with closing(self._connect()) as conn:
            row = conn.execute(sql, params).fetchone()
        return LedgerTotals(
            usd=float(row["usd"]),
            tokens_in=int(row["ti"]),
            tokens_out=int(row["to_"]),
            images=int(row["im"]),
            audio_seconds=float(row["aud"]),
            calls=int(row["n"]),
        )

    def totals(self) -> LedgerTotals:
        return self._totals_query()

    def stage_totals(self, stage: str) -> LedgerTotals:
        return self._totals_query("WHERE stage = ?", (stage,))

    def by_stage(self) -> dict[str, LedgerTotals]:
        with closing(self._connect()) as conn:
            stages = [r["stage"] for r in conn.execute("SELECT DISTINCT stage FROM calls")]
        return {s: self.stage_totals(s) for s in stages}

    def iter_calls(self, stage: str | None = None) -> Iterator[sqlite3.Row]:
        sql = "SELECT * FROM calls"
        params: tuple[object, ...] = ()
        if stage:
            sql += " WHERE stage = ?"
            params = (stage,)
        sql += " ORDER BY id"
        with closing(self._connect()) as conn:
            yield from conn.execute(sql, params).fetchall()

    def remaining_usd(self) -> float | None:
        if self.cap_usd is None:
            return None
        return max(0.0, self.cap_usd - self.totals().usd)

    def check_cap(self) -> None:
        """Explicit pre-flight check, for callers that want to stop before spending."""
        if self.cap_usd is None:
            return
        spent = self.totals().usd
        if spent > self.cap_usd:
            raise CostCapExceeded(spent, self.cap_usd)
