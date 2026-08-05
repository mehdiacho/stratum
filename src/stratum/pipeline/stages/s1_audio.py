"""S1 — Audio extraction.

16 kHz mono 16-bit WAV. Every ASR backend wants this, and resampling once up
front stops each backend doing its own.

A silent video is a normal path, not an error. A lot of build footage in this
corpus is hands plus music, and that is the case the whole design exists for.
"""

from __future__ import annotations

from typing import Any

from stratum.media import ffmpeg
from stratum.pipeline.context import RunContext
from stratum.schema.media import VideoMeta

STAGE = "S1"


def run(ctx: RunContext) -> dict[str, Any]:
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)

    if not meta.has_audio_stream:
        ctx.info("no audio stream; skipping extraction")
        ctx.rundir.audio.unlink(missing_ok=True)
        return {
            "has_audio_stream": False,
            "treat_as_silent": True,
            "mean_volume_dbfs": None,
            "reason": "no audio stream in container",
        }

    ffmpeg.extract_audio(
        ctx.video.path,
        ctx.rundir.audio,
        sample_rate=ctx.config.pipeline.audio_sample_rate,
    )

    mean_dbfs = ffmpeg.mean_volume_dbfs(ctx.rundir.audio)
    threshold = ctx.config.pipeline.silence_dbfs_threshold

    # Unknown loudness is treated as "has audio", not as silent. Guessing silent
    # would skip transcription on a video that has speech, and a missing
    # transcript is a much more expensive mistake than a wasted ASR pass.
    treat_as_silent = mean_dbfs is not None and mean_dbfs < threshold

    if treat_as_silent:
        ctx.info(
            f"audio mean volume {mean_dbfs:.1f} dBFS is below {threshold:.1f}; treating as silent"
        )

    return {
        "has_audio_stream": True,
        "treat_as_silent": treat_as_silent,
        "mean_volume_dbfs": mean_dbfs,
        "sample_rate": ctx.config.pipeline.audio_sample_rate,
        "wav_bytes": ctx.rundir.audio.stat().st_size if ctx.rundir.audio.is_file() else 0,
    }
