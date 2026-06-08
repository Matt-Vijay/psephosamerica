from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.contracts import build_entity_resolution_output
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.ingest.congress_members import (
    BIOGUIDE_SOURCE_SYSTEM,
    bioguide_source_url,
    member_provenance,
    source_record_from_member_entry,
    source_records_from_member_payload,
)
from src.identity.current_member_lookup import (
    CurrentMemberLookupEntry,
    CurrentMemberLookupPayload,
)

_OBSERVED = datetime(2024, 1, 10, tzinfo=UTC)


def _entry(
    bioguide: str, name: str, *, state: str = "CA", chamber: str = "house"
) -> CurrentMemberLookupEntry:
    return CurrentMemberLookupEntry(
        bioguide_id=bioguide,
        slug=name.lower().replace(" ", "-"),
        name=name,
        search_name=name.lower(),
        state=state,
        chamber=chamber,  # type: ignore[arg-type]
    )


def _prov(entry: CurrentMemberLookupEntry) -> object:
    return member_provenance(
        entry,
        snapshot_date=date(2024, 1, 1),
        content_sha256="a" * 64,
        first_observed_at=_OBSERVED,
    )


# ── url + single-entry mapping ─────────────────────────────────────


def test_bioguide_source_url() -> None:
    assert bioguide_source_url("P000197") == "https://bioguide.congress.gov/search/bio/P000197"


def test_source_record_maps_member_fields() -> None:
    entry = _entry("P000197", "Nancy Pelosi", state="CA")
    record = source_record_from_member_entry(entry, _prov(entry))
    assert record.source_system == BIOGUIDE_SOURCE_SYSTEM
    assert record.source_record_id == "P000197"
    assert record.entity_type == "person"
    assert record.display_name == "Nancy Pelosi"
    assert record.region == "CA"
    assert record.jurisdiction == "us-congress"
    assert record.external_id_keys == frozenset({"bioguide:p000197"})


def test_member_person_name_parses() -> None:
    entry = _entry("D000001", "Jane M. Doe")
    record = source_record_from_member_entry(entry, _prov(entry))
    name = record.person_name()
    assert name is not None
    assert name.given == "jane"
    assert name.family == "doe"


def test_member_provenance_uses_bioguide_url_and_snapshot() -> None:
    entry = _entry("P000197", "Nancy Pelosi")
    prov = member_provenance(
        entry,
        snapshot_date=date(2024, 1, 1),
        content_sha256="b" * 64,
        first_observed_at=_OBSERVED,
    )
    assert prov.source_url == "https://bioguide.congress.gov/search/bio/P000197"
    assert prov.valid_from == date(2024, 1, 1)
    assert prov.known_at == _OBSERVED  # conservative default: knowable when observed
    assert prov.content_sha256 == "b" * 64


def test_member_provenance_explicit_known_at() -> None:
    entry = _entry("P000197", "Nancy Pelosi")
    prov = member_provenance(
        entry,
        snapshot_date=date(2024, 1, 1),
        content_sha256="b" * 64,
        first_observed_at=_OBSERVED,
        known_at=datetime(2024, 1, 8, tzinfo=UTC),
    )
    assert prov.known_at == datetime(2024, 1, 8, tzinfo=UTC)


# ── batch payload mapping ──────────────────────────────────────────


def test_source_records_from_payload() -> None:
    payload = CurrentMemberLookupPayload(
        snapshot_date=date(2024, 1, 1),
        members=[_entry("P000197", "Nancy Pelosi"), _entry("S000148", "Chuck Schumer", state="NY")],
    )
    records = source_records_from_member_payload(payload, _prov)
    assert len(records) == 2
    assert {r.source_record_id for r in records} == {"P000197", "S000148"}


# ── end-to-end: repeat observations collapse to one canonical id ───


def test_two_snapshots_of_same_member_resolve_to_one_canonical_id() -> None:
    # Same bioguide, slightly different name spelling, from two snapshot dates.
    jan = source_record_from_member_entry(
        _entry("P000197", "Nancy Pelosi"),
        member_provenance(
            _entry("P000197", "Nancy Pelosi"),
            snapshot_date=date(2024, 1, 1),
            content_sha256="a" * 64,
            first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
        ),
    )
    jun = source_record_from_member_entry(
        _entry("P000197", "Nancy P. Pelosi"),
        member_provenance(
            _entry("P000197", "Nancy P. Pelosi"),
            snapshot_date=date(2024, 6, 1),
            content_sha256="c" * 64,
            first_observed_at=datetime(2024, 6, 10, tzinfo=UTC),
        ),
    )
    # NB: identical (system, source_id) -> same record_id, so a true re-observation
    # dedupes; here we exercise the cross-name merge by giving distinct source rows.
    assert jan.record_id == jun.record_id  # same bioguide -> same source-record identity

    result = resolve([jan, jun])
    assert len(result.clusters) == 1


def test_distinct_members_with_shared_bioguide_alias_merge() -> None:
    # Two different source rows (different source_record_id) that nonetheless
    # carry the same bioguide external id must merge on the strong key.
    a = source_record_from_member_entry(
        _entry("P000197", "Nancy Pelosi"), _prov(_entry("P000197", "Nancy Pelosi"))
    )
    b_entry = CurrentMemberLookupEntry(
        bioguide_id="P000197",
        slug="n-pelosi",
        name="N. Pelosi",
        search_name="n pelosi",
        state="CA",
        chamber="house",
    )
    # Build b with a different source_record_id but same bioguide external id.
    from src.graph.entity_resolution.records import ExternalId, SourceRecord

    b = SourceRecord(
        source_system="fec",
        source_record_id="H8CA05035",
        entity_type="person",
        display_name=b_entry.name,
        external_ids=[ExternalId(system="bioguide", value="P000197")],
        jurisdiction="us-congress",
        region="CA",
        provenance=_prov(b_entry),
    )
    result = resolve([a, b])
    assert len(result.clusters) == 1
    entity = build_canonical_entity(result.clusters[0], {a.record_id: a, b.record_id: b})
    assert entity is not None
    out = build_entity_resolution_output(entity)
    assert out.canonical_person_id == entity.canonical_id
    assert "bioguide:p000197" in out.external_ids
    assert len(out.source_anchors) == 2


def test_payload_resolves_distinct_members_separately() -> None:
    payload = CurrentMemberLookupPayload(
        snapshot_date=date(2024, 1, 1),
        members=[
            _entry("P000197", "Nancy Pelosi", state="CA"),
            _entry("S000148", "Chuck Schumer", state="NY"),
        ],
    )
    records = source_records_from_member_payload(payload, _prov)
    result = resolve(records)
    assert len(result.clusters) == 2  # different people, no shared id, different names


def test_member_provenance_naive_observed_at_rejected() -> None:
    entry = _entry("P000197", "Nancy Pelosi")
    with pytest.raises(ValueError):
        member_provenance(
            entry,
            snapshot_date=date(2024, 1, 1),
            content_sha256="a" * 64,
            first_observed_at=datetime(2024, 1, 10),  # naive
        )
