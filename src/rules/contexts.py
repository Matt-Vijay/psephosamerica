"""Flat fact-context constructors for the four launch rule families.

Pure helpers only — no DB calls, no rule evaluation.

Each public ``build_*`` function accepts a pre-joined row dict (upstream
joins already resolved) and returns a flat ``dict[str, Any]`` suitable for
passing directly to ``evaluate_rule(rule, facts, ...)``.

Field names are stable and match the ``fact:`` keys in the corresponding
YAML rule definitions under ``src/rules/conflict_of_interest/``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


# ---------------------------------------------------------------------------
# Low-level derived-value helpers (exported for reuse / direct testing)
# ---------------------------------------------------------------------------

def days_gap(d1: dt.date, d2: dt.date) -> int:
    """Return the absolute number of days between two dates.

    Args:
        d1: First date.
        d2: Second date.

    Returns:
        Non-negative integer day count.
    """
    return abs((d2 - d1).days)


def overlap_days(
    start1: dt.date,
    end1: dt.date | None,
    start2: dt.date,
    end2: dt.date | None,
    *,
    reference_date: dt.date | None = None,
) -> int:
    """Compute the number of days two date ranges overlap.

    Open-ended ranges (``None`` end) are closed at *reference_date*, which
    defaults to today if not supplied.

    Args:
        start1: Start of the first range (inclusive).
        end1: End of the first range (inclusive), or None if open.
        start2: Start of the second range (inclusive).
        end2: End of the second range (inclusive), or None if open.
        reference_date: Upper bound for open-ended ranges.

    Returns:
        Number of overlapping days (0 if ranges do not overlap).
    """
    today = reference_date or dt.date.today()
    e1 = end1 if end1 is not None else today
    e2 = end2 if end2 is not None else today

    overlap_start = max(start1, start2)
    overlap_end = min(e1, e2)

    delta = (overlap_end - overlap_start).days + 1  # inclusive
    return max(0, delta)


def overdue_days(filed_at: dt.date | None, deadline: dt.date) -> int | None:
    """Number of calendar days a filing was submitted after its deadline.

    Args:
        filed_at: Actual filing date, or None if not yet filed.
        deadline: Official due date.

    Returns:
        Positive integer if late, 0 if on time, None if ``filed_at`` is
        absent (no imputation — caller must handle None per no_fire policy).
    """
    if filed_at is None:
        return None
    delta = (filed_at - deadline).days
    return max(0, delta)


def count_matching_transactions(
    transactions: list[dict[str, Any]],
    committee_sector: str,
    service_start: dt.date,
    service_end: dt.date | None,
    *,
    reference_date: dt.date | None = None,
) -> int:
    """Count disclosed transactions whose sector matches the committee sector
    and whose transaction date falls within the committee service window.

    Args:
        transactions: List of transaction dicts, each with at minimum
            ``transaction_date`` (date) and ``sector`` (str).
        committee_sector: Canonical sector string from the committee map.
        service_start: First day of committee service.
        service_end: Last day of committee service (None = still serving).
        reference_date: Upper bound for open-ended service, defaults to today.

    Returns:
        Non-negative integer count.
    """
    today = reference_date or dt.date.today()
    end = service_end if service_end is not None else today

    count = 0
    for txn in transactions:
        if txn.get("sector") != committee_sector:
            continue
        txn_date = txn.get("transaction_date")
        if txn_date is None:
            continue
        if service_start <= txn_date <= end:
            count += 1
    return count


def count_distinct_trade_days(
    transactions: list[dict[str, Any]],
    committee_sector: str,
    service_start: dt.date,
    service_end: dt.date | None,
    *,
    reference_date: dt.date | None = None,
) -> int:
    """Count distinct calendar days on which matching trades occurred.

    Uses the same sector-and-date filter as ``count_matching_transactions``.

    Args:
        transactions: List of transaction dicts.
        committee_sector: Canonical sector string.
        service_start: First day of committee service.
        service_end: Last day of committee service (None = still serving).
        reference_date: Upper bound for open-ended service.

    Returns:
        Number of distinct trade days (0 or more).
    """
    today = reference_date or dt.date.today()
    end = service_end if service_end is not None else today

    trade_days: set[dt.date] = set()
    for txn in transactions:
        if txn.get("sector") != committee_sector:
            continue
        txn_date = txn.get("transaction_date")
        if txn_date is None:
            continue
        if service_start <= txn_date <= end:
            trade_days.add(txn_date)
    return len(trade_days)


def sectors_overlap(
    committee_sectors: set[str] | list[str],
    holding_sectors: set[str] | list[str],
) -> bool:
    """Return True if any committee sector appears in the holding sectors.

    Args:
        committee_sectors: Sectors linked to the member's committee(s).
        holding_sectors: Sectors of the member's disclosed holdings.

    Returns:
        True if the intersection is non-empty.
    """
    return bool(set(committee_sectors) & set(holding_sectors))


def midpoint_value(
    value_min: float | None,
    value_max: float | None,
) -> float | None:
    """Return the midpoint of a disclosure value range.

    Falls back to whichever bound is available, or None if both are absent.

    Args:
        value_min: Lower bound of the disclosed value range.
        value_max: Upper bound of the disclosed value range.

    Returns:
        Midpoint float, or the single available bound, or None.
    """
    if value_min is not None and value_max is not None:
        return (value_min + value_max) / 2.0
    if value_min is not None:
        return float(value_min)
    if value_max is not None:
        return float(value_max)
    return None


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def build_committee_sector_trade_context(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a pre-joined row into a fact context for
    ``conflict_of_interest_risk.committee_sector_trade.v1``.

    Expected row keys (upstream joins already resolved):
        committee_name (str)
        committee_sector (str | None)
        committee_start_date (date)
        committee_end_date (date | None)
        holding_sector (str | None)
        sector_name (str)
        disclosure_period_start (date)
        disclosure_period_end (date | None)

    Derived facts produced:
        committee_service_overlap_days (int)  — days of overlap between
            committee service and the disclosure period
        holding_overlap_days (int)  — same interval from the holding side

    Returns:
        Flat dict[str, Any] ready for ``evaluate_rule``.
    """
    committee_start: dt.date = row["committee_start_date"]
    committee_end: dt.date | None = row.get("committee_end_date")
    disc_start: dt.date = row["disclosure_period_start"]
    disc_end: dt.date | None = row.get("disclosure_period_end")

    shared_overlap = overlap_days(committee_start, committee_end, disc_start, disc_end)

    return {
        "committee_name": row.get("committee_name"),
        "committee_sector": row.get("committee_sector"),
        "holding_sector": row.get("holding_sector"),
        "sector_name": row.get("sector_name"),
        "committee_service_overlap_days": shared_overlap,
        "holding_overlap_days": shared_overlap,
        "overlap_start": max(committee_start, disc_start),
        "overlap_end": min(
            committee_end if committee_end is not None else dt.date.today(),
            disc_end if disc_end is not None else dt.date.today(),
        ),
    }


def build_repeated_committee_linked_trading_context(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Shape a pre-joined row into a fact context for
    ``conflict_of_interest_risk.repeated_committee_linked_trading.v1``.

    Expected row keys:
        committee_name (str)
        committee_sector (str | None)
        committee_start_date (date)
        committee_end_date (date | None)
        transactions (list[dict]):
            Each dict must have:
                transaction_date (date)
                sector (str)

    Derived facts produced:
        matching_transaction_count (int)
        distinct_trade_days (int)
        service_overlap_days (int)  — total days the member has served on
            the committee (from start to end or today)

    Returns:
        Flat dict[str, Any] ready for ``evaluate_rule``.
    """
    committee_start: dt.date = row["committee_start_date"]
    committee_end: dt.date | None = row.get("committee_end_date")
    committee_sector: str | None = row.get("committee_sector")
    transactions: list[dict[str, Any]] = row.get("transactions", [])

    today = dt.date.today()
    service_end = committee_end if committee_end is not None else today
    total_service_days = days_gap(committee_start, service_end) + 1  # inclusive

    if committee_sector is not None:
        matching = count_matching_transactions(
            transactions, committee_sector, committee_start, committee_end
        )
        distinct_days = count_distinct_trade_days(
            transactions, committee_sector, committee_start, committee_end
        )
    else:
        matching = 0
        distinct_days = 0

    return {
        "committee_name": row.get("committee_name"),
        "committee_sector": committee_sector,
        "matching_transaction_count": matching,
        "distinct_trade_days": distinct_days,
        "service_overlap_days": total_service_days,
    }


def build_late_or_amended_disclosure_context(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a pre-joined row into a fact context for
    ``conflict_of_interest_risk.late_or_amended_disclosure.v1``.

    Expected row keys:
        filing_id (str)
        filed_at (date | None)
        deadline (date)
        filing_is_amendment (bool)
        superseded_filing_id (str | None)
        filing_kind (str)  — 'original' or 'amendment'

    Derived facts produced:
        days_late (int | None)
        amendment_clause (str)  — descriptive clause for the explanation
            template; empty string for original filings

    Returns:
        Flat dict[str, Any] ready for ``evaluate_rule``.
    """
    filed_at: dt.date | None = row.get("filed_at")
    deadline: dt.date = row["deadline"]
    is_amendment: bool = bool(row.get("filing_is_amendment", False))
    superseded_id: str | None = row.get("superseded_filing_id")

    late_days = overdue_days(filed_at, deadline)

    if is_amendment and superseded_id:
        amendment_clause = f" (amends filing {superseded_id})"
    elif is_amendment:
        amendment_clause = " (amendment)"
    else:
        amendment_clause = ""

    return {
        "filing_id": row.get("filing_id"),
        "filed_at": filed_at,
        "deadline": deadline,
        "days_late": late_days,
        "filing_kind": row.get("filing_kind"),
        "filing_is_amendment": is_amendment,
        "superseded_filing_id": superseded_id,
        "amendment_clause": amendment_clause,
    }


def build_sector_holdings_overlap_context(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a pre-joined row into a fact context for
    ``conflict_of_interest_risk.sector_holdings_overlap.v1``.

    Expected row keys:
        committee_name (str)
        committee_sector (str | None)
        committee_start_date (date)
        committee_end_date (date | None)
        holding_sector (str | None)
        sector_name (str)
        holding_value_min (float | None)
        holding_value_max (float | None)
        disclosure_period_start (date)
        disclosure_period_end (date | None)

    Derived facts produced:
        holding_value_usd (float | None)  — midpoint of the disclosed range
        overlap_days (int)  — days the holding was disclosed while the
            member served on the committee

    Returns:
        Flat dict[str, Any] ready for ``evaluate_rule``.
    """
    committee_start: dt.date = row["committee_start_date"]
    committee_end: dt.date | None = row.get("committee_end_date")
    disc_start: dt.date = row["disclosure_period_start"]
    disc_end: dt.date | None = row.get("disclosure_period_end")

    holding_value = midpoint_value(
        row.get("holding_value_min"),
        row.get("holding_value_max"),
    )

    shared_overlap = overlap_days(committee_start, committee_end, disc_start, disc_end)

    return {
        "committee_name": row.get("committee_name"),
        "committee_sector": row.get("committee_sector"),
        "holding_sector": row.get("holding_sector"),
        "sector_name": row.get("sector_name"),
        "holding_value_usd": holding_value,
        "overlap_days": shared_overlap,
    }
