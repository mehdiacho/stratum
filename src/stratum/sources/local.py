"""Local sources: a file, a folder, and the sidecar metadata reader.

The sidecar is the whole trick. It means a URL downloader is not needed to get
the benefit of URL metadata: any external tool — or Mehdi pasting a caption into
a text file — populates the same fields the eventual module would, and the
pipeline never knows the difference.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from stratum.errors import StratumError
from stratum.sources.protocols import VIDEO_SUFFIXES, Source, VideoRef

SIDECAR_SUFFIX = ".json"


class SourceError(StratumError):
    pass


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class LocalFileSource:
    """One file path."""

    def resolve(self, spec: str) -> Sequence[VideoRef]:
        path = Path(spec).expanduser()
        if not path.is_file():
            raise SourceError(f"not a file: {path}")
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            raise SourceError(
                f"{path.name} does not look like a video "
                f"(known: {', '.join(sorted(VIDEO_SUFFIXES))})"
            )
        return [VideoRef(path=path.resolve())]


class LocalDirectorySource:
    """A folder, which is how a saved series arrives.

    Ordered by filename by default. Filename order is right more often than mtime
    for a series — downloads finish out of order, but people name episodes in
    sequence.
    """

    def __init__(self, *, order_by: str = "name", recursive: bool = False) -> None:
        if order_by not in ("name", "mtime"):
            raise ValueError("order_by must be 'name' or 'mtime'")
        self.order_by = order_by
        self.recursive = recursive

    def resolve(self, spec: str) -> Sequence[VideoRef]:
        root = Path(spec).expanduser()
        if not root.is_dir():
            raise SourceError(f"not a directory: {root}")
        pattern = "**/*" if self.recursive else "*"
        files = [
            p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
        ]
        if self.order_by == "mtime":
            files.sort(key=lambda p: (p.stat().st_mtime, p.name.lower()))
        else:
            files.sort(key=lambda p: (p.name.lower(), p.name))
        if not files:
            raise SourceError(f"no video files in {root}")
        return [VideoRef(path=p.resolve()) for p in files]


class SidecarMetadataSource:
    """Wraps another source and enriches each ref from `<video>.json`.

    Sidecar shape (every field optional):

        {
          "origin": "https://...",
          "title": "NFT channel build, part 2",
          "description": "full caption / post text",
          "author": "@someone",
          "published_at": "2026-07-19T10:00:00Z",
          "fetched_at": "2026-08-01T12:00:00Z"
        }

    `url` and `caption` are accepted as aliases for `origin` and `description`,
    because that is what external downloaders tend to emit and rejecting a file
    over a key name would be pointless friction.
    """

    ALIASES: ClassVar[dict[str, str]] = {
        "url": "origin",
        "webpage_url": "origin",
        "caption": "description",
        "post_text": "description",
        "uploader": "author",
        "channel": "author",
        "upload_date": "published_at",
    }

    def __init__(self, inner: Source) -> None:
        self.inner = inner

    @staticmethod
    def sidecar_for(video: Path) -> Path:
        return video.with_suffix(SIDECAR_SUFFIX)

    def read_sidecar(self, video: Path) -> dict[str, Any]:
        path = self.sidecar_for(video)
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceError(f"cannot read sidecar {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise SourceError(f"sidecar {path} must contain a JSON object")
        normalised: dict[str, Any] = {}
        for key, value in raw.items():
            name = str(key)
            normalised[self.ALIASES.get(name, name)] = value
        return normalised

    def resolve(self, spec: str) -> Sequence[VideoRef]:
        refs = list(self.inner.resolve(spec))
        enriched: list[VideoRef] = []
        for ref in refs:
            data = self.read_sidecar(ref.path)
            if not data:
                enriched.append(ref)
                continue
            enriched.append(
                replace(
                    ref,
                    origin=data.get("origin") or ref.origin,
                    title=data.get("title") or ref.title,
                    description=data.get("description") or ref.description,
                    author=data.get("author") or ref.author,
                    published_at=_parse_datetime(data.get("published_at")) or ref.published_at,
                    fetched_at=_parse_datetime(data.get("fetched_at")) or ref.fetched_at,
                )
            )
        return enriched


class AutoLocalSource:
    """Dispatches on whether the spec is a file or a directory."""

    def __init__(self, *, order_by: str = "name", recursive: bool = False) -> None:
        self.file_source = LocalFileSource()
        self.dir_source = LocalDirectorySource(order_by=order_by, recursive=recursive)

    def resolve(self, spec: str) -> Sequence[VideoRef]:
        path = Path(spec).expanduser()
        if path.is_dir():
            return self.dir_source.resolve(spec)
        return self.file_source.resolve(spec)


def default_source(*, order_by: str = "name", recursive: bool = False) -> Source:
    """What the CLI uses: local resolution, always sidecar-enriched."""
    return SidecarMetadataSource(AutoLocalSource(order_by=order_by, recursive=recursive))
