"""The pipeline: context, stages, orchestration, rendering and chunking."""

from stratum.pipeline.context import RunContext
from stratum.pipeline.orchestrator import RunResult, process

__all__ = ["RunContext", "RunResult", "process"]
