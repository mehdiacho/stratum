"""How a video gets into the pipeline.

The interface is defined now so the deferred URL module slots in without a
refactor. `UrlSource` is deliberately not implemented and must not be — see
06_INGESTION_ROADMAP.md. The protocol accommodates it; nothing satisfies it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
)


@dataclass(frozen=True)
class VideoRef:
    """A video plus whatever is known about where it came from.

    `description` is the caption or post text and it is not a nicety. It is
    frequently the densest text in the entire artifact — a build video's post text
    often carries the parts list, the dimensions, and the thing the narrator never
    says out loud. Claims derived from it are tagged `stated`.
    """

    path: Path
    origin: str | None = None
    title: str | None = None
    description: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None

    @property
    def has_metadata(self) -> bool:
        return any((self.origin, self.title, self.description, self.author, self.published_at))


@runtime_checkable
class Source(Protocol):
    def resolve(self, spec: str) -> Sequence[VideoRef]: ...
