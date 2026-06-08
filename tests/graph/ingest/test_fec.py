from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.entity_resolution.linker import resolve
from src.graph.ingest.fec import (
    member_crosswalk_source_record,
    parse_committee_master_line,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://www.fec.gov/files/bulk-downloads/2024/cm24.zip",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)

# Real FEC committee-master format (pipe-delimited, 15 fields).
_CM_LINE = (
    "C00000059|HALLMARK CARDS, INC. PAC (HALLPAC)|KLEIN, CASSIE MS.|"
    "2501 MCGEE, MD853||KANSAS CITY|MO|64108|B|Q|UNK|M|C|HALLMARK CARDS, INC.|"
)
_CM_LINE_WITH_CAND = (
    "C00100156|FRIENDS OF JANE DOE|DOE, JANE|1 MAIN ST||AUSTIN|TX|78701|P|N|DEM|Q|||H0TX01234"
)


# ── committee master parsing ───────────────────────────────────────


def test_parse_committee_master_line() -> None:
    record = parse_committee_master_line(_CM_LINE, provenance=_PROV)
    assert record.entity_type == "org"
    assert record.source_system == "fec"
    assert record.source_record_id == "C00000059"
    assert record.display_name == "HALLMARK CARDS, INC. PAC (HALLPAC)"
    assert record.region == "MO"
    assert record.external_id_keys == frozenset({"fec_committee:c00000059"})


def test_committee_does_not_absorb_candidate_id_as_own_identity() -> None:
    # The linked candidate id is a relationship, not the committee's identity,
    # so it must not become one of the committee's external ids (which would
    # wrongly merge the committee with the candidate).
    record = parse_committee_master_line(_CM_LINE_WITH_CAND, provenance=_PROV)
    assert record.external_id_keys == frozenset({"fec_committee:c00100156"})


def test_parse_committee_blank_region() -> None:
    line = "C00000059|ACME PAC|TREAS||||||B|Q|||C|ACME|"
    record = parse_committee_master_line(line, provenance=_PROV)
    assert record.region is None


def test_parse_committee_rejects_short_line() -> None:
    with pytest.raises(ValueError, match="committee master"):
        parse_committee_master_line("C00000059|ACME", provenance=_PROV)


def test_parse_committee_rejects_blank_id() -> None:
    line = "|ACME PAC|TREAS||||||B|Q|||C|ACME|"
    with pytest.raises(ValueError):
        parse_committee_master_line(line, provenance=_PROV)


# ── member crosswalk records ───────────────────────────────────────


def test_member_crosswalk_record_carries_both_strong_keys() -> None:
    record = member_crosswalk_source_record(
        bioguide_id="A000055",
        fec_candidate_id="H6AL04098",
        display_name="Robert Aderholt",
        provenance=_PROV,
    )
    assert record.entity_type == "person"
    assert record.external_id_keys == frozenset({"bioguide:a000055", "fec_candidate:h6al04098"})


def test_crosswalk_member_resolves_with_a_bioguide_member() -> None:
    # A crosswalk record (bioguide + fec) and a Congress member record (bioguide
    # only) for the same person resolve to one canonical entity via the shared
    # bioguide -- cross-source linkage on a strong key.
    from src.graph.ingest.congress_members import (
        member_provenance,
        source_record_from_member_entry,
    )
    from src.identity.current_member_lookup import CurrentMemberLookupEntry

    crosswalk = member_crosswalk_source_record(
        bioguide_id="A000055",
        fec_candidate_id="H6AL04098",
        display_name="Robert Aderholt",
        provenance=_PROV,
    )
    entry = CurrentMemberLookupEntry(
        bioguide_id="A000055",
        slug="robert-aderholt",
        name="Robert Aderholt",
        search_name="robert aderholt",
        state="AL",
        chamber="house",
    )
    congress = source_record_from_member_entry(
        entry,
        member_provenance(
            entry,
            snapshot_date=date(2024, 1, 1),
            content_sha256="b" * 64,
            first_observed_at=datetime(2024, 1, 2, tzinfo=UTC),
        ),
    )
    result = resolve([crosswalk, congress])
    assert len(result.clusters) == 1
