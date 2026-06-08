"""Adapt endorsements into endorser -> endorsee edges.

Endorsement-network signal is, per the blueprint, the most data-efficient signal
for officials with a thin voting record: who endorses whom places an unknown
candidate in ideological space before they have cast a single vote. An
endorsement is an ``endorsement`` edge from the endorser (an org like the Sierra
Club, or another official) to the endorsee, carrying a ``stance`` (endorse /
oppose — an anti-endorsement is just as informative) and a window that closes
if the endorsement is rescinded. ``known_at`` defaults to the announcement day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_STANCE_ALIASES: dict[str, str] = {
    "endorse": "endorse",
    "endorsement": "endorse",
    "support": "endorse",
    "for": "endorse",
    "oppose": "oppose",
    "opposition": "oppose",
    "anti": "oppose",
    "against": "oppose",
}


def _normalize_stance(raw: str) -> str:
    stance = _STANCE_ALIASES.get(raw.strip().lower())
    if stance is None:
        raise ValueError(f"unknown endorsement stance: {raw!r} (expected 'endorse' or 'oppose')")
    return stance


def endorsement_provenance(
    *,
    source_url: str,
    content_sha256: str,
    announced_date: date,
    first_observed_at: datetime,
    rescinded_date: date | None = None,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for an endorsement; ``valid_to`` is the rescission date."""
    default_known = datetime(
        announced_date.year, announced_date.month, announced_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=announced_date,
        valid_to=rescinded_date,
        known_at=known_at if known_at is not None else default_known,
    )


def endorsement_edge(
    *,
    endorser_canonical_id: str,
    endorsee_canonical_id: str,
    provenance: ProvenanceEnvelope,
    stance: str = "endorse",
) -> GraphEdge:
    """Build the endorsement edge from an endorser to an endorsee."""
    return GraphEdge(
        edge_type="endorsement",
        src_id=endorser_canonical_id,
        dst_id=endorsee_canonical_id,
        attributes={"stance": _normalize_stance(stance)},
        provenance=provenance,
    )
