"""Backends. Three protocols, no leakage.

No pipeline stage imports anything from below this package except the protocol
types, and no stage branches on which backend is active.
"""

from stratum.backends.protocols import (
    Embedder,
    EmbedResult,
    Reasoner,
    ReasonRequest,
    ReasonResponse,
    SynthesiseRequest,
    SynthesiseResponse,
    Transcriber,
    TranscribeResult,
)
from stratum.backends.registry import build_embedder, build_reasoner, build_transcriber

__all__ = [
    "EmbedResult",
    "Embedder",
    "ReasonRequest",
    "ReasonResponse",
    "Reasoner",
    "SynthesiseRequest",
    "SynthesiseResponse",
    "TranscribeResult",
    "Transcriber",
    "build_embedder",
    "build_reasoner",
    "build_transcriber",
]
