"""Adapt financial disclosures into member -> issuer financial-interest edges.

Financial disclosures are this repo's founding domain (conflict-of-interest
scoring). As graph edges they connect a member to an issuer (a company,
fund, or other org) with the interest type (holding / purchase / sale /
exchange) and the disclosed amount range.

Their leakage semantics match donations: a holding or trade is *not public when
it happens* — only when the periodic disclosure is filed, often many months
later. :func:`disclosure_provenance` sets ``valid_from`` at the holding/trade
date and ``known_at`` at the filing date, so a prediction made before the
disclosure cannot leak the interest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_INTEREST_ALIASES: dict[str, str] = {
    "holding": "holding",
    "asset": "holding",
    "ownership": "holding",
    "purchase": "purchase",
    "buy": "purchase",
    "bought": "purchase",
    "sale": "sale",
    "sell": "sale",
    "sold": "sale",
    "exchange": "exchange",
}


def _normalize_interest_type(raw: str) -> str:
    interest = _INTEREST_ALIASES.get(raw.strip().lower())
    if interest is None:
        raise ValueError(f"unknown financial interest type: {raw!r}")
    return interest


def disclosure_provenance(
    *,
    source_url: str,
    content_sha256: str,
    as_of_date: date,
    filed_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a disclosed interest; ``known_at`` defaults to the filing date."""
    disclosed = datetime(filed_date.year, filed_date.month, filed_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=as_of_date,
        known_at=known_at if known_at is not None else disclosed,
    )


def financial_interest_edge(
    *,
    member_canonical_id: str,
    issuer_canonical_id: str,
    interest_type: str,
    amount_range: str,
    provenance: ProvenanceEnvelope,
    transaction_id: str | None = None,
) -> GraphEdge:
    """Build the financial-interest edge from a member to an issuer."""
    if not amount_range.strip():
        raise ValueError("amount_range must be non-blank")
    return GraphEdge(
        edge_type="financial_interest",
        src_id=member_canonical_id,
        dst_id=issuer_canonical_id,
        attributes={
            "interest_type": _normalize_interest_type(interest_type),
            "amount_range": amount_range.strip(),
        },
        external_key=transaction_id,
        provenance=provenance,
    )
