"""FEC canonical load-plan layer.

Pure helpers that produce ordered table-batch operation plans for:
  - fec_committee
  - contribution

Also provides a helper for unresolved candidate-committee linkage hints;
member resolution (bioguide_id lookup) is NOT performed here.

Each operation dict has:
  - table: str              canonical table name (or sentinel for hints)
  - rows: list[dict]        transform output rows ready for the write layer
  - conflict_columns: list[str]  columns that define ON CONFLICT identity
  - mode: str               "upsert" | "hints"

No DB writes are performed here.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.ingest.fec.models import (
    CandidateCommitteeLinkage,
    CommitteeRecord,
    ContributionRecord,
)
from src.ingest.fec.transform import (
    committee_to_row,
    contribution_to_row,
    linkage_to_row,
)

# ---------------------------------------------------------------------------
# Table name constants
# ---------------------------------------------------------------------------

_TABLE_FEC_COMMITTEE = "fec_committee"
_TABLE_CONTRIBUTION = "contribution"
# Sentinel — not a real canonical DB table; the write/resolution layer
# consumes these rows to populate match_decision or drive entity resolution.
_TABLE_LINKAGE_HINT = "_fec_linkage_hint"

# ---------------------------------------------------------------------------
# Individual plan builders
# ---------------------------------------------------------------------------


def plan_fec_committees(
    records: Iterable[CommitteeRecord],
) -> dict[str, Any]:
    """Return a batch operation plan for the fec_committee table.

    Conflict identity is fec_committee_id (the FEC-assigned unique key).
    Official FEC IDs are preserved as returned by the transform layer.
    Raises ValueError for any record with an invalid fec_committee_id.
    """
    rows = [committee_to_row(r) for r in records]
    return {
        "table": _TABLE_FEC_COMMITTEE,
        "rows": rows,
        "conflict_columns": ["fec_committee_id"],
        "mode": "upsert",
    }


def plan_contributions(
    records: Iterable[ContributionRecord],
) -> dict[str, Any]:
    """Return a batch operation plan for the contribution table.

    Conflict identity is source_record_id (the FEC sub_id unique row key).
    FK columns (recipient_fec_committee_id) are left as raw string variants;
    the write layer resolves them to bigint FKs without coupling this layer
    to DB state.
    Raises ValueError for any record missing contribution_date or with an
    invalid fec_committee_id.
    """
    rows = [contribution_to_row(r) for r in records]
    return {
        "table": _TABLE_CONTRIBUTION,
        "rows": rows,
        "conflict_columns": ["source_record_id"],
        "mode": "upsert",
    }


def plan_linkage_hints(
    records: Iterable[CandidateCommitteeLinkage],
) -> dict[str, Any]:
    """Return a hint batch for unresolved candidate-committee linkages.

    The table sentinel '_fec_linkage_hint' is not written to the canonical
    schema directly.  The write/entity-resolution layer uses these rows to
    populate match_decision records or to drive bioguide_id lookups.
    Member resolution is intentionally NOT performed here.
    """
    rows = [linkage_to_row(r) for r in records]
    return {
        "table": _TABLE_LINKAGE_HINT,
        "rows": rows,
        "conflict_columns": ["fec_candidate_id", "fec_committee_id"],
        "mode": "hints",
    }


# ---------------------------------------------------------------------------
# Ordered full-batch plan
# ---------------------------------------------------------------------------


def plan_fec_load(
    committees: Iterable[CommitteeRecord],
    contributions: Iterable[ContributionRecord],
    linkages: Iterable[CandidateCommitteeLinkage],
) -> list[dict[str, Any]]:
    """Return an ordered list of operation plan dicts for a full FEC load.

    Order is significant:
      1. fec_committee — no FK dependencies within this batch
      2. contribution  — FK into fec_committee; must follow committee rows
      3. linkage hints — no canonical-schema FK; resolution happens later

    No DB writes are performed; this function is purely structural.
    """
    return [
        plan_fec_committees(committees),
        plan_contributions(contributions),
        plan_linkage_hints(linkages),
    ]
