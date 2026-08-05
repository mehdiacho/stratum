"""Exception types. Kept in one place so the CLI can map them to exit codes."""

from __future__ import annotations


class StratumError(Exception):
    """Base for everything this package raises deliberately."""


class ConfigError(StratumError):
    pass


class DependencyMissing(StratumError):
    """ffmpeg/ffprobe absent, or an optional extra was not installed."""


class ProbeError(StratumError):
    pass


class StageError(StratumError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


class BackendError(StratumError):
    pass


class SchemaViolation(BackendError):
    """A model returned something that would not parse, after repair attempts."""


class CostCapExceeded(StratumError):
    """`max_cost_usd_per_video` is a hard stop, not a warning.

    Raised mid-run; the orchestrator catches it and writes a partial artifact
    containing everything completed so far. This exists because the failure mode
    of a sequential per-frame pipeline is a bad threshold retaining 900 keyframes
    instead of 80, discovered afterwards on the bill.
    """

    def __init__(self, spent_usd: float, cap_usd: float) -> None:
        super().__init__(f"cost cap hit: spent ${spent_usd:.4f} of ${cap_usd:.4f}")
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd


class WallTimeExceeded(StratumError):
    def __init__(self, elapsed_s: float, cap_s: float) -> None:
        super().__init__(f"wall-time cap hit: {elapsed_s:.0f}s of {cap_s:.0f}s")
        self.elapsed_s = elapsed_s
        self.cap_s = cap_s
