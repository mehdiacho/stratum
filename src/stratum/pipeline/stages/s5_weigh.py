"""S5 — Weigh.

"Priority context", made measurable. Some videos are audio with wallpaper; some
are silent hands with music over the top. Most are a mix. The pipeline measures
which and passes the classification into S6's prompting rather than assuming.

Phase 1 uses the two cheap signals and skips the grounding check, which costs
model calls. Phase 1 also computes one profile for the whole video; the
simplification is recorded in the profile itself.
"""

from __future__ import annotations

from typing import Any

from stratum.pipeline.context import RunContext
from stratum.schema.artifact import ModalityClass, ModalityProfile, ProfileScope
from stratum.schema.media import SelectionRecord, VideoMeta
from stratum.schema.transcript import SpeechQuality, Transcript

STAGE = "S5"


def classify(
    *,
    has_speech: bool,
    speech_quality: SpeechQuality,
    wpm: float,
    mean_confidence: float | None,
    keyframes_per_minute: float,
    audio_led_wpm: float,
    sparse_speech_wpm: float,
    min_asr_confidence: float,
    visual_led_kfpm: float,
) -> tuple[ModalityClass, str]:
    """Returns the class and the one-line reason it was chosen.

    The reason is carried into the artifact. A classification without a stated
    basis is an opinion, and R6 asks whether this profile changes output quality
    at all — that question is unanswerable if the reasoning is not recorded.
    """
    if not has_speech or speech_quality is SpeechQuality.NONE:
        return (
            ModalityClass.VISUAL_ONLY,
            "No usable speech. Every claim must be seen, stated or inferred — never heard.",
        )

    confident = mean_confidence is None or mean_confidence >= min_asr_confidence

    if speech_quality is SpeechQuality.LOW or not confident:
        shown = "unknown" if mean_confidence is None else round(mean_confidence, 2)
        return (
            ModalityClass.VISUAL_LED,
            f"Speech is present but unreliable (quality {speech_quality.value}, mean "
            f"confidence {shown}). Treated as commentary over the picture.",
        )

    if wpm < sparse_speech_wpm:
        return (
            ModalityClass.VISUAL_LED,
            f"Narration is sparse at {wpm:.1f} wpm. Visual evidence dominant.",
        )

    if wpm >= audio_led_wpm and keyframes_per_minute < visual_led_kfpm:
        return (
            ModalityClass.AUDIO_LED,
            f"Dense narration at {wpm:.1f} wpm over a visually static video "
            f"({keyframes_per_minute:.1f} keyframes/min).",
        )

    if keyframes_per_minute >= visual_led_kfpm and wpm < audio_led_wpm:
        return (
            ModalityClass.VISUAL_LED,
            f"The picture changes fast ({keyframes_per_minute:.1f} keyframes/min) "
            f"against moderate narration ({wpm:.1f} wpm).",
        )

    return (
        ModalityClass.BALANCED,
        f"Narration at {wpm:.1f} wpm alongside {keyframes_per_minute:.1f} keyframes/min. "
        "Neither modality clearly leads; conflicts between them are recorded rather "
        "than resolved.",
    )


def run(ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.pipeline.weigh
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    transcript = ctx.read_model(ctx.rundir.transcript, Transcript)
    selection = ctx.read_model(ctx.rundir.selection_record, SelectionRecord)

    minutes = meta.duration_s / 60.0 if meta.duration_s > 0 else 0.0
    wpm = transcript.word_count / minutes if minutes > 0 else 0.0
    kfpm = len(selection.keyframes) / minutes if minutes > 0 else 0.0

    cls, reason = classify(
        has_speech=transcript.has_speech,
        speech_quality=transcript.speech_quality,
        wpm=wpm,
        mean_confidence=transcript.mean_confidence,
        keyframes_per_minute=kfpm,
        audio_led_wpm=cfg.audio_led_wpm,
        sparse_speech_wpm=cfg.sparse_speech_wpm,
        min_asr_confidence=cfg.min_asr_confidence,
        visual_led_kfpm=cfg.visual_led_kfpm,
    )

    forced = False
    if cfg.force_class:
        try:
            cls = ModalityClass(cfg.force_class)
        except ValueError as exc:
            raise ValueError(
                f"weigh.force_class {cfg.force_class!r} is not one of "
                f"{[c.value for c in ModalityClass]}"
            ) from exc
        forced = True
        reason = f"Forced to {cls.value} by configuration. Computed class would have been: {reason}"

    note = reason
    if not cfg.enable_grounding_check:
        note += " Grounding check skipped (Phase 1 — it costs model calls)."

    profile = ModalityProfile(
        **{"class": cls},
        speech_density_wpm=round(wpm, 2),
        mean_asr_confidence=transcript.mean_confidence,
        keyframes_per_minute=round(kfpm, 2),
        mean_visual_distance=selection.mean_retained_distance,
        grounding_score=None,
        note=note,
        scope=ProfileScope.WHOLE_VIDEO,
    )
    ctx.write_model(ctx.rundir.modality_profile, profile)

    ctx.info(f"modality profile: {cls.value} — {reason}")

    return {
        "class": cls.value,
        "forced": forced,
        "speech_density_wpm": round(wpm, 2),
        "keyframes_per_minute": round(kfpm, 2),
        "mean_asr_confidence": transcript.mean_confidence,
        "mean_visual_distance": selection.mean_retained_distance,
    }
