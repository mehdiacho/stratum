"""Stable, addressable identifiers.

Principle 2 of 03_OUTPUT_SCHEMA.md: every entity, transition, claim and step has
an ID that survives a re-run and can be cited. IDs are derived from position or
content, never from a counter that depends on call order, because the multi-video
merge in Phase 3 depends on them being reproducible.
"""

from __future__ import annotations

import hashlib
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def keyframe_id(index: int) -> str:
    """kf_0031 — index is the position in the retained keyframe sequence."""
    return f"kf_{index:04d}"


def transition_id(keyframe_index: int) -> str:
    """tr_0031 — one transition per keyframe, so the indices line up deliberately."""
    return f"tr_{keyframe_index:04d}"


def claim_id(transition_index: int, claim_index: int) -> str:
    """cl_0031_02 — scoped to its transition so a re-run of one transition is stable."""
    return f"cl_{transition_index:04d}_{claim_index:02d}"


def step_id(ordinal: int) -> str:
    return f"st_{ordinal:02d}"


def open_question_id(index: int) -> str:
    return f"oq_{index:02d}"


def conflict_id(index: int) -> str:
    return f"cf_{index:02d}"


def entity_id(label: str, *, disambiguator: int | None = None) -> str:
    """ent_pvc_grow_channel, or ent_pvc_grow_channel_2 when two distinct entities
    share a label.

    Derived from the label rather than a counter so that the same entity in a
    re-run gets the same id, which is what makes Phase 3 corpus merging possible.
    """
    slug = _SLUG_RE.sub("_", label.strip().lower()).strip("_")
    if not slug:
        slug = "unnamed_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    slug = slug[:48].rstrip("_")
    if disambiguator is not None and disambiguator > 1:
        return f"ent_{slug}_{disambiguator}"
    return f"ent_{slug}"


def chunk_id(run_id: str, unit_id: str) -> str:
    """run_9f3a::st_04 — the run prefix is short so chunks stay readable in a
    retrieval hit, and the full run id is carried in `source`."""
    return f"run_{run_id[:8]}::{unit_id}"
