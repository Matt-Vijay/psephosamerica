from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.provenance import ProvenanceEnvelope


def _prov(
    known: datetime, *, url: str = "https://example.gov/x", sha: str = "e"
) -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source_url=url,
        content_sha256=sha * 64,
        first_observed_at=known,
        valid_from=date(2024, 1, 1),
        known_at=known,
    )


def _rec(
    name: str,
    rid: str,
    *,
    system: str = "sys",
    entity_type: str = "person",
    ext: list[dict[str, str]] | None = None,
    known: datetime | None = None,
    url: str = "https://example.gov/x",
    sha: str = "e",
) -> SourceRecord:
    return SourceRecord(
        source_system=system,
        source_record_id=rid,
        entity_type=entity_type,  # type: ignore[arg-type]
        display_name=name,
        external_ids=ext or [],
        jurisdiction="us",
        provenance=_prov(known or datetime(2024, 1, 5, tzinfo=UTC), url=url, sha=sha),
    )


def _by_id(*records: SourceRecord) -> dict[str, SourceRecord]:
    return {r.record_id: r for r in records}


# ── building from a cluster ────────────────────────────────────────


def test_build_picks_most_complete_display_name() -> None:
    a = _rec("Jane Doe", "1", ext=[{"system": "bioguide", "value": "D1"}])
    b = _rec("Jane M. Doe Jr.", "2", ext=[{"system": "bioguide", "value": "D1"}])
    result = resolve([a, b])
    assert len(result.clusters) == 1
    entity = build_canonical_entity(result.clusters[0], _by_id(a, b))
    assert entity is not None
    assert entity.display_name == "Jane M. Doe Jr."
    assert entity.entity_type == "person"


def test_build_merges_external_ids_across_sources() -> None:
    a = _rec("Jane Doe", "1", ext=[{"system": "bioguide", "value": "D1"}])
    b = _rec(
        "Jane Doe",
        "2",
        system="fec",
        ext=[{"system": "fec", "value": "H1"}, {"system": "bioguide", "value": "D1"}],
    )
    # Force them together with a shared bioguide.
    cluster = resolve([a, b]).clusters[0]
    entity = build_canonical_entity(cluster, _by_id(a, b))
    assert entity is not None
    assert [(e.system, e.value) for e in entity.external_ids] == [
        ("bioguide", "D1"),
        ("fec", "H1"),
    ]


def test_canonical_id_matches_cluster_id() -> None:
    a = _rec("Jane M. Doe", "1")
    b = _rec("Doe, Jane M.", "2")
    cluster = resolve([a, b]).clusters[0]
    entity = build_canonical_entity(cluster, _by_id(a, b))
    assert entity is not None
    assert entity.canonical_id == cluster.canonical_id


def test_source_anchors_carry_provenance() -> None:
    a = _rec("Jane Doe", "1", url="https://www.congress.gov/member/1", sha="a")
    entity = build_canonical_entity(resolve([a]).clusters[0], _by_id(a))
    assert entity is not None
    assert len(entity.anchors) == 1
    anchor = entity.anchors[0]
    assert anchor.record_id == a.record_id
    assert anchor.source_system == "sys"
    assert anchor.provenance.source_url == "https://www.congress.gov/member/1"
    assert anchor.provenance.content_sha256 == "a" * 64


def test_anchors_sorted_by_record_id() -> None:
    a = _rec("Jane M. Doe", "1")
    b = _rec("Doe, Jane M.", "2")
    entity = build_canonical_entity(resolve([a, b]).clusters[0], _by_id(a, b))
    assert entity is not None
    ids = [anchor.record_id for anchor in entity.anchors]
    assert ids == sorted(ids)


# ── known_at / leakage ─────────────────────────────────────────────


def test_known_at_is_earliest_source_observation() -> None:
    a = _rec("Jane M. Doe", "1", known=datetime(2024, 3, 1, tzinfo=UTC))
    b = _rec("Doe, Jane M.", "2", known=datetime(2024, 1, 5, tzinfo=UTC))
    entity = build_canonical_entity(resolve([a, b]).clusters[0], _by_id(a, b))
    assert entity is not None
    assert entity.known_at == datetime(2024, 1, 5, tzinfo=UTC)


def test_known_as_of_gate() -> None:
    a = _rec("Jane Doe", "1", known=datetime(2024, 1, 5, tzinfo=UTC))
    entity = build_canonical_entity(resolve([a]).clusters[0], _by_id(a))
    assert entity is not None
    assert entity.known_as_of(datetime(2024, 2, 1, tzinfo=UTC)) is True
    assert entity.known_as_of(datetime(2023, 1, 1, tzinfo=UTC)) is False


def test_known_as_of_rejects_naive_cutoff() -> None:
    a = _rec("Jane Doe", "1")
    entity = build_canonical_entity(resolve([a]).clusters[0], _by_id(a))
    assert entity is not None
    with pytest.raises(ValueError, match="timezone-aware"):
        entity.known_as_of(datetime(2024, 2, 1))


def test_source_record_ids_match_anchors() -> None:
    a = _rec("Jane M. Doe", "1")
    b = _rec("Doe, Jane M.", "2")
    entity = build_canonical_entity(resolve([a, b]).clusters[0], _by_id(a, b))
    assert entity is not None
    assert entity.source_record_ids == tuple(anchor.record_id for anchor in entity.anchors)
    assert set(entity.source_record_ids) == {a.record_id, b.record_id}


# ── as_of time-travel projection ───────────────────────────────────


def test_as_of_drops_sources_not_yet_known() -> None:
    early = _rec(
        "Jane Doe",
        "1",
        ext=[{"system": "bioguide", "value": "D1"}],
        known=datetime(2024, 1, 5, tzinfo=UTC),
    )
    late = _rec(
        "Jane M. Doe Jr.",
        "2",
        ext=[{"system": "bioguide", "value": "D1"}, {"system": "fec", "value": "H1"}],
        known=datetime(2024, 6, 1, tzinfo=UTC),
    )
    cluster = resolve([early, late]).clusters[0]
    by_id = _by_id(early, late)

    # As of February, only the early record is known.
    snapshot = build_canonical_entity(cluster, by_id, as_of=datetime(2024, 2, 1, tzinfo=UTC))
    assert snapshot is not None
    assert snapshot.display_name == "Jane Doe"  # the later, fuller name isn't known yet
    assert [e.system for e in snapshot.external_ids] == ["bioguide"]  # no fec yet
    assert len(snapshot.anchors) == 1
    assert snapshot.known_as_of(datetime(2024, 2, 1, tzinfo=UTC)) is True

    # As of July, both are known.
    full = build_canonical_entity(cluster, by_id, as_of=datetime(2024, 7, 1, tzinfo=UTC))
    assert full is not None
    assert full.display_name == "Jane M. Doe Jr."
    assert len(full.anchors) == 2


def test_as_of_before_any_source_returns_none() -> None:
    a = _rec("Jane Doe", "1", known=datetime(2024, 1, 5, tzinfo=UTC))
    cluster = resolve([a]).clusters[0]
    snapshot = build_canonical_entity(cluster, _by_id(a), as_of=datetime(2023, 1, 1, tzinfo=UTC))
    assert snapshot is None


def test_as_of_canonical_id_reflects_surviving_membership() -> None:
    early = _rec(
        "Jane Doe",
        "1",
        ext=[{"system": "bioguide", "value": "D1"}],
        known=datetime(2024, 1, 5, tzinfo=UTC),
    )
    late = _rec(
        "Jane Doe",
        "2",
        ext=[{"system": "bioguide", "value": "D1"}],
        known=datetime(2024, 6, 1, tzinfo=UTC),
    )
    cluster = resolve([early, late]).clusters[0]
    by_id = _by_id(early, late)
    snapshot = build_canonical_entity(cluster, by_id, as_of=datetime(2024, 2, 1, tzinfo=UTC))
    full = build_canonical_entity(cluster, by_id)
    assert snapshot is not None and full is not None
    # Different membership -> different content-addressed canonical id.
    assert snapshot.canonical_id != full.canonical_id


# ── org entities ───────────────────────────────────────────────────


def test_org_canonical_entity() -> None:
    a = _rec("Acme PAC", "1", entity_type="org", ext=[{"system": "fec_committee", "value": "C1"}])
    b = _rec(
        "Acme Political Action Committee",
        "2",
        entity_type="org",
        ext=[{"system": "fec_committee", "value": "C1"}],
    )
    cluster = resolve([a, b]).clusters[0]
    entity = build_canonical_entity(cluster, _by_id(a, b))
    assert entity is not None
    assert entity.entity_type == "org"
    # Longer name wins for orgs.
    assert entity.display_name == "Acme Political Action Committee"
