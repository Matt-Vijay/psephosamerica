from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.fec_linkage import (
    CandidateCommitteeLink,
    committee_affiliation_edge,
    parse_candidate_committee_linkage_line,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)

# Real FEC candidate-committee linkage (ccl.txt) format, 7 pipe-delimited fields.
_CCL = "H0AL01055|2024|2024|C00697789|H|P|247126"


# ── parsing ────────────────────────────────────────────────────────


def test_parse_linkage_line() -> None:
    link = parse_candidate_committee_linkage_line(_CCL)
    assert isinstance(link, CandidateCommitteeLink)
    assert link.candidate_id == "H0AL01055"
    assert link.committee_id == "C00697789"
    assert link.committee_type == "H"
    assert link.designation == "P"
    assert link.linkage_id == "247126"


def test_parse_rejects_short_line() -> None:
    with pytest.raises(ValueError, match="linkage line"):
        parse_candidate_committee_linkage_line("H0AL01055|2024")


def test_parse_rejects_blank_ids() -> None:
    with pytest.raises(ValueError):
        parse_candidate_committee_linkage_line("|2024|2024|C00697789|H|P|247126")
    with pytest.raises(ValueError):
        parse_candidate_committee_linkage_line("H0AL01055|2024|2024||H|P|247126")


# ── affiliation edge ───────────────────────────────────────────────


def test_affiliation_edge_connects_committee_to_candidate() -> None:
    edge = committee_affiliation_edge(
        committee_canonical_id="ce-committee",
        candidate_canonical_id="ce-candidate",
        designation="P",
        linkage_id="247126",
        provenance=_PROV,
    )
    assert edge.edge_type == "affiliated_committee"
    assert edge.src_id == "ce-committee"
    assert edge.dst_id == "ce-candidate"
    assert edge.attributes["designation"] == "principal"
    assert edge.external_key == "247126"


@pytest.mark.parametrize(
    ("code", "expected"),
    [("P", "principal"), ("A", "authorized"), ("J", "joint_fundraiser"), ("D", "leadership_pac")],
)
def test_designation_decoded(code: str, expected: str) -> None:
    edge = committee_affiliation_edge(
        committee_canonical_id="ce-c",
        candidate_canonical_id="ce-p",
        designation=code,
        linkage_id="1",
        provenance=_PROV,
    )
    assert edge.attributes["designation"] == expected


def test_unknown_designation_kept_normalized() -> None:
    edge = committee_affiliation_edge(
        committee_canonical_id="ce-c",
        candidate_canonical_id="ce-p",
        designation="X",
        linkage_id="1",
        provenance=_PROV,
    )
    assert edge.attributes["designation"] == "x"


def test_affiliation_self_loop_rejected() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        committee_affiliation_edge(
            committee_canonical_id="ce-x",
            candidate_canonical_id="ce-x",
            designation="P",
            linkage_id="1",
            provenance=_PROV,
        )
