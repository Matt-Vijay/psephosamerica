"""Bridge from canonical joined row-dicts to rule/evidence-ready bundles.

Pure helpers only — no SQL execution, no DB calls, no I/O.

Each ``assemble_*_bundle`` function accepts a pre-joined row dict (all
necessary joins already resolved upstream) and returns a ``ConflictBundle``
containing:

- ``member_bioguide_id`` — canonical member identifier
- ``context`` — flat fact dict ready for ``evaluate_rule``
- ``source_anchors`` — list of SourceAnchor objects linking official records
- ``superseded_filing_id`` — str if an amendment supersedes a prior filing,
  otherwise None

Covers all four launch rule families:
  - committee_sector_trade
  - repeated_committee_linked_trading
  - late_or_amended_disclosure
  - sector_holdings_overlap
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.evidence.builder import build_source_anchor
from src.export.contracts import SourceAnchor
from src.rules.contexts import (
    build_committee_sector_trade_context,
    build_late_or_amended_disclosure_context,
    build_repeated_committee_linked_trading_context,
    build_sector_holdings_overlap_context,
)


@dataclass(frozen=True)
class ConflictBundle:
    """Immutable bundle bridging joined row data to rule engine inputs.

    Attributes:
        member_bioguide_id: Canonical member identifier.
        context: Flat fact dict produced by the matching context builder.
        source_anchors: Official record pointers required by the evidence card.
        superseded_filing_id: Present when the triggering filing is an
            amendment that replaces a prior filing; None otherwise.
    """

    member_bioguide_id: str
    context: dict[str, Any]
    source_anchors: list[SourceAnchor]
    superseded_filing_id: str | None


# ---------------------------------------------------------------------------
# committee_sector_trade
# ---------------------------------------------------------------------------


def assemble_committee_sector_trade_bundle(row: dict[str, Any]) -> ConflictBundle:
    """Assemble a bundle for ``committee_sector_trade.v1``.

    Expected row keys (beyond those consumed by the context builder):
        member_bioguide_id (str)
        financial_disclosure_id (str)  — source_id for the disclosure anchor
        committee_membership_id (str)  — source_id for the membership anchor

    Context builder keys (passed through to
    ``build_committee_sector_trade_context``):
        committee_name, committee_sector, committee_start_date,
        committee_end_date, holding_sector, sector_name,
        disclosure_period_start, disclosure_period_end

    Returns:
        ConflictBundle with two source anchors and no superseded filing.
    """
    context = build_committee_sector_trade_context(row)

    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]

    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )


# ---------------------------------------------------------------------------
# repeated_committee_linked_trading
# ---------------------------------------------------------------------------


def assemble_repeated_committee_linked_trading_bundle(
    row: dict[str, Any],
) -> ConflictBundle:
    """Assemble a bundle for ``repeated_committee_linked_trading.v1``.

    Expected row keys (beyond context builder keys):
        member_bioguide_id (str)
        financial_disclosure_id (str)
        committee_membership_id (str)

    Context builder keys (passed through to
    ``build_repeated_committee_linked_trading_context``):
        committee_name, committee_sector, committee_start_date,
        committee_end_date, transactions

    Returns:
        ConflictBundle with two source anchors and no superseded filing.
    """
    context = build_repeated_committee_linked_trading_context(row)

    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]

    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )


# ---------------------------------------------------------------------------
# late_or_amended_disclosure
# ---------------------------------------------------------------------------


def assemble_late_or_amended_disclosure_bundle(
    row: dict[str, Any],
) -> ConflictBundle:
    """Assemble a bundle for ``late_or_amended_disclosure.v1``.

    Expected row keys (beyond context builder keys):
        member_bioguide_id (str)
        financial_disclosure_id (str)

    Context builder keys (passed through to
    ``build_late_or_amended_disclosure_context``):
        filing_id, filed_at, deadline, filing_is_amendment,
        superseded_filing_id, filing_kind

    When ``filing_is_amendment`` is truthy, ``superseded_filing_id`` from
    the row is forwarded onto the bundle so the rule engine can link the
    new fire to the replaced filing.

    Returns:
        ConflictBundle with one source anchor; superseded_filing_id set for
        amendments, None for originals.
    """
    context = build_late_or_amended_disclosure_context(row)

    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label=f"Financial disclosure ({row.get('filing_kind', 'filing')})",
        ),
    ]

    superseded: str | None = None
    if row.get("filing_is_amendment") and row.get("superseded_filing_id") is not None:
        superseded = str(row["superseded_filing_id"])

    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=superseded,
    )


# ---------------------------------------------------------------------------
# sector_holdings_overlap
# ---------------------------------------------------------------------------


def assemble_sector_holdings_overlap_bundle(row: dict[str, Any]) -> ConflictBundle:
    """Assemble a bundle for ``sector_holdings_overlap.v1``.

    Expected row keys (beyond context builder keys):
        member_bioguide_id (str)
        financial_disclosure_id (str)
        committee_membership_id (str)

    Context builder keys (passed through to
    ``build_sector_holdings_overlap_context``):
        committee_name, committee_sector, committee_start_date,
        committee_end_date, holding_sector, sector_name,
        holding_value_min, holding_value_max,
        disclosure_period_start, disclosure_period_end

    Returns:
        ConflictBundle with two source anchors and no superseded filing.
    """
    context = build_sector_holdings_overlap_context(row)

    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]

    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )
