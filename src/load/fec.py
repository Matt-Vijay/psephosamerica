"""FEC canonical load-plan layer.

Produces ordered table-batch operation plan dicts; no DB writes.

Each plan dict has:
  table:            str           canonical table name (or sentinel for hints)
  rows:             list[dict]    transform-output rows for the write layer
  conflict_columns: list[str]     ON CONFLICT identity columns
  mode:             str           "upsert" | "hints"
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

_TABLE_FEC_COMMITTEE = "fec_committee"
_TABLE_CONTRIBUTION = "contribution"
# Not a canonical DB table — write/entity-resolution layer consumes these
# rows to populate match_decision or drive bioguide_id lookups.
_TABLE_LINKAGE_HINT = "_fec_linkage_hint"


def plan_fec_committees(
    records: Iterable[CommitteeRecord],
) -> dict[str, Any]:
    """Conflict identity: fec_committee_id. Raises ValueError for invalid IDs."""
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
    """Conflict identity: source_record_id (FEC sub_id).
    recipient_fec_committee_id FK is left as raw string; write layer resolves to bigint.
    Raises ValueError for records missing contribution_date or with invalid fec_committee_id.
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
    """Hint batch for unresolved candidate-committee linkages.
    Member resolution is NOT performed here — that is the entity-resolution layer.
    """
    rows = [linkage_to_row(r) for r in records]
    return {
        "table": _TABLE_LINKAGE_HINT,
        "rows": rows,
        "conflict_columns": ["fec_candidate_id", "fec_committee_id"],
        "mode": "hints",
    }


def plan_fec_load(
    committees: Iterable[CommitteeRecord],
    contributions: Iterable[ContributionRecord],
    linkages: Iterable[CandidateCommitteeLinkage],
) -> list[dict[str, Any]]:
    """Ordered operation plan for a full FEC load.

    Execute in list order:
      1. fec_committee — no FK dependencies within this batch
      2. contribution  — FK into fec_committee; must follow committee rows
      3. linkage hints — no canonical-schema FK; resolution happens later
    """
    return [
        plan_fec_committees(committees),
        plan_contributions(contributions),
        plan_linkage_hints(linkages),
    ]
