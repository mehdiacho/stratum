"""S3 — Sample.

Two passes: a uniform coarse decode, and shot-boundary detection.

**Never decode at source frame rate.** This is the single most important
efficiency decision in the pipeline and the correction to the obvious approach. A
10-minute 120 fps video is 72,000 frames; its information content is closer to 80.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from stratum.media import ffmpeg
from stratum.pipeline.context import RunContext
from stratum.schema.media import Candidate, SampleRecord, VideoMeta

STAGE = "S3"

log = logging.getLogger("stratum")


def detect_shot_boundaries(
    path: Path,
    *,
    adaptive_threshold: float,
    min_scene_len_s: float,
    fps: float,
    downscale: int,
) -> tuple[list[float], str | None]:
    """PySceneDetect with `AdaptiveDetector`.

    Adaptive rather than the plain content detector because it handles the
    gradual transitions — fades, slow pans — that content detection misses, and
    this corpus is full of them.

    Returns `(boundary_seconds, note)`. Detection failing is not fatal: the
    pipeline degrades to uniform sampling with no forced retains, and says so.
    """
    try:
        from scenedetect import AdaptiveDetector, SceneManager, open_video
    except ImportError as exc:  # pragma: no cover - dependency is in base install
        return [], f"PySceneDetect unavailable ({exc}); no shot boundaries detected"

    try:
        video = open_video(str(path))
        manager = SceneManager()
        min_scene_len_frames = max(1, round(min_scene_len_s * max(fps, 1.0)))
        manager.add_detector(
            AdaptiveDetector(
                adaptive_threshold=adaptive_threshold,
                min_scene_len=min_scene_len_frames,
            )
        )
        if downscale > 1:
            manager.auto_downscale = False
            manager.downscale = downscale
        manager.detect_scenes(video, show_progress=False)
        scenes = manager.get_scene_list()
    except Exception as exc:
        log.warning("shot detection failed on %s: %s", path.name, exc)
        return [], f"shot detection failed ({exc}); continuing with uniform sampling"

    # The first scene starts at 0, which is not a cut — the first frame is
    # force-retained on its own account.
    boundaries = [float(start.get_seconds()) for start, _end in scenes if start.get_seconds() > 0]
    return sorted(boundaries), None


def _mark_shot_starts(candidates: list[Candidate], boundaries: list[float]) -> int:
    """Force-retain the first candidate at or after each boundary, so a cut can
    never be deduplicated away."""
    if not candidates or not boundaries:
        return 0
    marked = 0
    cursor = 0
    for boundary in boundaries:
        while cursor < len(candidates) and candidates[cursor].timestamp_s < boundary:
            cursor += 1
        if cursor >= len(candidates):
            break
        if not candidates[cursor].is_shot_start:
            candidates[cursor].is_shot_start = True
            marked += 1
    return marked


def run(ctx: RunContext) -> dict[str, Any]:
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    cfg = ctx.config.pipeline.sample

    ctx.rundir.clear_candidates()
    frames = ffmpeg.extract_frames(
        ctx.video.path,
        ctx.rundir.candidates,
        fps=cfg.sample_fps,
        long_edge_px=cfg.long_edge_px,
        jpeg_quality=cfg.jpeg_quality,
        rotation=meta.rotation,
    )

    notes: list[str] = []

    if not frames:
        # A video shorter than one sampling interval. Real, and it must not crash.
        notes.append(
            f"uniform decode at {cfg.sample_fps} fps produced no frames; "
            "the video is shorter than one sampling interval"
        )
        ctx.warn(notes[-1])

    candidates = [
        Candidate(
            index=i,
            # The fps filter emits frame i at i/fps. Timestamps are derived rather
            # than read back so they stay exact and the stage stays deterministic.
            timestamp_s=round(i / cfg.sample_fps, 4),
            path=ctx.rundir.relative(frame),
        )
        for i, frame in enumerate(frames)
    ]

    boundaries: list[float] = []
    detector: str | None = None
    if cfg.detect_shots and candidates:
        boundaries, note = detect_shot_boundaries(
            ctx.video.path,
            adaptive_threshold=cfg.adaptive_threshold,
            min_scene_len_s=cfg.min_scene_len_s,
            fps=meta.fps_avg or cfg.sample_fps,
            downscale=cfg.scene_detect_downscale,
        )
        detector = "AdaptiveDetector"
        if note:
            notes.append(note)
            ctx.warn(note)
        elif not boundaries:
            # Entirely normal — a single continuous tripod shot has no cuts.
            notes.append("no shot boundaries detected; the video appears to be one shot")

    marked = _mark_shot_starts(candidates, boundaries)

    record = SampleRecord(
        sample_fps=cfg.sample_fps,
        long_edge_px=cfg.long_edge_px,
        candidates=candidates,
        shot_boundaries_s=boundaries,
        detector=detector,
        note="; ".join(notes) or None,
    )
    ctx.write_model(ctx.rundir.sample_record, record)

    ctx.info(
        f"sampled {len(candidates)} candidates at {cfg.sample_fps} fps "
        f"({len(boundaries)} shot boundaries, {marked} force-retained)"
    )

    return {
        "candidates": len(candidates),
        "sample_fps": cfg.sample_fps,
        "shot_boundaries": len(boundaries),
        "shot_starts_marked": marked,
        "note": record.note,
    }
