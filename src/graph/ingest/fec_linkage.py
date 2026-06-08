"""Parse FEC candidate-committee linkages (``ccl.txt``) into Person<->Org edges.

The linkage file ties each candidate (FEC candidate ID) to the committees that
raise and spend on their behalf (principal campaign committee, authorized
committees, leadership PACs). Resolved, it connects the canonical *person* graph
to the canonical *org* graph — the affiliation edge that lets donor flows into a
committee attach to the official it serves.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_CAND_ID = 0
_COMMITTEE_ID = 3
_COMMITTEE_TYPE = 4
_DESIGNATION = 5
_LINKAGE_ID = 6
_MIN_FIELDS = 7

_DESIGNATIONS: dict[str, str] = {
    "p": "principal",
    "a": "authorized",
    "j": "joint_fundraiser",
    "d": "leadership_pac",
    "b": "lobbyist_registrant_pac",
    "u": "unauthorized",
}


@dataclass(frozen=True)
class CandidateCommitteeLink:
    """One parsed candidate <-> committee linkage."""

    candidate_id: str
    committee_id: str
    committee_type: str
    designation: str
    linkage_id: str


def parse_candidate_committee_linkage_line(line: str) -> CandidateCommitteeLink:
    """Parse one ``ccl.txt`` line into a :class:`CandidateCommitteeLink`."""
    fields = line.rstrip("\n").split("|")
    if len(fields) < _MIN_FIELDS:
        raise ValueError(f"linkage line has {len(fields)} fields, expected >= {_MIN_FIELDS}")
    candidate_id = fields[_CAND_ID].strip()
    committee_id = fields[_COMMITTEE_ID].strip()
    if not candidate_id or not committee_id:
        raise ValueError("linkage line has a blank candidate or committee id")
    return CandidateCommitteeLink(
        candidate_id=candidate_id,
        committee_id=committee_id,
        committee_type=fields[_COMMITTEE_TYPE].strip(),
        designation=fields[_DESIGNATION].strip(),
        linkage_id=fields[_LINKAGE_ID].strip(),
    )


def committee_affiliation_edge(
    *,
    committee_canonical_id: str,
    candidate_canonical_id: str,
    designation: str,
    linkage_id: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the committee -> candidate affiliation edge."""
    decoded = _DESIGNATIONS.get(designation.strip().lower(), designation.strip().lower())
    return GraphEdge(
        edge_type="affiliated_committee",
        src_id=committee_canonical_id,
        dst_id=candidate_canonical_id,
        attributes={"designation": decoded},
        external_key=linkage_id,
        provenance=provenance,
    )
