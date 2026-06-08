"""Adapt Legistar event-item votes into municipal member->bill vote edges.

Many city and county councils record roll-call votes through Legistar: each
event item carries per-member votes (``VotePersonId`` — the same Legistar person
ID resolved in :mod:`src.graph.ingest.legistar` — and a ``VoteValueName``). This
closes the municipal-vote tier: a council member's canonical Person ID votes on
a matter's canonical Bill ID, exactly as a member of Congress votes on a federal
bill.

Municipal vote vocabularies vary; :func:`normalize_municipal_vote_choice` maps
them to the canonical choice set, returning ``None`` for non-member rows
(``Guest``, chair annotations) so they are dropped rather than mis-recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_CHOICE_ALIASES: dict[str, str] = {
    "aye": "yea",
    "yes": "yea",
    "yea": "yea",
    "no": "nay",
    "nay": "nay",
    "absent": "not_voting",
    "excused": "not_voting",
    "not voting": "not_voting",
    "recused": "abstain",
    "abstain": "abstain",
    "abstained": "abstain",
    "present": "present",
}


@dataclass(frozen=True)
class LegistarVote:
    """One parsed Legistar per-member vote."""

    person_id: int
    person_name: str
    vote_value: str


def parse_legistar_vote(record: dict[str, Any]) -> LegistarVote:
    """Parse one Legistar event-item vote record."""
    person_id = record.get("VotePersonId")
    if person_id is None:
        raise ValueError("Legistar vote is missing a VotePersonId")
    return LegistarVote(
        person_id=int(person_id),
        person_name=(record.get("VotePersonName") or "").strip(),
        vote_value=(record.get("VoteValueName") or "").strip(),
    )


def normalize_municipal_vote_choice(raw: str) -> str | None:
    """Map a municipal vote label to the canonical choice set (``None`` if not a vote)."""
    return _CHOICE_ALIASES.get(" ".join(raw.strip().lower().split()))


def legistar_vote_edge(
    *,
    member_canonical_id: str,
    bill_canonical_id: str,
    vote_value: str,
    vote_external_key: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge | None:
    """Build a municipal ``vote`` edge, or ``None`` for non-member vote rows."""
    choice = normalize_municipal_vote_choice(vote_value)
    if choice is None:
        return None
    return GraphEdge(
        edge_type="vote",
        src_id=member_canonical_id,
        dst_id=bill_canonical_id,
        attributes={"choice": choice},
        external_key=vote_external_key,
        provenance=provenance,
    )
