from __future__ import annotations

from datetime import UTC, datetime

from src.graph.cdc import diff_outputs
from src.graph.entity_resolution.records import SourceRecord
from src.graph.materialize import materialize_person_nodes
from src.graph.provenance import ProvenanceEnvelope


def _rec(name: str, rid: str, *, bioguide: str, known: datetime) -> SourceRecord:
    return SourceRecord(
        source_system="congress_bioguide",
        source_record_id=rid,
        entity_type="person",
        display_name=name,
        external_ids=[{"system": "bioguide", "value": bioguide}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url=f"https://bioguide.congress.gov/search/bio/{bioguide}",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )


def test_materialize_keys_nodes_by_persistent_id() -> None:
    records = [
        _rec("Jane Doe", "J1", bioguide="J000001", known=datetime(2024, 1, 1, tzinfo=UTC)),
        _rec("John Smith", "S1", bioguide="S000001", known=datetime(2024, 1, 1, tzinfo=UTC)),
    ]
    nodes, assignment = materialize_person_nodes(records)
    assert len(nodes) == 2
    # Every node's canonical_id is a persistent assigned ID.
    assert {n.canonical_id for n in nodes} == set(assignment.by_record.values())
    for node in nodes:
        assert node.canonical_person_id == node.canonical_id


def test_materialize_collapses_shared_bioguide() -> None:
    records = [
        _rec("Jane Doe", "src-a", bioguide="J000001", known=datetime(2024, 1, 1, tzinfo=UTC)),
        # Different source row, same bioguide -> one canonical person.
        _rec("J. Doe", "src-b", bioguide="J000001", known=datetime(2024, 2, 1, tzinfo=UTC)),
    ]
    nodes, _ = materialize_person_nodes(records)
    assert len(nodes) == 1
    assert len(nodes[0].source_anchors) == 2


def test_persistent_id_is_stable_across_runs_and_cdc_sees_update() -> None:
    # Run 1: Jane known from one source.
    run1 = [_rec("Jane Doe", "src-a", bioguide="J000001", known=datetime(2024, 1, 1, tzinfo=UTC))]
    nodes1, assignment1 = materialize_person_nodes(run1)
    stable_id = nodes1[0].canonical_id

    # Run 2: a second source for the same person arrives (entity grows).
    run2 = run1 + [
        _rec("Jane M. Doe", "src-b", bioguide="J000001", known=datetime(2024, 6, 1, tzinfo=UTC))
    ]
    nodes2, _ = materialize_person_nodes(run2, prior_assignment=assignment1)

    # Same stable canonical ID despite the membership (and content hash) change.
    assert len(nodes2) == 1
    assert nodes2[0].canonical_id == stable_id

    # The CDC feed reports an update (not a create) on the stable key.
    prev = {n.canonical_id: n for n in nodes1}
    curr = {n.canonical_id: n for n in nodes2}
    deltas = diff_outputs(prev, curr)
    assert len(deltas) == 1
    assert deltas[0].change_type == "updated"
    assert deltas[0].canonical_id == stable_id
    assert deltas[0].added_source_record_ids  # the new source row appears


def test_materialize_empty() -> None:
    nodes, assignment = materialize_person_nodes([])
    assert nodes == []
    assert assignment.by_record == {}


def test_nodes_sorted_by_canonical_id() -> None:
    records = [
        _rec("Zara Zog", "Z1", bioguide="Z000001", known=datetime(2024, 1, 1, tzinfo=UTC)),
        _rec("Amy Adams", "A1", bioguide="A000001", known=datetime(2024, 1, 1, tzinfo=UTC)),
    ]
    nodes, _ = materialize_person_nodes(records)
    ids = [n.canonical_id for n in nodes]
    assert ids == sorted(ids)
