"""DB-backed input row assembly for the four conflict-of-interest rule families.

``fetch_*`` helpers issue real SQL via repositories.fetch_all.
``assemble_*`` helpers accept pre-fetched rows and inject sector-resolution
callables so they never reach the DB themselves.

Invariant: no live issuer-to-sector resolution happens here.  Callers must
supply sector mappings or resolver callables for the three sector-dependent
families (committee_sector_trade, repeated_committee_linked_trading,
sector_holdings_overlap).
"""

from __future__ import annotations

from typing import Any, Callable

from src.db.repositories import fetch_all


# ---------------------------------------------------------------------------
# late_or_amended_disclosure — fully DB-backed
# ---------------------------------------------------------------------------

_LATE_OR_AMENDED_SQL = """
SELECT
    fd.id                           AS financial_disclosure_id,
    m.bioguide_id                   AS member_bioguide_id,
    fd.filing_type                  AS filing_kind,
    fd.filed_at                     AS filed_at,
    fd.filing_period_end            AS deadline,
    (fd.filing_type = 'amendment')  AS filing_is_amendment,
    fd.supersedes_financial_disclosure_id AS superseded_filing_id
FROM financial_disclosure fd
JOIN member m ON m.id = fd.member_id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
  AND fd.filing_year = %(filing_year)s
ORDER BY m.bioguide_id, fd.id
"""


def fetch_late_or_amended_rows(
    conn,
    *,
    bioguide_ids: list[str],
    filing_year: int,
) -> list[dict[str, Any]]:
    """Return one row per filing for each member, ready for
    assemble_late_or_amended_disclosure_bundle.
    """
    return fetch_all(conn, _LATE_OR_AMENDED_SQL, {"bioguide_ids": bioguide_ids, "filing_year": filing_year})


# ---------------------------------------------------------------------------
# committee_membership — base rows (sector enrichment injected by caller)
# ---------------------------------------------------------------------------

_COMMITTEE_MEMBERSHIP_SQL = """
SELECT
    cm.id                   AS committee_membership_id,
    m.bioguide_id           AS member_bioguide_id,
    c.id                    AS committee_id,
    c.committee_code        AS committee_code,
    c.name                  AS committee_name,
    c.congress              AS congress,
    cm.role                 AS role,
    cm.start_date           AS committee_start_date,
    cm.end_date             AS committee_end_date,
    cm.is_current           AS is_current
FROM committee_membership cm
JOIN member m ON m.id = cm.member_id
JOIN committee c ON c.id = cm.committee_id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
  AND c.review_tier != 'out_of_scope'
ORDER BY m.bioguide_id, cm.id
"""


def fetch_committee_membership_rows(
    conn,
    *,
    bioguide_ids: list[str],
) -> list[dict[str, Any]]:
    """Return canonical committee membership rows.  Callers enrich each row
    with a ``committee_sector`` key before passing to an assemble_* helper.
    """
    return fetch_all(conn, _COMMITTEE_MEMBERSHIP_SQL, {"bioguide_ids": bioguide_ids})


# ---------------------------------------------------------------------------
# holdings — base rows
# ---------------------------------------------------------------------------

_HOLDINGS_SQL = """
SELECT
    h.id                            AS holding_id,
    fd.id                           AS financial_disclosure_id,
    m.bioguide_id                   AS member_bioguide_id,
    fd.filing_year                  AS filing_year,
    fd.filing_period_start          AS disclosure_period_start,
    fd.filing_period_end            AS disclosure_period_end,
    h.issuer_name                   AS issuer_name,
    h.issuer_ticker                 AS issuer_ticker,
    h.asset_category                AS asset_category,
    h.owner_type                    AS owner_type,
    h.value_min                     AS holding_value_min,
    h.value_max                     AS holding_value_max,
    h.value_label                   AS value_label
FROM holding h
JOIN financial_disclosure fd ON fd.id = h.financial_disclosure_id
JOIN member m ON m.id = fd.member_id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
  AND fd.filing_year = %(filing_year)s
ORDER BY m.bioguide_id, fd.id, h.line_number
"""


def fetch_holding_rows(
    conn,
    *,
    bioguide_ids: list[str],
    filing_year: int,
) -> list[dict[str, Any]]:
    """Return canonical holding rows.  Callers enrich each row with a
    ``holding_sector`` key before passing to an assemble_* helper.
    """
    return fetch_all(conn, _HOLDINGS_SQL, {"bioguide_ids": bioguide_ids, "filing_year": filing_year})


# ---------------------------------------------------------------------------
# transactions — base rows
# ---------------------------------------------------------------------------

_TRANSACTIONS_SQL = """
SELECT
    t.id                            AS transaction_id,
    fd.id                           AS financial_disclosure_id,
    m.bioguide_id                   AS member_bioguide_id,
    fd.filing_year                  AS filing_year,
    fd.filing_period_start          AS disclosure_period_start,
    fd.filing_period_end            AS disclosure_period_end,
    t.issuer_name                   AS issuer_name,
    t.issuer_ticker                 AS issuer_ticker,
    t.transaction_type              AS transaction_type,
    t.transaction_date              AS transaction_date,
    t.owner_type                    AS owner_type,
    t.amount_min                    AS amount_min,
    t.amount_max                    AS amount_max,
    t.amount_label                  AS amount_label
FROM "transaction" t
JOIN financial_disclosure fd ON fd.id = t.financial_disclosure_id
JOIN member m ON m.id = fd.member_id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
  AND fd.filing_year = %(filing_year)s
ORDER BY m.bioguide_id, fd.id, t.line_number
"""


def fetch_transaction_rows(
    conn,
    *,
    bioguide_ids: list[str],
    filing_year: int,
) -> list[dict[str, Any]]:
    """Return canonical transaction rows.  Callers enrich each row with a
    ``sector`` key before passing to an assemble_* helper.
    """
    return fetch_all(conn, _TRANSACTIONS_SQL, {"bioguide_ids": bioguide_ids, "filing_year": filing_year})


# ---------------------------------------------------------------------------
# assemble_* helpers — sector-dependent families
# ---------------------------------------------------------------------------

IssuerSectorResolver = Callable[[str, str | None], str | None]
"""callable(issuer_name, issuer_ticker) -> sector slug or None"""

CommitteeSectorResolver = Callable[[str, int], str | None]
"""callable(committee_code, congress) -> sector slug or None"""


def assemble_committee_sector_trade_rows(
    membership_rows: list[dict[str, Any]],
    holding_rows: list[dict[str, Any]],
    *,
    committee_sector_resolver: CommitteeSectorResolver,
    issuer_sector_resolver: IssuerSectorResolver,
) -> list[dict[str, Any]]:
    """Cross-join committee memberships with holdings when sectors match.

    Returns one output row per (membership, holding) pair whose resolved
    sectors are identical and non-None.  Each row is ready for
    conflict.assemble_committee_sector_trade_bundle.
    """
    enriched_memberships = [
        {**r, "committee_sector": committee_sector_resolver(r["committee_code"], r["congress"])}
        for r in membership_rows
    ]
    enriched_holdings = [
        {**r, "holding_sector": issuer_sector_resolver(r["issuer_name"], r.get("issuer_ticker"))}
        for r in holding_rows
    ]

    out: list[dict[str, Any]] = []
    for m in enriched_memberships:
        if m["committee_sector"] is None:
            continue
        for h in enriched_holdings:
            if h["member_bioguide_id"] != m["member_bioguide_id"]:
                continue
            if h["holding_sector"] != m["committee_sector"]:
                continue
            out.append({
                **h,
                "committee_membership_id": m["committee_membership_id"],
                "committee_name": m["committee_name"],
                "committee_sector": m["committee_sector"],
                "committee_start_date": m["committee_start_date"],
                "committee_end_date": m["committee_end_date"],
                "sector_name": m["committee_sector"],
            })
    return out


def assemble_repeated_committee_linked_trading_rows(
    membership_rows: list[dict[str, Any]],
    transaction_rows: list[dict[str, Any]],
    *,
    committee_sector_resolver: CommitteeSectorResolver,
    issuer_sector_resolver: IssuerSectorResolver,
) -> list[dict[str, Any]]:
    """One output row per committee membership, carrying all sector-matching
    transactions as a nested list.  Ready for
    conflict.assemble_repeated_committee_linked_trading_bundle.
    """
    enriched_memberships = [
        {**r, "committee_sector": committee_sector_resolver(r["committee_code"], r["congress"])}
        for r in membership_rows
    ]
    enriched_transactions = [
        {**r, "sector": issuer_sector_resolver(r["issuer_name"], r.get("issuer_ticker"))}
        for r in transaction_rows
    ]

    out: list[dict[str, Any]] = []
    for m in enriched_memberships:
        if m["committee_sector"] is None:
            continue
        member_txns = [
            {"transaction_date": t["transaction_date"], "sector": t["sector"]}
            for t in enriched_transactions
            if t["member_bioguide_id"] == m["member_bioguide_id"]
            and t["sector"] is not None
        ]
        out.append({
            "member_bioguide_id": m["member_bioguide_id"],
            "financial_disclosure_id": None,
            "committee_membership_id": m["committee_membership_id"],
            "committee_name": m["committee_name"],
            "committee_sector": m["committee_sector"],
            "committee_start_date": m["committee_start_date"],
            "committee_end_date": m["committee_end_date"],
            "transactions": member_txns,
        })
    return out


def assemble_sector_holdings_overlap_rows(
    membership_rows: list[dict[str, Any]],
    holding_rows: list[dict[str, Any]],
    *,
    committee_sector_resolver: CommitteeSectorResolver,
    issuer_sector_resolver: IssuerSectorResolver,
) -> list[dict[str, Any]]:
    """One output row per (membership, holding) pair with matching sectors.
    Ready for conflict.assemble_sector_holdings_overlap_bundle.
    """
    enriched_memberships = [
        {**r, "committee_sector": committee_sector_resolver(r["committee_code"], r["congress"])}
        for r in membership_rows
    ]
    enriched_holdings = [
        {**r, "holding_sector": issuer_sector_resolver(r["issuer_name"], r.get("issuer_ticker"))}
        for r in holding_rows
    ]

    out: list[dict[str, Any]] = []
    for m in enriched_memberships:
        if m["committee_sector"] is None:
            continue
        for h in enriched_holdings:
            if h["member_bioguide_id"] != m["member_bioguide_id"]:
                continue
            if h["holding_sector"] != m["committee_sector"]:
                continue
            out.append({
                **h,
                "committee_membership_id": m["committee_membership_id"],
                "committee_name": m["committee_name"],
                "committee_sector": m["committee_sector"],
                "committee_start_date": m["committee_start_date"],
                "committee_end_date": m["committee_end_date"],
                "sector_name": m["committee_sector"],
                "holding_value_min": h.get("holding_value_min"),
                "holding_value_max": h.get("holding_value_max"),
            })
    return out
