"""Adapt declared positions into official -> topic stated-position edges.

Stated positions — candidate questionnaires (Vote Smart, LWV), campaign-site
issue pages (time-resolved via the Wayback Machine), op-eds, debate answers — are
the most data-efficient signal for an official with a thin voting record: a
first-term councilor has no votes, but their declared positions are labeled
training data of stance. As graph edges they connect the official to a topic
(an issue node or a bill) with a ``stance`` (support / oppose / neutral) and the
``statement_type`` it came from.

A statement is public when published, so ``known_at`` is the statement date.
For Wayback-captured campaign sites the source URL is the archived snapshot and
the statement date is the capture date.
"""

from __future__ import annotations

import re

from src.graph.edges import GraphEdge
from src.graph.provenance import (
    ProvenanceEnvelope,
    statement_provenance as stated_position_provenance,
)

__all__ = ["stated_position_provenance", "stated_position_edge"]

_STANCE_ALIASES: dict[str, str] = {
    "support": "support",
    "favor": "support",
    "for": "support",
    "yes": "support",
    "oppose": "oppose",
    "against": "oppose",
    "no": "oppose",
    "neutral": "neutral",
    "undecided": "neutral",
    "mixed": "neutral",
}


def _normalize_stance(raw: str) -> str:
    stance = _STANCE_ALIASES.get(raw.strip().lower())
    if stance is None:
        raise ValueError(f"unknown stated-position stance: {raw!r}")
    return stance


def stated_position_edge(
    *,
    official_canonical_id: str,
    topic_id: str,
    stance: str,
    statement_type: str,
    provenance: ProvenanceEnvelope,
    statement_id: str | None = None,
) -> GraphEdge:
    """Build the stated-position edge from an official to a topic."""
    if not statement_type.strip():
        raise ValueError("statement_type must be non-blank")
    normalized_type = re.sub(r"[\s-]+", "_", statement_type.strip().lower())
    return GraphEdge(
        edge_type="stated_position",
        src_id=official_canonical_id,
        dst_id=topic_id,
        attributes={"stance": _normalize_stance(stance), "statement_type": normalized_type},
        external_key=statement_id,
        provenance=provenance,
    )
