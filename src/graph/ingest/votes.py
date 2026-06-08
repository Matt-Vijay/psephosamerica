"""Adapt roll-call votes into provenance-carrying graph edges.

A vote is the canonical relational training fact: member X voted ``choice`` on
bill Y, knowable from the moment the roll call was public. :func:`vote_edge`
produces the ``vote`` :class:`~src.graph.edges.GraphEdge` connecting a member's
canonical Person ID to a bill's canonical ID, and :func:`vote_provenance` stamps
it so the leakage gate fires at the vote day — keeping vote history out of any
prediction made before the vote happened.

Choice vocabulary matches the prediction backtest's ``VoteOption``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

VOTE_CHOICES = frozenset({"yea", "nay", "present", "not_voting", "paired", "abstain"})

_CHOICE_ALIASES: dict[str, str] = {
    "yea": "yea",
    "yes": "yea",
    "aye": "yea",
    "y": "yea",
    "nay": "nay",
    "no": "nay",
    "n": "nay",
    "present": "present",
    "not_voting": "not_voting",
    "not voting": "not_voting",
    "notvoting": "not_voting",
    "absent": "not_voting",
    "nv": "not_voting",
    "paired": "paired",
    "abstain": "abstain",
}


def normalize_vote_choice(raw: str) -> str:
    """Map a source vote label to the canonical choice vocabulary."""
    key = " ".join(raw.strip().lower().split())
    choice = _CHOICE_ALIASES.get(key)
    if choice is None:
        raise ValueError(f"unrecognized vote choice: {raw!r}")
    return choice


def vote_provenance(
    *,
    source_url: str,
    content_sha256: str,
    vote_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a roll call; ``known_at`` defaults to the vote day (UTC).

    A roll-call result is public at the time of the vote, so the conservative
    leakage stamp is the start of the vote day.
    """
    default_known = datetime(vote_date.year, vote_date.month, vote_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=vote_date,
        known_at=known_at if known_at is not None else default_known,
    )


def vote_edge(
    *,
    member_canonical_id: str,
    bill_canonical_id: str,
    choice: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the ``vote`` edge from a member to a bill with a normalized choice."""
    return GraphEdge(
        edge_type="vote",
        src_id=member_canonical_id,
        dst_id=bill_canonical_id,
        attributes={"choice": normalize_vote_choice(choice)},
        provenance=provenance,
    )
