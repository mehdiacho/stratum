"""S6 — Traverse. The core.

One reasoner call per keyframe, in order, carrying state. Each call receives the
current keyframe, a compact running state, optionally the previous keyframe, the
transcript window aligned to this moment, and the video-level context.

**Sequential by nature.** Do not be tempted to parallelise this by chunking the
video: the seams produce exactly the discontinuities the design exists to remove.

Two things here are load-bearing and easy to get wrong:

- The running state is REPLACED each turn, never accumulated. If the model's
  state exceeds the budget it is compacted, and every compaction is logged.
  Constant compaction means the budget is wrong, not that the video is complex.
- A failed call writes `status: failed` and the run continues. One bad keyframe
  must not destroy a run, and a gap in the record must be visible rather than
  silent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stratum.backends.protocols import ReasonRequest
from stratum.errors import BackendError, CostCapExceeded
from stratum.pipeline.context import RunContext
from stratum.pipeline.entities import EntityRegistry
from stratum.pipeline.prompts import load_prompt
from stratum.schema.artifact import ModalityClass, ModalityProfile
from stratum.schema.common import Claim, Modality
from stratum.schema.ids import claim_id, transition_id
from stratum.schema.media import Keyframe, SelectionRecord, VideoMeta
from stratum.schema.transcript import Transcript
from stratum.schema.transition import (
    ActionInferred,
    DraftClaim,
    TranscriptWeight,
    TranscriptWindow,
    Transition,
    TransitionDraft,
    TransitionStatus,
)
from stratum.util import approx_tokens, format_duration, format_timestamp

STAGE = "S6"


@dataclass(frozen=True)
class _WeightPolicy:
    """How the S5 profile changes what S6 actually does.

    This is the concrete answer to "does the modality profile change output
    quality, or only the prompt" (R6): it changes the transcript window width, the
    weight label the model is given, and whether the transcript is sent at all.
    """

    weight: TranscriptWeight
    margin_multiplier: float
    guidance: str


_POLICIES: dict[ModalityClass, _WeightPolicy] = {
    ModalityClass.AUDIO_LED: _WeightPolicy(
        weight=TranscriptWeight.PRIMARY,
        margin_multiplier=2.0,
        guidance=(
            "This video is carried by its narration. Treat the transcript as the "
            "primary evidence and use the frame to confirm and locate what is said."
        ),
    ),
    ModalityClass.VISUAL_LED: _WeightPolicy(
        weight=TranscriptWeight.HINT,
        margin_multiplier=0.5,
        guidance=(
            "This video is carried by its picture. The frame is the primary evidence; "
            "the transcript is a hint and is often commentary rather than instruction. "
            "Do not let it talk you into seeing something that is not there."
        ),
    ),
    ModalityClass.BALANCED: _WeightPolicy(
        weight=TranscriptWeight.EQUAL,
        margin_multiplier=1.0,
        guidance=(
            "Audio and picture carry roughly equal load. Where they disagree, record "
            "both as separate claims with their own modalities. Do not silently pick one."
        ),
    ),
    ModalityClass.VISUAL_ONLY: _WeightPolicy(
        weight=TranscriptWeight.OMITTED,
        margin_multiplier=0.0,
        guidance=("There is no usable speech in this video. No claim may be tagged `heard`."),
    ),
}


def compact_state(state: str, budget_tokens: int, known_entities: list[str]) -> tuple[str, bool]:
    """Keep the running state inside its budget.

    Deterministic and model-free on purpose — a compaction that costs a model call
    would make the expensive stage more expensive precisely when it is already
    struggling. Stable entities collapse to a single roll-call line and the most
    recent detail is kept, which is the part that is actively changing.

    R3 asks whether compaction loses things that matter — a part set down at 2:00
    and picked up at 8:00. The entity roll-call is the answer to that, and the
    reference video is supposed to contain a deliberate test case for it.
    """
    if approx_tokens(state) <= budget_tokens:
        return state, False

    roll_call = ", ".join(known_entities[:40])
    header = f"Known so far: {roll_call}.\n" if roll_call else ""
    remaining = max(200, (budget_tokens - approx_tokens(header)) * 4)
    tail = state[-remaining:].lstrip()
    return f"{header}Most recent state: ...{tail}", True


def _read_existing(path: Path) -> list[Transition]:
    """Resume support. A truncated final line — the crash case — is discarded."""
    if not path.is_file():
        return []
    records: list[Transition] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(Transition.model_validate_json(line))
        except ValueError:
            break
    return records


def _window_for(
    keyframes: list[Keyframe], index: int, *, margin_s: float, duration_s: float
) -> tuple[float, float]:
    start = keyframes[index - 1].timestamp_s if index > 0 else 0.0
    end = keyframes[index].timestamp_s
    if index == len(keyframes) - 1 and duration_s > end:
        # The last keyframe should hear the tail of the video, not stop dead at
        # its own timestamp.
        end = duration_s
    return max(0.0, start - margin_s), end + margin_s


def _admissible_claims(
    draft: TransitionDraft, *, heard_allowed: bool, stated_allowed: bool
) -> tuple[list[DraftClaim], list[str]]:
    """Drop claims whose provenance is impossible given what was actually sent.

    A `heard` claim on a keyframe where no transcript was supplied is not a
    mis-tag, it is fabricated content: there was nothing to hear. Same for
    `stated` when no author description exists. Retagging them to `inferred`
    would keep the fabrication and merely relabel it, so they are dropped.

    The prompt already forbids this. This is the enforcement, because provenance
    on every claim is the product and a prompt is not a guarantee.
    """
    kept: list[DraftClaim] = []
    rejected: list[str] = []
    for claim in draft.claims:
        if claim.modality is Modality.HEARD and not heard_allowed:
            rejected.append(f"heard claim with no transcript: {claim.text[:80]}")
            continue
        if claim.modality is Modality.STATED and not stated_allowed:
            rejected.append(f"stated claim with no author description: {claim.text[:80]}")
            continue
        kept.append(claim)
    return kept, rejected


def _to_transition(
    draft: TransitionDraft,
    *,
    index: int,
    keyframe: Keyframe,
    registry: EntityRegistry,
    window: TranscriptWindow | None,
    attempts: int,
    claims_override: list[DraftClaim] | None = None,
) -> Transition:
    tr_id = transition_id(index)

    added = [registry.observe(e, kf_id=keyframe.id) for e in draft.entities_added]
    changed = [registry.observe(e, kf_id=keyframe.id) for e in draft.entities_changed]
    removed = [
        eid
        for eid in (
            registry.mark_removed(label, kf_id=keyframe.id) for label in draft.entities_removed
        )
        if eid
    ]

    claims = [
        Claim(
            id=claim_id(index, i),
            text=c.text,
            modality=c.modality,
            confidence=c.confidence,
            # A `heard` claim must be traceable to a transcript span. When the model
            # omits one, fall back to the window it was actually given.
            transcript_span=c.transcript_span
            or (
                (window.start_s, window.end_s) if window and c.modality is Modality.HEARD else None
            ),
            source_kf=keyframe.id,
        )
        for i, c in enumerate(draft.claims if claims_override is None else claims_override)
    ]

    action = (
        ActionInferred(value=draft.action_inferred, confidence=draft.action_confidence)
        if draft.action_inferred
        else None
    )

    return Transition(
        id=tr_id,
        keyframe=keyframe,
        delta=draft.delta,
        action_inferred=action,
        entities_added=added,
        entities_changed=changed,
        entities_removed=removed,
        claims=claims,
        uncertainty=draft.uncertainty,
        transcript_window=window,
        no_meaningful_change=draft.no_meaningful_change,
        status=TransitionStatus.OK if attempts == 1 else TransitionStatus.RETRIED,
        attempts=attempts,
    )


def run(ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.pipeline.traverse
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    selection = ctx.read_model(ctx.rundir.selection_record, SelectionRecord)
    transcript = ctx.read_model(ctx.rundir.transcript, Transcript)
    profile = ctx.read_model(ctx.rundir.modality_profile, ModalityProfile)

    keyframes = selection.keyframes
    if not keyframes:
        ctx.rundir.transitions.write_text("", encoding="utf-8")
        ctx.warn("S6 had no keyframes to traverse")
        return {"transitions": 0, "failed": 0}

    policy = _POLICIES[profile.cls]
    prompt = load_prompt(ctx.prompts_dir, ctx.config.prompts.traverse)

    # -- resume ------------------------------------------------------------
    existing = _read_existing(ctx.rundir.transitions)
    state_blob = {}
    if ctx.rundir.traverse_state.is_file():
        try:
            state_blob = json.loads(ctx.rundir.traverse_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state_blob = {}

    start_index = len(existing)
    running_state = str(state_blob.get("state", ""))
    if state_blob.get("next_index") != start_index:
        # Record and state disagree — a kill between the two writes. Redo from the
        # start rather than continue with a state that does not match the record.
        if start_index:
            ctx.info("traverse state is out of step with the transition record; restarting S6")
        existing, start_index, running_state = [], 0, ""

    registry = EntityRegistry.load(ctx.rundir.entities) if start_index else EntityRegistry()

    if start_index == 0:
        ctx.rundir.transitions.write_text("", encoding="utf-8")
    elif start_index >= len(keyframes):
        ctx.info(f"S6 already complete ({start_index} transitions)")
        return _summarise(existing, resumed_from=start_index)
    else:
        ctx.info(f"resuming S6 at keyframe {start_index + 1} of {len(keyframes)}")

    margin = cfg.transcript_window_margin_s * policy.margin_multiplier
    compactions = 0
    failures = 0
    inadmissible = 0
    transitions = list(existing)
    total_cost = 0.0

    with ctx.rundir.transitions.open("a", encoding="utf-8") as sink:
        for index in range(start_index, len(keyframes)):
            keyframe = keyframes[index]

            window: TranscriptWindow | None = None
            transcript_text = ""
            window_start, window_end = _window_for(
                keyframes, index, margin_s=margin, duration_s=meta.duration_s
            )
            if policy.weight is not TranscriptWeight.OMITTED and transcript.has_speech:
                transcript_text = transcript.text_between(window_start, window_end)
                if transcript_text:
                    window = TranscriptWindow(
                        start_s=round(window_start, 2),
                        end_s=round(window_end, 2),
                        text=transcript_text,
                        weight=policy.weight,
                    )

            images = [ctx.rundir.root / keyframe.path]
            for back in range(1, cfg.prior_frames_in_context + 1):
                prior = index - back
                if prior >= 0:
                    images.insert(0, ctx.rundir.root / keyframes[prior].path)

            system, user = prompt.render(
                title=ctx.video.title,
                author=ctx.video.author,
                description=ctx.video.description,
                duration_human=format_duration(meta.duration_s),
                profile_class=profile.cls.value,
                profile_note=profile.note or "",
                position=index + 1,
                total=len(keyframes),
                timestamp_s=round(keyframe.timestamp_s, 2),
                timestamp_human=format_timestamp(keyframe.timestamp_s),
                is_first=index == 0,
                shot_boundary=keyframe.shot_boundary,
                running_state=running_state,
                transcript_weight=policy.weight.value,
                transcript_text=transcript_text,
                window_start_s=round(window_start, 2),
                window_end_s=round(window_end, 2),
                transcript_guidance=policy.guidance,
                prior_count=len(images) - 1,
            )

            request = ReasonRequest(
                system_prompt=system,
                user_prompt=user,
                images=images,
                max_output_tokens=ctx.config.backends.reasoner.max_output_tokens,
                temperature=ctx.config.backends.reasoner.temperature,
                max_attempts=ctx.config.backends.reasoner.max_attempts,
                keyframe_id=keyframe.id,
                timestamp_s=keyframe.timestamp_s,
            )

            try:
                response = ctx.reasoner.analyse(request)
            except CostCapExceeded:
                # Propagates. The orchestrator writes a partial artifact containing
                # everything completed so far.
                raise
            except (BackendError, ValueError) as exc:
                failures += 1
                transition = Transition.failed(
                    transition_id=transition_id(index),
                    keyframe=keyframe,
                    error=str(exc),
                    attempts=ctx.config.backends.reasoner.max_attempts,
                )
                ctx.warn(f"{keyframe.id} failed: {exc}")
            else:
                total_cost += ctx.meter(response.usage, ctx.reasoner.cost_model, unit=keyframe.id)
                admissible, rejected = _admissible_claims(
                    response.draft,
                    heard_allowed=window is not None,
                    stated_allowed=bool(ctx.video.description),
                )
                inadmissible += len(rejected)
                for reason in rejected:
                    ctx.info(f"{keyframe.id}: dropped a claim — {reason}")
                transition = _to_transition(
                    response.draft,
                    index=index,
                    keyframe=keyframe,
                    registry=registry,
                    window=window,
                    attempts=response.attempts,
                    claims_override=admissible,
                )
                running_state, compacted = compact_state(
                    response.draft.state_after,
                    cfg.state_token_budget,
                    [e.label for e in registry.all()],
                )
                if compacted:
                    compactions += 1
                    ctx.info(f"{keyframe.id}: running state compacted to fit the budget")

            transitions.append(transition)
            sink.write(json.dumps(transition.model_dump(mode="json"), separators=(",", ":")))
            sink.write("\n")
            sink.flush()

            registry.save(ctx.rundir.entities)
            ctx.rundir.traverse_state.write_text(
                json.dumps({"next_index": index + 1, "state": running_state}, indent=2),
                encoding="utf-8",
            )

    if compactions > len(keyframes) // 3:
        ctx.warn(
            f"running state was compacted on {compactions} of {len(keyframes)} keyframes; "
            "traverse.state_token_budget is probably too small"
        )
    if failures:
        ctx.warn(
            f"{failures} of {len(keyframes)} keyframes failed analysis and are recorded "
            "with status=failed. Those moments are unobserved, not uneventful."
        )

    if inadmissible:
        ctx.warn(
            f"dropped {inadmissible} claim(s) whose provenance was impossible — the "
            "model tagged something `heard` with no transcript, or `stated` with no "
            "author description. That is fabricated content, not a mis-tag."
        )

    summary = _summarise(transitions, resumed_from=start_index)
    summary["inadmissible_claims"] = inadmissible
    summary["compactions"] = compactions
    summary["cost_usd"] = total_cost
    summary["entities"] = len(registry)
    ctx.info(
        f"traversed {len(transitions)} keyframes, {failures} failed, "
        f"{len(registry)} entities tracked"
    )
    return summary


def _summarise(transitions: list[Transition], *, resumed_from: int) -> dict[str, Any]:
    failed = [t for t in transitions if t.status is TransitionStatus.FAILED]
    unchanged = [t for t in transitions if t.no_meaningful_change]
    return {
        "transitions": len(transitions),
        "failed": len(failed),
        "no_meaningful_change": len(unchanged),
        "resumed_from_index": resumed_from,
    }
