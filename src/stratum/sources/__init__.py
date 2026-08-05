"""Ingestion sources. Local files now; the URL module is deferred by decision."""

from stratum.sources.local import (
    AutoLocalSource,
    LocalDirectorySource,
    LocalFileSource,
    SidecarMetadataSource,
    SourceError,
    default_source,
)
from stratum.sources.protocols import VIDEO_SUFFIXES, Source, VideoRef

__all__ = [
    "VIDEO_SUFFIXES",
    "AutoLocalSource",
    "LocalDirectorySource",
    "LocalFileSource",
    "SidecarMetadataSource",
    "Source",
    "SourceError",
    "VideoRef",
    "default_source",
]
