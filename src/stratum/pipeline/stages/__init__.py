"""The nine stages.

Each reads from the run directory and writes back to it. Each is independently
resumable and independently testable. No stage calls the next one.
"""

from types import ModuleType

from stratum.pipeline.stages import (
    s0_ingest,
    s1_audio,
    s2_transcribe,
    s3_sample,
    s4_select,
    s5_weigh,
    s6_traverse,
    s7_synthesise,
    s8_emit,
)

STAGE_MODULES: dict[str, ModuleType] = {
    "S0": s0_ingest,
    "S1": s1_audio,
    "S2": s2_transcribe,
    "S3": s3_sample,
    "S4": s4_select,
    "S5": s5_weigh,
    "S6": s6_traverse,
    "S7": s7_synthesise,
    "S8": s8_emit,
}

__all__ = ["STAGE_MODULES"]
