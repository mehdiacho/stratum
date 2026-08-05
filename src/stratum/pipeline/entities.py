"""Entity tracking across a run.

The model reports entities by natural-language label and never invents an ID. This
registry turns labels into stable IDs, merges repeat mentions, and accumulates
provenanced attributes.

Stability matters beyond tidiness: the Phase 3 corpus merge reconciles entities
across videos, and it can only do that if the same thing gets the same ID on every
re-run of the same video.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from stratum.schema.artifact import Entity
from stratum.schema.common import Modality, ProvenancedValue
from stratum.schema.ids import entity_id
from stratum.schema.transition import DraftEntity

_NORMALISE = re.compile(r"[^a-z0-9]+")

# Words that carry no identity. "the pump" and "a pump" are the same pump.
_STOPWORDS = frozenset({"a", "an", "the", "some", "this", "that", "these", "those"})


def normalise_label(label: str) -> str:
    tokens = [t for t in _NORMALISE.sub(" ", label.lower()).split() if t and t not in _STOPWORDS]
    return " ".join(tokens)


class EntityRegistry:
    """Persisted after every keyframe so a resume picks up the same IDs."""

    def __init__(self) -> None:
        self._by_key: dict[str, Entity] = {}
        self._order: list[str] = []

    # -- persistence --------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {
                "order": self._order,
                "entities": {k: v.model_dump(mode="json") for k, v in self._by_key.items()},
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> EntityRegistry:
        registry = cls()
        raw = json.loads(text)
        registry._order = list(raw.get("order", []))
        registry._by_key = {
            k: Entity.model_validate(v) for k, v in (raw.get("entities") or {}).items()
        }
        return registry

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.to_json() + "\n", encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> EntityRegistry:
        if not path.is_file():
            return cls()
        return cls.from_json(path.read_text(encoding="utf-8"))

    # -- lookup -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_key)

    def all(self) -> list[Entity]:
        return [self._by_key[k] for k in self._order]

    def by_label(self, label: str) -> Entity | None:
        return self._by_key.get(normalise_label(label))

    def id_for_label(self, label: str) -> str | None:
        entity = self.by_label(label)
        return entity.id if entity else None

    # -- mutation -----------------------------------------------------------

    def observe(self, draft: DraftEntity, *, kf_id: str) -> str:
        """Record a mention. Returns the stable entity id."""
        key = normalise_label(draft.label)
        if not key:
            key = normalise_label(draft.kind) or "unnamed"

        entity = self._by_key.get(key)
        if entity is None:
            entity = Entity(
                id=entity_id(key),
                label=draft.label.strip(),
                kind=draft.kind or "object",
                first_seen_kf=kf_id,
                last_seen_kf=kf_id,
                mentions=[kf_id],
            )
            self._by_key[key] = entity
            self._order.append(key)
        else:
            entity.last_seen_kf = kf_id
            if kf_id not in entity.mentions:
                entity.mentions.append(kf_id)
            alias = draft.label.strip()
            if alias and alias != entity.label and alias not in entity.aliases:
                entity.aliases.append(alias)

        for name, value in draft.attrs.items():
            existing = entity.attributes.get(name)
            incoming = ProvenancedValue(
                value=str(value),
                modality=draft.modality,
                confidence=draft.confidence,
                source_kf=kf_id,
            )
            # Keep the better-warranted value. A `seen` measurement beats an
            # `inferred` one regardless of the confidence the model attached.
            if existing is None or _warrant(incoming) > _warrant(existing):
                entity.attributes[name] = incoming

        return entity.id

    def register_synthetic(self, label: str, *, kind: str = "material") -> str:
        """Register an entity that S7 named but S6 never observed.

        The alternative is a `Material` pointing at an entity id that appears
        nowhere in `artifact.entities`, and a dangling reference in the canonical
        artifact is worse than an entity with no keyframes: the entity at least
        says honestly that it has no mentions. S7 also records it as a dropped
        reference, because a model naming things it never saw is a signal.
        """
        key = normalise_label(label) or "unnamed"
        existing = self._by_key.get(key)
        if existing is not None:
            return existing.id
        entity = Entity(id=entity_id(key), label=label.strip(), kind=kind, mentions=[])
        self._by_key[key] = entity
        self._order.append(key)
        return entity.id

    def mark_removed(self, label: str, *, kf_id: str) -> str | None:
        entity = self.by_label(label)
        if entity is None:
            return None
        if kf_id not in entity.mentions:
            entity.mentions.append(kf_id)
        entity.last_seen_kf = kf_id
        return entity.id


_MODALITY_WARRANT = {
    Modality.SEEN: 3.0,
    Modality.HEARD: 2.5,
    Modality.STATED: 2.5,
    Modality.INFERRED: 1.0,
}


def _warrant(value: ProvenancedValue) -> float:
    return _MODALITY_WARRANT.get(value.modality, 1.0) + value.confidence
