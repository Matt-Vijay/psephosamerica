from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.graph.cdc import EntityDelta, diff_outputs
from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput


def _anchor(record_id: str, *, sha: str = "a") -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="congress",
        record_id=record_id,
        source_url=f"https://example.gov/{record_id}",
        content_sha256=sha * 64,
        content_address=f"sha256/{sha}{sha}/{sha}{sha}/" + sha * 64,
        known_at=datetime(2024, 1, 5, tzinfo=UTC),
        valid_from=date(2024, 1, 1),
    )


def _output(
    *,
    display_name: str = "Jane Doe",
    external_ids: list[str] | None = None,
    anchors: list[ContractSourceAnchor] | None = None,
    enrichment: str = "pending",
) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id="ce-stable",
        entity_type="person",
        display_name=display_name,
        external_ids=external_ids if external_ids is not None else ["bioguide:d1"],
        known_at=datetime(2024, 1, 5, tzinfo=UTC),
        source_anchors=anchors or [_anchor("sr-1")],
        enrichment_status=enrichment,  # type: ignore[arg-type]
    )


# ── created / removed ──────────────────────────────────────────────


def test_created_entity() -> None:
    deltas = diff_outputs({}, {"ce-1": _output()})
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.change_type == "created"
    assert delta.canonical_id == "ce-1"
    assert delta.added_external_ids == ["bioguide:d1"]
    assert delta.added_source_record_ids == ["sr-1"]
    assert delta.new_display_name == "Jane Doe"


def test_removed_entity() -> None:
    deltas = diff_outputs({"ce-1": _output()}, {})
    assert len(deltas) == 1
    assert deltas[0].change_type == "removed"
    assert deltas[0].removed_external_ids == ["bioguide:d1"]
    assert deltas[0].removed_source_record_ids == ["sr-1"]
    assert deltas[0].old_display_name == "Jane Doe"


# ── unchanged emits nothing ────────────────────────────────────────


def test_unchanged_entity_emits_no_delta() -> None:
    out = _output()
    assert diff_outputs({"ce-1": out}, {"ce-1": _output()}) == []


# ── field-level updates ────────────────────────────────────────────


def test_added_external_id() -> None:
    prev = {"ce-1": _output(external_ids=["bioguide:d1"])}
    curr = {"ce-1": _output(external_ids=["bioguide:d1", "fec:h1"])}
    delta = diff_outputs(prev, curr)[0]
    assert delta.change_type == "updated"
    assert delta.added_external_ids == ["fec:h1"]
    assert delta.removed_external_ids == []


def test_removed_external_id() -> None:
    prev = {"ce-1": _output(external_ids=["bioguide:d1", "fec:h1"])}
    curr = {"ce-1": _output(external_ids=["bioguide:d1"])}
    delta = diff_outputs(prev, curr)[0]
    assert delta.removed_external_ids == ["fec:h1"]


def test_new_source_anchor() -> None:
    prev = {"ce-1": _output(anchors=[_anchor("sr-1")])}
    curr = {"ce-1": _output(anchors=[_anchor("sr-1"), _anchor("sr-2", sha="b")])}
    delta = diff_outputs(prev, curr)[0]
    assert delta.added_source_record_ids == ["sr-2"]


def test_display_name_change() -> None:
    prev = {"ce-1": _output(display_name="Jane Doe")}
    curr = {"ce-1": _output(display_name="Jane M. Doe")}
    delta = diff_outputs(prev, curr)[0]
    assert delta.old_display_name == "Jane Doe"
    assert delta.new_display_name == "Jane M. Doe"


def test_enrichment_field_change_is_detected() -> None:
    # Same identity/anchors/name/status, but the dossier was regenerated.
    prev = {"ce-1": _output()}
    curr_out = _output()
    curr_out = curr_out.model_copy(update={"dossier_json": {"summary": "new"}})
    delta = diff_outputs(prev, {"ce-1": curr_out})[0]
    assert delta.change_type == "updated"
    assert delta.enrichment_changed is True


def test_dossier_as_of_only_change_is_not_a_delta() -> None:
    # A clock-only advance (dossier as_of) with identical facts/embeddings must
    # NOT churn the entity through the feed.
    curr_out = _output().model_copy(
        update={"dossier_json": {"summary": "s", "claims": [], "as_of": "2099-01-01T00:00:00Z"}}
    )
    prev_out = _output().model_copy(
        update={"dossier_json": {"summary": "s", "claims": [], "as_of": "2024-01-01T00:00:00Z"}}
    )
    assert diff_outputs({"ce-1": prev_out}, {"ce-1": curr_out}) == []


def test_structural_embedding_change_is_detected() -> None:
    prev = {"ce-1": _output()}
    curr_out = _output().model_copy(update={"structural_embedding": [1.0, 2.0]})
    delta = diff_outputs(prev, {"ce-1": curr_out})[0]
    assert delta.enrichment_changed is True


def test_semantic_embedding_change_is_detected() -> None:
    # Adding the dual-emit semantic vector must fire enrichment_changed so Track
    # B's hot-swap watcher sees it.
    prev = {"ce-1": _output()}
    curr_out = _output().model_copy(update={"semantic_embedding": [0.5] * 384})
    delta = diff_outputs(prev, {"ce-1": curr_out})[0]
    assert delta.enrichment_changed is True


def test_enrichment_status_change() -> None:
    prev = {"ce-1": _output(enrichment="pending")}
    curr = {
        "ce-1": EntityResolutionOutput(
            canonical_id="ce-stable",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=["bioguide:d1"],
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
            source_anchors=[_anchor("sr-1")],
            enrichment_status="ready",
            dossier_json={"x": 1},
            dossier_embedding=[0.1],
            structural_embedding=[0.2],
        )
    }
    delta = diff_outputs(prev, curr)[0]
    assert delta.old_enrichment_status == "pending"
    assert delta.new_enrichment_status == "ready"


# ── multiple entities, deterministic order ─────────────────────────


def test_multiple_entities_sorted_by_key() -> None:
    prev = {"ce-b": _output()}
    curr = {"ce-a": _output(), "ce-b": _output(display_name="Changed")}
    deltas = diff_outputs(prev, curr)
    assert [d.canonical_id for d in deltas] == ["ce-a", "ce-b"]
    assert deltas[0].change_type == "created"
    assert deltas[1].change_type == "updated"


def test_delta_round_trips_json() -> None:
    delta = diff_outputs({}, {"ce-1": _output()})[0]
    assert EntityDelta.model_validate_json(delta.model_dump_json()) == delta


# ── invariants ─────────────────────────────────────────────────────


def test_updated_delta_requires_a_change() -> None:
    with pytest.raises(ValidationError, match="at least one change"):
        EntityDelta(canonical_id="ce-1", change_type="updated")


def test_created_delta_is_valid_with_only_additions() -> None:
    delta = EntityDelta(
        canonical_id="ce-1",
        change_type="created",
        added_external_ids=["bioguide:d1"],
        new_display_name="Jane Doe",
    )
    assert delta.change_type == "created"
