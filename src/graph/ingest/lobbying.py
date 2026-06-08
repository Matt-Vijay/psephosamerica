"""Adapt LDA lobbying disclosures into client -> bill lobbying edges.

The blueprint wants lobbying disclosures filed *on the specific bill*. A lobbying
edge connects the client (the org on whose behalf lobbying happened) to the bill
lobbied on, carrying the issue area and — when reported — the spend and the
registrant (lobbying firm). Identity is the LDA filing ID, so each quarterly
filing is a distinct edge.

Disclosure-lag leakage again: lobbying activity is public when the quarterly LDA
report is filed, not when the contact happened. ``valid_from`` is the activity
date; ``known_at`` is the filing date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope


def lobbying_provenance(
    *,
    source_url: str,
    content_sha256: str,
    activity_date: date,
    filed_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a lobbying filing; ``known_at`` defaults to the filing date."""
    disclosed = datetime(filed_date.year, filed_date.month, filed_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=activity_date,
        known_at=known_at if known_at is not None else disclosed,
    )


def lobbying_edge(
    *,
    client_canonical_id: str,
    bill_canonical_id: str,
    issue_area: str,
    filing_id: str,
    provenance: ProvenanceEnvelope,
    amount_cents: int | None = None,
    registrant: str | None = None,
) -> GraphEdge:
    """Build the lobbying-contact edge from a client to a bill."""
    if not issue_area.strip():
        raise ValueError("issue_area must be non-blank")
    attributes = {"issue_area": issue_area.strip()}
    if amount_cents is not None:
        if isinstance(amount_cents, bool) or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive integer")
        attributes["amount_cents"] = str(amount_cents)
    if registrant is not None and registrant.strip():
        attributes["registrant"] = registrant.strip()
    return GraphEdge(
        edge_type="lobbying_contact",
        src_id=client_canonical_id,
        dst_id=bill_canonical_id,
        attributes=attributes,
        external_key=filing_id,
        provenance=provenance,
    )
