"""S2 — Transcribe.

Backend-selected. The output is normalised regardless of which backend produced
it, and word-level timestamps are required rather than optional: S6 aligns a
transcript window to each keyframe, and segment granularity is too coarse when
keyframes land two seconds apart.

The transcript is a *witness* to the frames, not a substitute for them.
"""

from __future__ import annotations

from typing import Any

from stratum.pipeline.context import RunContext
from stratum.schema.media import VideoMeta
from stratum.schema.transcript import SpeechQuality, Transcript

STAGE = "S2"

LOW_QUALITY_MEAN_CONFIDENCE = 0.55
LOW_QUALITY_WPM = 15.0


def _grade(transcript: Transcript) -> SpeechQuality:
    """Music-only detection.

    If the transcript comes back near-empty, or with low confidence across the
    board, mark it low. Do not throw it away — song lyrics over a build video are
    noise, and knowing they are noise is what pushes S5 toward visual_led.
    """
    if not transcript.has_speech or transcript.word_count == 0:
        return SpeechQuality.NONE

    wpm = (
        transcript.word_count / (transcript.duration_s / 60.0) if transcript.duration_s > 0 else 0.0
    )
    mean_conf = transcript.mean_confidence

    if wpm < LOW_QUALITY_WPM:
        return SpeechQuality.LOW
    if mean_conf is not None and mean_conf < LOW_QUALITY_MEAN_CONFIDENCE:
        return SpeechQuality.LOW
    return SpeechQuality.GOOD


def run(ctx: RunContext) -> dict[str, Any]:
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    audio_stats = ctx.stage_stats("S1")
    silent = bool(audio_stats.get("treat_as_silent")) or not ctx.rundir.audio.is_file()

    if silent:
        transcript = Transcript.silent(meta.duration_s, backend="none")
        ctx.write_model(ctx.rundir.transcript, transcript)
        ctx.info("no usable audio; writing an empty transcript and continuing")
        return {
            "skipped": True,
            "reason": audio_stats.get("reason") or "audio below silence threshold",
            "has_speech": False,
            "speech_quality": SpeechQuality.NONE.value,
            "words": 0,
        }

    backend = ctx.transcriber
    result = backend.transcribe(ctx.rundir.audio, language=ctx.config.backends.transcriber.language)
    transcript = result.transcript

    # A backend may report its own duration; the container's is authoritative.
    if meta.duration_s > 0:
        transcript.duration_s = meta.duration_s

    transcript.speech_quality = _grade(transcript)
    if transcript.speech_quality is SpeechQuality.NONE:
        transcript.has_speech = False

    ctx.write_model(ctx.rundir.transcript, transcript)
    cost = ctx.meter(result.usage, backend.cost_model, unit="transcript")

    if transcript.speech_quality is SpeechQuality.LOW:
        ctx.warn(
            "transcript quality is low — sparse or low-confidence speech. It is kept "
            "and weighted down rather than discarded."
        )
    if transcript.words == [] and transcript.segments:
        ctx.warn(
            f"{backend.name} returned no word-level timestamps; S6 will fall back to "
            "segment granularity, which is coarse when keyframes are seconds apart"
        )

    return {
        "backend": backend.name,
        "language": transcript.language,
        "words": transcript.word_count,
        "segments": len(transcript.segments),
        "has_speech": transcript.has_speech,
        "speech_quality": transcript.speech_quality.value,
        "mean_confidence": transcript.mean_confidence,
        "cost_usd": cost,
    }
