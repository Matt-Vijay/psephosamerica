"""DB-backed full recompute run for the conflict-of-interest score family.

Six-step orchestration:
  1. Fetch member roster and previous score snapshots
  2. Fetch canonical conflict inputs
  3. Assemble per-family input rows with sector enrichment
  4. Recompute conflicts → rule fires + evidence cards
  5. Build FK-safe load plan
  6. Persist rule fires, evidence cards, and score snapshots
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from src.db.load_executor import Resolvers, execute_load_plan
from src.db.load_report import LoadSummary
from src.db.runtime_lookups import load_lookup_bundle
from src.export.contracts import EvidenceCardPayload
from src.load.recompute import recompute_load_plan
from src.normalize.taxonomy_runtime import TaxonomyRuntime
from src.pipeline.conflict_recompute import recompute_conflicts
from src.query.conflict_inputs import (
    IssuerSectorResolver,
    assemble_committee_sector_trade_rows,
    assemble_repeated_committee_linked_trading_rows,
    assemble_sector_holdings_overlap_rows,
    fetch_committee_membership_rows,
    fetch_holding_rows,
    fetch_late_or_amended_rows,
    fetch_transaction_rows,
)
from src.query.recompute_rows import (
    fetch_previous_score_snapshot_rows,
    fetch_recompute_members,
    index_latest_previous_snapshots,
)
from src.rules.models import RuleFire


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RecomputeRunResult:
    rule_fires: list[RuleFire] = field(default_factory=list)
    evidence_cards: list[EvidenceCardPayload] = field(default_factory=list)
    load_summary: LoadSummary | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_committee_sector_resolver(
    membership_rows: list[dict[str, Any]],
    taxonomy: TaxonomyRuntime,
) -> Callable[[str, int], str | None]:
    """Build a (committee_code, congress) → sector_id resolver from pre-fetched rows.

    Pure: no DB access.  committee_name is already present on each row from
    the JOIN in fetch_committee_membership_rows.
    """
    sector_map: dict[tuple[str, int], str | None] = {}
    for row in membership_rows:
        key = (row["committee_code"], row["congress"])
        if key not in sector_map:
            mapping = taxonomy.committee_sector(row["committee_name"], congress=row["congress"])
            sector_map[key] = mapping.sector_id if mapping else None

    def resolve(committee_code: str, congress: int) -> str | None:
        return sector_map.get((committee_code, congress))

    return resolve


def _build_recompute_resolvers(bioguide_map: dict[str, int]) -> Resolvers:
    """Build FK resolvers for the recompute write phase.

    Only _bioguide_id needs resolution; rule_fire_id and
    superseded_financial_disclosure_id are nullable and resolved to None
    when absent — the load_executor strips unknown hint keys silently.
    """
    return {
        "_bioguide_id": (
            "member_id",
            lambda row: bioguide_map.get(row.get("_bioguide_id", "")),
        ),
    }


def _delta_rows_by_member_id(
    cards: list[EvidenceCardPayload],
    bioguide_to_id: dict[str, int],
) -> dict[int, list[dict[str, Any]]]:
    """Group evidence card score deltas by integer member_id for snapshot building."""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for card in cards:
        member_id = bioguide_to_id.get(card.member_bioguide_id)
        if member_id is not None:
            grouped.setdefault(member_id, []).append(
                {"dimension": card.dimension, "score_delta": card.score_delta}
            )
    return grouped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_recompute(
    conn: Any,
    *,
    recompute_run_id: int,
    snapshot_date: dt.date,
    taxonomy: TaxonomyRuntime,
    issuer_sector_resolver: IssuerSectorResolver,
) -> RecomputeRunResult:
    """Execute a full conflict-of-interest recompute against the live database.

    Args:
        conn:                   Open psycopg connection.
        recompute_run_id:       ingestion_run.id for this pass; embedded in
                                every rule_fire and score_snapshot row.
        snapshot_date:          Date used as the score_snapshot key and the
                                filing_year boundary for disclosure queries.
        taxonomy:               Pre-loaded TaxonomyRuntime; used to resolve
                                committee codes to sector IDs.
        issuer_sector_resolver: Callable(issuer_name, issuer_ticker) → sector
                                slug or None; injected so callers control DB vs
                                cached vs normalised resolution strategy.
    """
    # ------------------------------------------------------------------ 1. --
    # Fetch member roster and previous score snapshots.
    members = fetch_recompute_members(conn)
    if not members:
        return RecomputeRunResult()

    member_ids = [m["id"] for m in members]
    bioguide_ids = [m["bioguide_id"] for m in members]
    members_by_bioguide = {m["bioguide_id"]: m for m in members}
    bioguide_to_id = {m["bioguide_id"]: m["id"] for m in members}

    snapshot_rows = fetch_previous_score_snapshot_rows(conn, member_ids=member_ids)
    _previous = index_latest_previous_snapshots(snapshot_rows)  # baseline; available for callers

    filing_year = snapshot_date.year

    # ------------------------------------------------------------------ 2. --
    # Fetch canonical conflict inputs.
    late_rows = fetch_late_or_amended_rows(
        conn, bioguide_ids=bioguide_ids, filing_year=filing_year
    )
    membership_rows = fetch_committee_membership_rows(conn, bioguide_ids=bioguide_ids)
    holding_rows = fetch_holding_rows(
        conn, bioguide_ids=bioguide_ids, filing_year=filing_year
    )
    transaction_rows = fetch_transaction_rows(
        conn, bioguide_ids=bioguide_ids, filing_year=filing_year
    )

    # ------------------------------------------------------------------ 3. --
    # Assemble per-family input rows with sector enrichment.
    committee_sector_resolver = _build_committee_sector_resolver(membership_rows, taxonomy)

    rows_by_family: dict[str, list[dict[str, Any]]] = {
        "late_or_amended_disclosure": late_rows,
        "committee_sector_trade": assemble_committee_sector_trade_rows(
            membership_rows,
            holding_rows,
            committee_sector_resolver=committee_sector_resolver,
            issuer_sector_resolver=issuer_sector_resolver,
        ),
        "repeated_committee_linked_trading": assemble_repeated_committee_linked_trading_rows(
            membership_rows,
            transaction_rows,
            committee_sector_resolver=committee_sector_resolver,
            issuer_sector_resolver=issuer_sector_resolver,
        ),
        "sector_holdings_overlap": assemble_sector_holdings_overlap_rows(
            membership_rows,
            holding_rows,
            committee_sector_resolver=committee_sector_resolver,
            issuer_sector_resolver=issuer_sector_resolver,
        ),
    }

    # ------------------------------------------------------------------ 4. --
    # Recompute conflicts → rule fires + evidence cards.
    conflict_result = recompute_conflicts(
        rows_by_family=rows_by_family,
        members_by_bioguide=members_by_bioguide,
        recompute_run_id=str(recompute_run_id),
        snapshot_date=snapshot_date,
    )

    # ------------------------------------------------------------------ 5. --
    # Build FK-safe load plan (rule_fire → evidence_card → score_snapshot).
    deltas = _delta_rows_by_member_id(conflict_result.evidence_cards, bioguide_to_id)
    operations = recompute_load_plan(
        fires=conflict_result.rule_fires,
        cards=conflict_result.evidence_cards,
        members=members,
        delta_rows_by_member_id=deltas,
        snapshot_at=snapshot_date,
        recompute_run_id=recompute_run_id,
    )

    # ------------------------------------------------------------------ 6. --
    # Persist recompute outputs.
    lookup_bundle = load_lookup_bundle(conn)
    resolvers = _build_recompute_resolvers(lookup_bundle.bioguide_map)
    load_summary = execute_load_plan(
        conn,
        operations,
        resolvers=resolvers,
        run_id=recompute_run_id,
    )

    return RecomputeRunResult(
        rule_fires=conflict_result.rule_fires,
        evidence_cards=conflict_result.evidence_cards,
        load_summary=load_summary,
    )
