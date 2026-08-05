"""S0 — Ingest.

Probe the file, record what it actually is, and write `video_meta.json`.

Two traps live here and both are handled explicitly rather than discovered later:
rotation metadata, and the gap between container fps and real average fps on
variable-frame-rate phone footage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stratum.errors import ProbeError
from stratum.media import ffmpeg
from stratum.pipeline.context import RunContext
from stratum.run.directory import content_hash
from stratum.schema.media import VideoMeta

STAGE = "S0"


def _duration_s(fmt: dict[str, Any], video_stream: dict[str, Any] | None) -> float:
    for candidate in (fmt.get("duration"), (video_stream or {}).get("duration")):
        try:
            value = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    # Some containers only carry duration as a stream tag.
    tags = (video_stream or {}).get("tags") or {}
    raw = tags.get("DURATION")
    if isinstance(raw, str) and ":" in raw:
        try:
            hours, minutes, seconds = raw.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            pass
    return 0.0


def probe_video(path: Path) -> VideoMeta:
    """Everything S0 knows about the file. Pure — no run directory involved, so
    it can be called from tests and from `stratum inspect`."""
    ffmpeg.ensure_available()
    data = ffmpeg.probe(path)
    streams = ffmpeg.split_streams(data)
    if streams.video is None:
        raise ProbeError(f"{path.name} has no video stream")

    fps_container = ffmpeg.parse_rate(streams.video.get("r_frame_rate"))
    fps_avg = ffmpeg.parse_rate(streams.video.get("avg_frame_rate"))
    duration = _duration_s(streams.fmt, streams.video)

    if fps_avg is None:
        # No average reported. Derive it if we can; otherwise fall back to the
        # container value, and say so — a wrong fps makes every timestamp wrong.
        frames = streams.video.get("nb_frames")
        try:
            if frames and duration > 0:
                fps_avg = float(frames) / duration
        except (TypeError, ValueError):
            fps_avg = None
    if fps_avg is None:
        fps_avg = fps_container or 0.0

    return VideoMeta(
        filename=path.name,
        path=str(path),
        duration_s=round(duration, 3),
        fps_container=fps_container,
        fps_avg=round(float(fps_avg), 6),
        width=int(streams.video.get("width") or 0),
        height=int(streams.video.get("height") or 0),
        rotation=ffmpeg.rotation_degrees(streams.video),
        has_audio_stream=streams.audio is not None,
        video_codec=streams.video.get("codec_name"),
        audio_codec=(streams.audio or {}).get("codec_name"),
        content_hash=content_hash(path),
        size_bytes=path.stat().st_size,
    )


def run(ctx: RunContext) -> dict[str, Any]:
    path = ctx.video.path
    meta = probe_video(path)
    ctx.write_model(ctx.rundir.video_meta, meta)

    if meta.duration_s <= 0:
        ctx.warn(
            f"{meta.filename}: ffprobe reported no usable duration; downstream "
            "timestamps and budgets will be unreliable"
        )
    if meta.is_variable_frame_rate:
        ctx.info(
            f"{meta.filename}: variable frame rate "
            f"(container {meta.fps_container:.3f}, average {meta.fps_avg:.3f}) — "
            "using the average"
        )
    if meta.rotation:
        ctx.info(
            f"{meta.filename}: rotation flag {meta.rotation}deg will be baked into extracted frames"
        )
    if not meta.has_audio_stream:
        ctx.info(f"{meta.filename}: no audio stream — the silent path is a normal path")

    return {
        "duration_s": meta.duration_s,
        "fps_avg": meta.fps_avg,
        "fps_container": meta.fps_container,
        "resolution": [meta.width, meta.height],
        "rotation": meta.rotation,
        "variable_frame_rate": meta.is_variable_frame_rate,
        "has_audio_stream": meta.has_audio_stream,
        "content_hash": meta.content_hash,
        "size_bytes": meta.size_bytes,
    }
