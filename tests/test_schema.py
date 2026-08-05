"""Schema tests.

Provenance on every claim is the product. These tests exist so that a refactor
that quietly drops a modality field fails loudly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stratum.schema.common import FACTUAL_MODALITIES, SCHEMA_VERSION, Claim, Modality
from stratum.schema.ids import (
    chunk_id,
    claim_id,
    entity_id,
    keyframe_id,
    step_id,
    transition_id,
)
from stratum.schema.transition import DraftClaim, TransitionDraft


def test_modality_has_exactly_four_values() -> None:
    # D3 was decided on 4 Aug 2026: `stated` joins the three in 03_OUTPUT_SCHEMA.
    # If this fails, the enum changed and a schema migration is owed.
    assert [m.value for m in Modality] == ["heard", "seen", "inferred", "stated"]


def test_stated_is_factual_but_inferred_is_not() -> None:
    assert Modality.STATED in FACTUAL_MODALITIES
    assert Modality.HEARD in FACTUAL_MODALITIES
    assert Modality.SEEN in FACTUAL_MODALITIES
    assert Modality.INFERRED not in FACTUAL_MODALITIES


def test_claim_requires_a_modality() -> None:
    with pytest.raises(ValidationError):
        Claim(id="cl_0000_00", text="a pump is present", confidence=0.5)  # type: ignore[call-arg]


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Claim(id="c", text="t", modality=Modality.SEEN, confidence=1.4)
    with pytest.raises(ValidationError):
        Claim(id="c", text="t", modality=Modality.SEEN, confidence=-0.1)


def test_extra_fields_are_rejected_not_dropped() -> None:
    """A model that invents a field must fail loudly and get repaired. Silently
    dropping the extra is exactly how provenance fields disappear."""
    with pytest.raises(ValidationError):
        DraftClaim(text="t", modality=Modality.SEEN, confidence=0.5, certainty="high")  # type: ignore[call-arg]


def test_transition_draft_requires_state_after() -> None:
    with pytest.raises(ValidationError):
        TransitionDraft(delta="something moved")  # type: ignore[call-arg]


def test_transition_draft_minimal_is_valid() -> None:
    draft = TransitionDraft(delta="a channel was added", state_after="one channel on the bench")
    assert draft.entities_added == []
    assert draft.no_meaningful_change is False


def test_ids_are_stable_and_shaped() -> None:
    assert keyframe_id(31) == "kf_0031"
    assert transition_id(31) == "tr_0031"
    assert claim_id(31, 2) == "cl_0031_02"
    assert step_id(4) == "st_04"
    assert chunk_id("9f3adeadbeef", "st_04") == "run_9f3adead::st_04"


def test_entity_ids_derive_from_the_label() -> None:
    """Derived, not counted — a re-run of the same video must produce the same
    ids or the Phase 3 corpus merge cannot work."""
    assert entity_id("PVC grow channel") == "ent_pvc_grow_channel"
    assert entity_id("PVC  Grow   Channel") == entity_id("pvc grow channel")
    assert entity_id("PVC grow channel", disambiguator=2) == "ent_pvc_grow_channel_2"
    assert entity_id("PVC grow channel", disambiguator=1) == "ent_pvc_grow_channel"


def test_entity_id_survives_a_label_with_no_usable_characters() -> None:
    assert entity_id("!!!").startswith("ent_unnamed_")


def test_schema_version_is_pinned() -> None:
    assert SCHEMA_VERSION == "1.0"
