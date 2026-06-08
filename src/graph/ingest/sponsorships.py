"""Adapt bill sponsorships and cosponsorships into graph edges.

A sponsorship connects a member to a bill. It carries a nuance votes and
donations do not: a **cosponsorship can be withdrawn**, so the relationship has
a closed valid-time window. :func:`sponsorship_provenance` sets ``valid_from``
at the sponsoring action and ``valid_to`` at the withdrawal date (if any), so
``GraphEdge.covers(t)`` correctly reports whether the member was a (co)sponsor
at time ``t``. ``known_at`` defaults to the action day (sponsorships are public
when recorded).

The role selects the edge type: ``sponsor -> "sponsorship"``,
``cosponsor -> "cosponsorship"``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_ROLE_EDGE_TYPES: dict[str, str] = {
    "sponsor": "sponsorship",
    "cosponsor": "cosponsorship",
}


def sponsorship_provenance(
    *,
    source_url: str,
    content_sha256: str,
    action_date: date,
    first_observed_at: datetime,
    withdrawn_date: date | None = None,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a (co)sponsorship; ``valid_to`` is the withdrawal date."""
    default_known = datetime(action_date.year, action_date.month, action_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=action_date,
        valid_to=withdrawn_date,
        known_at=known_at if known_at is not None else default_known,
    )


def sponsorship_edge(
    *,
    member_canonical_id: str,
    bill_canonical_id: str,
    role: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the (co)sponsorship edge from a member to a bill."""
    edge_type = _ROLE_EDGE_TYPES.get(role.strip().lower())
    if edge_type is None:
        raise ValueError(f"unknown sponsorship role: {role!r} (expected 'sponsor' or 'cosponsor')")
    return GraphEdge(
        edge_type=edge_type,
        src_id=member_canonical_id,
        dst_id=bill_canonical_id,
        provenance=provenance,
    )
