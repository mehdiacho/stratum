"""Media handling: ffmpeg/ffprobe calls and cheap per-frame image measures."""

from stratum.media.ffmpeg import (
    ensure_available,
    extract_audio,
    extract_frames,
    has_audio_stream,
    mean_volume_dbfs,
    parse_rate,
    probe,
    rotation_degrees,
    split_streams,
    transpose_filter,
)
from stratum.media.imaging import cosine_similarity, hamming, perceptual_hash, sharpness

__all__ = [
    "cosine_similarity",
    "ensure_available",
    "extract_audio",
    "extract_frames",
    "hamming",
    "has_audio_stream",
    "mean_volume_dbfs",
    "parse_rate",
    "perceptual_hash",
    "probe",
    "rotation_degrees",
    "sharpness",
    "split_streams",
    "transpose_filter",
]
