"""Flat fact-context constructors for the four launch rule families.

Pure helpers — no DB calls, no rule evaluation.

Field names match the ``fact:`` keys in the YAML rules under
``src/rules/conflict_of_interest/``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


def days_gap(d1: dt.date, d2: dt.date) -> int:
    return abs((d2 - d1).days)


def overlap_days(
    start1: dt.date,
    end1: dt.date | None,
    start2: dt.date,
    end2: dt.date | None,
    *,
    reference_date: dt.date | None = None,
) -> int:
    """Days two date ranges overlap. Open-ended (None) bounds close at reference_date (today)."""
    today = reference_date or dt.date.today()
    e1 = end1 if end1 is not None else today
    e2 = end2 if end2 is not None else today

    overlap_start = max(start1, start2)
    overlap_end = min(e1, e2)

    delta = (overlap_end - overlap_start).days + 1  # inclusive
    return max(0, delta)


def overdue_days(filed_at: dt.date | None, deadline: dt.date) -> int | None:
    """Days after deadline a filing was submitted. None if filed_at is absent (no imputation)."""
    if filed_at is None:
        return None
    return max(0, (filed_at - deadline).days)


def count_matching_transactions(
    transactions: list[dict[str, Any]],
    committee_sector: str,
    service_start: dt.date,
    service_end: dt.date | None,
    *,
    reference_date: dt.date | None = None,
) -> int:
    """Count transactions matching committee_sector within the service window."""
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
    """Distinct calendar days with matching trades. Uses the same filter as count_matching_transactions."""
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
    return bool(set(committee_sectors) & set(holding_sectors))


def midpoint_value(
    value_min: float | None,
    value_max: float | None,
) -> float | None:
    """Midpoint of a disclosed value range. Falls back to whichever bound is present."""
    if value_min is not None and value_max is not None:
        return (value_min + value_max) / 2.0
    if value_min is not None:
        return float(value_min)
    if value_max is not None:
        return float(value_max)
    return None


def build_committee_sector_trade_context(row: dict[str, Any]) -> dict[str, Any]:
    """Row keys: committee_name, committee_sector, committee_start_date,
    committee_end_date, holding_sector, sector_name,
    disclosure_period_start, disclosure_period_end.
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
    """Row keys: committee_name, committee_sector, committee_start_date,
    committee_end_date, transactions (list of dicts with transaction_date and sector).
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
    """Row keys: filing_id, filed_at, deadline, filing_is_amendment,
    superseded_filing_id, filing_kind ('original' or 'amendment').
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
    """Row keys: committee_name, committee_sector, committee_start_date,
    committee_end_date, holding_sector, sector_name, holding_value_min,
    holding_value_max, disclosure_period_start, disclosure_period_end.
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
