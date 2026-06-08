"""Adapt committee assignments into member -> committee edges.

A committee membership is a ``committee_membership`` edge with a role (member /
chair / ranking_member / vice_chair) and a term window: ``valid_from`` at the
assignment date, ``valid_to`` when the member left, so ``GraphEdge.covers(t)``
reports whether the member sat on the committee at time ``t``. ``known_at``
defaults to the assignment day (committee rosters are public when set).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_ROLE_ALIASES: dict[str, str] = {
    "member": "member",
    "chair": "chair",
    "chairman": "chair",
    "chairwoman": "chair",
    "chairperson": "chair",
    "ranking_member": "ranking_member",
    "ranking member": "ranking_member",
    "ranking": "ranking_member",
    "vice_chair": "vice_chair",
    "vice chair": "vice_chair",
    "vice chairman": "vice_chair",
}


def _normalize_role(raw: str) -> str:
    key = " ".join(raw.strip().lower().split())
    role = _ROLE_ALIASES.get(key)
    if role is None:
        raise ValueError(f"unknown committee role: {raw!r}")
    return role


def committee_membership_provenance(
    *,
    source_url: str,
    content_sha256: str,
    start_date: date,
    first_observed_at: datetime,
    end_date: date | None = None,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a committee assignment; ``valid_to`` is the departure date."""
    default_known = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=start_date,
        valid_to=end_date,
        known_at=known_at if known_at is not None else default_known,
    )


def committee_membership_edge(
    *,
    member_canonical_id: str,
    committee_canonical_id: str,
    role: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the committee-membership edge from a member to a committee."""
    return GraphEdge(
        edge_type="committee_membership",
        src_id=member_canonical_id,
        dst_id=committee_canonical_id,
        attributes={"role": _normalize_role(role)},
        provenance=provenance,
    )
