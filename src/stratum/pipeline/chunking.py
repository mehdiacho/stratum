"""`chunks.jsonl` — the handoff point.

Chunks break at transition and step boundaries, never at a token count. Each one
repeats the context needed to stand alone, because standing alone is how it will
be retrieved.

Modality tags are written into the *text*, not only into metadata: retrieval
layers strip metadata, and a model reading a retrieved fragment must be able to
see that 0.45 without access to a schema.
"""

from __future__ import annotations

from stratum.schema.artifact import Artifact
from stratum.schema.chunks import Chunk, ChunkKind, ChunkSource
from stratum.schema.common import Modality
from stratum.schema.ids import chunk_id
from stratum.schema.transition import Transition, TransitionStatus
from stratum.util import format_timestamp


def _tag(text: str, modality: Modality, confidence: float) -> str:
    return f"{text} [{modality.value}, {confidence:.2f}]"


def _source(artifact: Artifact, span: tuple[float, float] | None) -> ChunkSource:
    return ChunkSource(
        run_id=artifact.run.id,
        video=artifact.run.source.filename,
        timestamp_range_s=span,
        origin=artifact.run.source.origin,
    )


def _modalities(*groups: list[Modality]) -> list[Modality]:
    seen: list[Modality] = []
    for group in groups:
        for modality in group:
            if modality not in seen:
                seen.append(modality)
    return seen


def _overview_chunk(artifact: Artifact) -> Chunk:
    run = artifact.run
    duration = format_timestamp(run.source.duration_s)
    profile = artifact.modality_profile
    parts = [
        f"{run.source.filename} — {duration} — {profile.cls.value.replace('_', '-')}.",
        artifact.overview,
    ]
    if run.source.description:
        parts.append(f"Author description [stated]: {run.source.description}")
    if artifact.procedure:
        titles = "; ".join(f"{s.ordinal}. {s.title}" for s in artifact.procedure)
        parts.append(f"Procedure in {len(artifact.procedure)} steps: {titles}.")
    if artifact.open_questions:
        parts.append(f"{len(artifact.open_questions)} open questions remain.")
    return Chunk(
        chunk_id=chunk_id(run.id, "overview"),
        kind=ChunkKind.OVERVIEW,
        text=" ".join(p for p in parts if p).strip(),
        source=_source(artifact, (0.0, run.source.duration_s)),
        keyframes=[t.keyframe.path for t in artifact.transitions[:1]],
        entities=[e.id for e in artifact.entities],
        modalities_present=[Modality.STATED] if run.source.description else [],
        confidence=0.7,
    )


def _step_chunk(artifact: Artifact, step_index: int) -> Chunk:
    step = artifact.procedure[step_index]
    total = len(artifact.procedure)
    span = step.timestamp_range_s
    when = f" ({format_timestamp(span[0])}-{format_timestamp(span[1])})" if span else ""

    lines = [f"Step {step.ordinal} of {total} — {step.title}{when}. {step.detail}".strip()]

    claims: list[str] = []
    modalities: list[Modality] = []
    entities: list[str] = []
    keyframes: list[str] = []
    unresolved: list[str] = []

    for ref in step.derived_from:
        transition = artifact.transition_by_id(ref)
        if transition is None:
            continue
        keyframes.append(transition.keyframe.path)
        entities.extend(transition.entities_added + transition.entities_changed)
        for claim in transition.claims:
            claims.append(_tag(claim.text, claim.modality, claim.confidence))
            modalities.append(claim.modality)
        if transition.uncertainty:
            unresolved.append(transition.uncertainty)

    if claims:
        lines.append(" ".join(claims))
    if unresolved:
        lines.append("Unresolved: " + " ".join(unresolved))

    return Chunk(
        chunk_id=chunk_id(artifact.run.id, step.id),
        kind=ChunkKind.STEP,
        text=" ".join(lines).strip(),
        source=_source(artifact, span),
        keyframes=keyframes,
        entities=sorted(set(entities)),
        modalities_present=_modalities(modalities),
        confidence=step.confidence,
    )


def _transition_chunk(artifact: Artifact, transition: Transition) -> Chunk:
    when = format_timestamp(transition.keyframe.timestamp_s)
    lines = [f"At {when} ({transition.keyframe.id}): {transition.delta}"]
    if transition.action_inferred:
        lines.append(
            _tag(
                f"Action: {transition.action_inferred.value}",
                Modality.INFERRED,
                transition.action_inferred.confidence,
            )
        )
    modalities = []
    for claim in transition.claims:
        lines.append(_tag(claim.text, claim.modality, claim.confidence))
        modalities.append(claim.modality)
    if transition.uncertainty:
        lines.append(f"Unresolved: {transition.uncertainty}")

    confidence = (
        sum(c.confidence for c in transition.claims) / len(transition.claims)
        if transition.claims
        else 0.5
    )
    return Chunk(
        chunk_id=chunk_id(artifact.run.id, transition.id),
        kind=ChunkKind.TRANSITION,
        text=" ".join(lines).strip(),
        source=_source(
            artifact, (transition.keyframe.timestamp_s, transition.keyframe.timestamp_s)
        ),
        keyframes=[transition.keyframe.path],
        entities=sorted(set(transition.entities_added + transition.entities_changed)),
        modalities_present=_modalities(modalities),
        confidence=round(confidence, 2),
    )


def _entity_chunk(artifact: Artifact, entity_index: int) -> Chunk:
    entity = artifact.entities[entity_index]
    attrs = " ".join(
        _tag(f"{name}: {value.value}", value.modality, value.confidence)
        for name, value in entity.attributes.items()
    )
    material = next((m for m in artifact.materials if m.entity == entity.id), None)
    quantity = ""
    if material and material.quantity:
        quantity = " " + _tag(
            f"Quantity: {material.quantity.value:g}"
            + (f" {material.quantity.unit}" if material.quantity.unit else ""),
            material.quantity.modality,
            material.quantity.confidence,
        )

    stamps = [
        t.keyframe.timestamp_s for t in artifact.transitions if t.keyframe.id in entity.mentions
    ]
    span = (min(stamps), max(stamps)) if stamps else None

    text = (
        f"{entity.label} ({entity.kind}) appears in {len(entity.mentions)} keyframes "
        f"from {entity.first_seen_kf} to {entity.last_seen_kf}. {attrs}{quantity}"
    ).strip()

    return Chunk(
        chunk_id=chunk_id(artifact.run.id, entity.id),
        kind=ChunkKind.ENTITY,
        text=text,
        source=_source(artifact, span),
        keyframes=[
            t.keyframe.path for t in artifact.transitions if t.keyframe.id in entity.mentions
        ][:6],
        entities=[entity.id],
        modalities_present=_modalities([v.modality for v in entity.attributes.values()]),
        confidence=0.6,
    )


def build_chunks(artifact: Artifact, *, include_transitions: bool = True) -> list[Chunk]:
    chunks: list[Chunk] = [_overview_chunk(artifact)]

    for index in range(len(artifact.procedure)):
        chunks.append(_step_chunk(artifact, index))

    if include_transitions:
        for transition in artifact.transitions:
            if transition.status is TransitionStatus.FAILED or transition.no_meaningful_change:
                # A failed or empty transition has nothing to retrieve. It stays in
                # artifact.json, where the gap is visible; it is not indexed.
                continue
            chunks.append(_transition_chunk(artifact, transition))

    for index in range(len(artifact.entities)):
        chunks.append(_entity_chunk(artifact, index))

    for question in artifact.open_questions:
        chunks.append(
            Chunk(
                chunk_id=chunk_id(artifact.run.id, question.id),
                kind=ChunkKind.OPEN_QUESTION,
                text=(
                    f"Open question from {artifact.run.source.filename}: {question.text} "
                    f"Why it matters: {question.why_it_matters}"
                ).strip(),
                source=_source(artifact, None),
                entities=question.related,
                confidence=0.8,
            )
        )

    for conflict in artifact.conflicts:
        chunks.append(
            Chunk(
                chunk_id=chunk_id(artifact.run.id, conflict.id),
                kind=ChunkKind.CONFLICT,
                text=(
                    f"Conflict in {artifact.run.source.filename}: the audio says "
                    f"{conflict.heard} [heard] but the picture shows {conflict.seen} [seen]. "
                    f"Resolution: {conflict.resolution.value}."
                ),
                source=_source(artifact, None),
                keyframes=[
                    t.keyframe.path
                    for t in artifact.transitions
                    if t.keyframe.id in conflict.keyframes
                ],
                modalities_present=[Modality.HEARD, Modality.SEEN],
                confidence=0.5,
            )
        )

    return chunks
