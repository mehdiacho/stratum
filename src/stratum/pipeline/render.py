"""`artifact.md` — a template rendering of the canonical model.

Never hand-written, never edited. It is a view, and if it disagrees with
`artifact.json` the template is wrong.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from stratum.schema.artifact import Artifact, Conflict, Entity, Material, Step
from stratum.schema.common import Claim
from stratum.schema.transition import Transition, TransitionStatus
from stratum.util import format_duration, format_timestamp

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def render_markdown(artifact: Artifact, *, template_name: str = "artifact.md.j2") -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )

    by_kf = {t.keyframe.id: t for t in artifact.transitions}

    def transitions_for(step: Step) -> list[Transition]:
        seen: list[Transition] = []
        for ref in step.derived_from:
            transition = artifact.transition_by_id(ref)
            if (
                transition
                and transition.status is not TransitionStatus.FAILED
                and not transition.no_meaningful_change
            ):
                seen.append(transition)
        # One image per step keeps the document readable; the full frame set is in
        # the transitions section below it.
        return seen[:1]

    def claims_for(step: Step) -> list[Claim]:
        claims: list[Claim] = []
        for ref in step.derived_from:
            transition = artifact.transition_by_id(ref)
            if transition:
                claims.extend(transition.claims)
        return claims[:8]

    def qty(material: Material) -> str:
        if material.quantity is None:
            return "—"
        unit = f" {material.quantity.unit}" if material.quantity.unit else ""
        return f"{material.quantity.value:g}{unit}"

    def warrant(material: Material) -> str:
        if material.quantity is None:
            return "not quantified"
        q = material.quantity
        return f"{q.modality.value} ({q.confidence:.2f})"

    def attrs(entity: Entity) -> str:
        if not entity.attributes:
            return "—"
        return "; ".join(
            f"{name}: {value.value} — {value.modality.value} ({value.confidence:.2f})"
            for name, value in entity.attributes.items()
        )

    def conflict_line(conflict: Conflict) -> str:
        """Built here, not in the template.

        A template line ending in `{% endif %}` loses its trailing newline under
        trim_blocks, which silently runs the next block onto the end of the list.
        Anything with an optional tail belongs in Python.
        """
        when = f"**{kf_time(conflict.keyframes[0])}** — " if conflict.keyframes else ""
        note = f" {conflict.note}" if conflict.note else ""
        return (
            f"{when}audio says {conflict.heard}; the picture shows {conflict.seen}. "
            f"_{conflict.resolution.value}_.{note}"
        )

    def seen_span(entity: Entity) -> str:
        if not entity.mentions:
            # S7 named it but S6 never saw it. Say so rather than printing None.
            return "_named in synthesis only_"
        return f"{entity.first_seen_kf} → {entity.last_seen_kf} ({len(entity.mentions)})"

    def kf_time(keyframe_id: str) -> str:
        transition = by_kf.get(keyframe_id)
        return format_timestamp(transition.keyframe.timestamp_s) if transition else keyframe_id

    source = artifact.run.source
    byline = f" · {source.author}" if source.author else ""

    template = env.get_template(template_name)
    return (
        template.render(
            a=artifact,
            byline=byline,
            conflict_line=conflict_line,
            duration=format_duration(source.duration_s),
            failed_count=len(artifact.failed_transition_ids),
            ts=format_timestamp,
            transitions_for=transitions_for,
            claims_for=claims_for,
            qty=qty,
            warrant=warrant,
            attrs=attrs,
            seen_span=seen_span,
            kf_time=kf_time,
        ).strip()
        + "\n"
    )
