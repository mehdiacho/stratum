"""What every stage receives.

Stages are functions of a context. They read from the run directory and write back
to it; no stage calls the next one. The orchestrator runs them in order and can be
told to start from any stage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from stratum.backends.protocols import Embedder, Reasoner, Transcriber
from stratum.backends.registry import build_embedder, build_reasoner, build_transcriber
from stratum.config.models import Config
from stratum.config.pricing import PricingTable, Usage
from stratum.run.directory import RunDir
from stratum.run.ledger import CostLedger
from stratum.run.manifest import Manifest
from stratum.sources.protocols import VideoRef

log = logging.getLogger("stratum")

T = TypeVar("T", bound=BaseModel)


@dataclass
class RunContext:
    config: Config
    rundir: RunDir
    manifest: Manifest
    ledger: CostLedger
    pricing: PricingTable
    video: VideoRef
    prompts_dir: Path
    stage: str = ""
    _transcriber: Transcriber | None = field(default=None, repr=False)
    _embedder: Embedder | None = field(default=None, repr=False)
    _reasoner: Reasoner | None = field(default=None, repr=False)

    # -- backends, built on first use --------------------------------------
    #
    # Lazy so that a run which skips transcription (silent video) never
    # constructs an ASR backend, and a posture with an unpinned slot only fails
    # if a stage actually needs that slot.

    @property
    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            self._transcriber = build_transcriber(self.config, self.pricing)
        return self._transcriber

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = build_embedder(self.config, self.pricing)
        return self._embedder

    @property
    def reasoner(self) -> Reasoner:
        if self._reasoner is None:
            self._reasoner = build_reasoner(self.config, self.pricing)
        return self._reasoner

    # -- metering ----------------------------------------------------------

    def meter(self, usage: Usage, cost_model: Any, *, unit: str | None = None) -> float:
        """Record one backend call against the ledger.

        Raises CostCapExceeded once the per-video cap is crossed. The cap is a
        hard stop; the orchestrator catches it and writes a partial artifact.
        """
        return self.ledger.record(stage=self.stage, cost_model=cost_model, usage=usage, unit=unit)

    # -- run directory IO --------------------------------------------------

    def write_model(self, path: Path, model: BaseModel) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(model.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def read_model(self, path: Path, model: type[T]) -> T:
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    def read_model_or_none(self, path: Path, model: type[T]) -> T | None:
        if not path.is_file():
            return None
        return self.read_model(path, model)

    def stage_stats(self, stage: str) -> dict[str, Any]:
        rec = self.manifest.stages.get(stage)
        return dict(rec.stats) if rec else {}

    def warn(self, message: str) -> None:
        log.warning(message)
        self.manifest.warn(message)

    def info(self, message: str) -> None:
        log.info(message)

    def save_manifest(self) -> None:
        self.manifest.save(self.rundir.manifest)
