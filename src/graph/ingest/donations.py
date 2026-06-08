"""Adapt campaign contributions into provenance-carrying donation edges.

A donation is a ``donor -> recipient`` edge, but its leakage semantics differ
sharply from a vote: a contribution is *not publicly knowable when it is made*,
only when the receiving committee files the report that discloses it — often
weeks or months later. :func:`donation_provenance` therefore stamps
``valid_from`` at the contribution date (when it happened in the world) and
``known_at`` at the report-filing date (when it became public), so a prediction
made in the disclosure gap cannot leak a donation that had not yet been
disclosed.

Amounts are carried as integer cents to avoid float drift; the source
transaction ID becomes the edge's ``external_key`` so multiple same-day
contributions between a pair stay distinct.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope


def donation_provenance(
    *,
    source_url: str,
    content_sha256: str,
    contribution_date: date,
    report_filed_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a contribution; ``known_at`` defaults to the filing date.

    The disclosure-lag rule: the public learns of the gift when the report is
    filed, not when the money moved.
    """
    disclosed = datetime(
        report_filed_date.year, report_filed_date.month, report_filed_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=contribution_date,
        known_at=known_at if known_at is not None else disclosed,
    )


def donation_edge(
    *,
    donor_canonical_id: str,
    recipient_canonical_id: str,
    amount_cents: int,
    transaction_id: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the ``donation`` edge from a donor to a recipient committee."""
    if isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")
    return GraphEdge(
        edge_type="donation",
        src_id=donor_canonical_id,
        dst_id=recipient_canonical_id,
        attributes={"amount_cents": str(amount_cents)},
        external_key=transaction_id,
        provenance=provenance,
    )
