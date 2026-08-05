"""S7 — Synthesise.

One reasoner call over the full transition record, producing the procedure, the
materials, the open questions and the conflicts.

Everything the model returns is checked against what actually exists before it
becomes canonical. A step citing a transition that was never recorded is a step
the model invented, and it is recorded as a dropped reference rather than quietly
accepted.
"""

from __future__ import annotations

from typing import Any

from stratum.backends.protocols import SynthesiseRequest
from stratum.pipeline.context import RunContext
from stratum.pipeline.entities import EntityRegistry
from stratum.pipeline.prompts import load_prompt
from stratum.schema.artifact import (
    Conflict,
    Material,
    ModalityProfile,
    OpenQuestion,
    Step,
)
from stratum.schema.common import ProvenancedQuantity
from stratum.schema.ids import conflict_id, open_question_id, step_id
from stratum.schema.media import VideoMeta
from stratum.schema.synthesis import SynthesisResult
from stratum.schema.transition import Transition, TransitionStatus
from stratum.util import format_duration, format_timestamp, truncate_words

STAGE = "S7"


def _load_transitions(ctx: RunContext) -> list[Transition]:
    path = ctx.rundir.transitions
    if not path.is_file():
        return []
    out: list[Transition] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(Transition.model_validate_json(line))
    return out


def _render_transitions(transitions: list[Transition], *, include_failed: bool) -> str:
    """The transition record as the model sees it.

    Modality and confidence are written inline against every claim. If the model
    cannot see which claims were inferred, it cannot preserve the distinction, and
    provenance laundering is the characteristic S7 failure.
    """
    lines: list[str] = []
    for t in transitions:
        if t.status is TransitionStatus.FAILED:
            if include_failed:
                lines.append(
                    f"[{t.id} @ {format_timestamp(t.keyframe.timestamp_s)} {t.keyframe.id}] "
                    f"ANALYSIS FAILED — this moment is unobserved, not uneventful."
                )
            continue
        head = f"[{t.id} @ {format_timestamp(t.keyframe.timestamp_s)} {t.keyframe.id}]"
        if t.no_meaningful_change:
            lines.append(f"{head} no meaningful change.")
            continue
        lines.append(f"{head} {t.delta}")
        if t.action_inferred:
            lines.append(
                f"    action: {t.action_inferred.value} "
                f"(confidence {t.action_inferred.confidence:.2f})"
            )
        for claim in t.claims:
            lines.append(
                f"    claim: {claim.text} [{claim.modality.value}, {claim.confidence:.2f}]"
            )
        if t.uncertainty:
            lines.append(f"    unresolved: {t.uncertainty}")
    return "\n".join(lines)


def _render_entities(registry: EntityRegistry) -> str:
    lines = []
    for entity in registry.all():
        attrs = ", ".join(
            f"{name}={value.value} [{value.modality.value}, {value.confidence:.2f}]"
            for name, value in entity.attributes.items()
        )
        span = f"{entity.first_seen_kf}..{entity.last_seen_kf}"
        lines.append(
            f"- {entity.label} ({entity.kind}) seen {span}, {len(entity.mentions)} mentions"
            + (f" — {attrs}" if attrs else "")
        )
    return "\n".join(lines) or "(none tracked)"


def run(ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.pipeline.synthesise
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    profile = ctx.read_model(ctx.rundir.modality_profile, ModalityProfile)
    registry = EntityRegistry.load(ctx.rundir.entities)
    transitions = _load_transitions(ctx)

    by_id = {t.id: t for t in transitions}
    known_keyframes = {t.keyframe.id for t in transitions}
    failed = [t for t in transitions if t.status is TransitionStatus.FAILED]

    if not transitions:
        result = SynthesisResult(overview="No transitions were recorded for this video.")
        ctx.write_model(ctx.rundir.synthesis, result)
        ctx.warn("S7 had no transitions to synthesise from")
        return {"steps": 0, "materials": 0, "open_questions": 0, "conflicts": 0}

    failure_ratio = len(failed) / len(transitions)
    if failure_ratio > cfg.max_failed_transition_ratio:
        raise ValueError(
            f"{len(failed)} of {len(transitions)} transitions failed "
            f"({failure_ratio:.0%}), above synthesise.max_failed_transition_ratio "
            f"({cfg.max_failed_transition_ratio:.0%}). Synthesising from this record "
            "would produce a confidently wrong document."
        )

    prompt = load_prompt(ctx.prompts_dir, ctx.config.prompts.synthesise)
    system, user = prompt.render(
        title=ctx.video.title,
        author=ctx.video.author,
        description=ctx.video.description,
        duration_human=format_duration(meta.duration_s),
        profile_class=profile.cls.value,
        profile_note=profile.note or "",
        transition_count=len(transitions),
        failed_count=len(failed),
        transitions_text=_render_transitions(
            transitions, include_failed=cfg.include_failed_in_context
        ),
        entities_text=_render_entities(registry),
    )

    response = ctx.reasoner.synthesise(
        SynthesiseRequest(
            system_prompt=system,
            user_prompt=user,
            temperature=ctx.config.backends.reasoner.temperature,
            max_attempts=ctx.config.backends.reasoner.max_attempts,
        )
    )
    cost = ctx.meter(response.usage, ctx.reasoner.cost_model, unit="synthesis")
    draft = response.draft
    dropped: list[str] = []

    # -- procedure ---------------------------------------------------------
    procedure: list[Step] = []
    for ordinal, step in enumerate(draft.steps, start=1):
        cited = [ref for ref in step.derived_from if ref in by_id]
        dropped.extend(f"step {ordinal}: {ref}" for ref in step.derived_from if ref not in by_id)
        stamps = [by_id[ref].keyframe.timestamp_s for ref in cited]
        procedure.append(
            Step(
                id=step_id(ordinal),
                ordinal=ordinal,
                title=step.title,
                detail=step.detail,
                derived_from=cited,
                timestamp_range_s=(min(stamps), max(stamps)) if stamps else None,
                keyframes=[by_id[ref].keyframe.id for ref in cited],
                confidence=step.confidence,
            )
        )

    # -- materials ---------------------------------------------------------
    materials: list[Material] = []
    for material in draft.materials:
        entity = registry.by_label(material.entity_label)
        if entity is None:
            # Register it rather than leaving a dangling id in the artifact, and
            # keep the note: S7 naming a thing S6 never saw is worth surfacing.
            eid = registry.register_synthetic(material.entity_label)
            dropped.append(f"material references an untracked entity: {material.entity_label}")
        else:
            eid = entity.id
        quantity = (
            ProvenancedQuantity(
                value=material.quantity,
                unit=material.unit,
                modality=material.modality,
                confidence=material.confidence,
            )
            if material.quantity is not None
            else None
        )
        materials.append(
            Material(
                entity=eid,
                label=entity.label if entity else material.entity_label,
                quantity=quantity,
                note=material.note,
            )
        )

    # -- open questions ----------------------------------------------------
    open_questions = [
        OpenQuestion(
            id=open_question_id(i),
            text=q.text,
            why_it_matters=q.why_it_matters,
            related=[
                rid
                for rid in (registry.id_for_label(label) for label in q.related_entity_labels)
                if rid
            ],
        )
        for i, q in enumerate(draft.open_questions, start=1)
    ]

    # A run with failures has a gap in it, and the gap is itself an open question.
    if failed:
        failed_at = ", ".join(format_timestamp(t.keyframe.timestamp_s) for t in failed[:8])
        open_questions.append(
            OpenQuestion(
                id=open_question_id(len(open_questions) + 1),
                text=(
                    f"{len(failed)} keyframe(s) could not be analysed ({failed_at}"
                    f"{', ...' if len(failed) > 8 else ''})."
                ),
                why_it_matters=(
                    "Those moments are unobserved rather than uneventful; anything that "
                    "happened in them is missing from this record."
                ),
            )
        )

    # -- conflicts ---------------------------------------------------------
    conflicts: list[Conflict] = []
    for i, conflict in enumerate(draft.conflicts, start=1):
        cited = [kf for kf in conflict.keyframes if kf in known_keyframes]
        dropped.extend(
            f"conflict {i}: {kf}" for kf in conflict.keyframes if kf not in known_keyframes
        )
        conflicts.append(
            Conflict(
                id=conflict_id(i),
                heard=conflict.heard,
                seen=conflict.seen,
                keyframes=cited,
                note=conflict.note,
            )
        )

    # S7 may have registered entities S6 never saw; S8 reads the registry back.
    registry.save(ctx.rundir.entities)

    result = SynthesisResult(
        overview=truncate_words(draft.overview, 400),
        procedure=procedure,
        materials=materials,
        open_questions=open_questions,
        conflicts=conflicts,
        dropped_references=dropped,
    )
    ctx.write_model(ctx.rundir.synthesis, result)

    if dropped:
        ctx.warn(
            f"S7 dropped {len(dropped)} reference(s) that pointed at nothing (first: {dropped[0]})"
        )

    ctx.info(
        f"synthesised {len(procedure)} steps, {len(materials)} materials, "
        f"{len(open_questions)} open questions, {len(conflicts)} conflicts"
    )

    return {
        "steps": len(procedure),
        "materials": len(materials),
        "open_questions": len(open_questions),
        "conflicts": len(conflicts),
        "dropped_references": len(dropped),
        "failed_transitions": len(failed),
        "cost_usd": cost,
    }
