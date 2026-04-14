"""Pure helpers that assemble a MemberProfilePayload from canonical row inputs.

No SQL, no DB, no I/O.

Inputs are raw row dicts as returned by upstream queries:
  - one member row  (member table shape)
  - score_snapshot rows  (score_snapshot table shape; dimension_scores is a jsonb dict)
  - rule_fire rows  (rule_fire joined with evidence_card; includes evidence_card_id,
    score_delta, short_explanation, snapshot_date)
  - committee rows  (committee_membership joined with committee; includes committee_name,
    role)

Output: a validated MemberProfilePayload ready for JSON export.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.export.builders import build_member_profile
from src.export.contracts import MemberProfilePayload


# ---------------------------------------------------------------------------
# Internal normalizers
# ---------------------------------------------------------------------------


def _normalize_member(row: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical member DB row to the dict shape build_member_profile expects."""
    return {
        "bioguide_id": row["bioguide_id"],
        "name": row["full_name"],
        "slug": row["slug"],
        "state": row["state"],
        "district": str(row["district"]) if row.get("district") is not None else None,
        "chamber": row["chamber"],
        "party": row["party"],
    }


def _extract_score_summaries(
    snapshot_rows: list[dict[str, Any]],
    fire_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive per-dimension score summaries from snapshot and rule fire rows.

    Uses the most recent snapshot row's ``dimension_scores`` dict as the source
    of current scores.  Rule fire count per dimension is derived from
    *fire_rows*.  Result is sorted deterministically by dimension name.
    """
    if not snapshot_rows:
        return []

    latest = max(snapshot_rows, key=lambda r: r["snapshot_at"])
    dimension_scores: dict[str, float] = latest.get("dimension_scores") or {}

    fire_counts: dict[str, int] = {}
    for fire in fire_rows:
        dim = fire.get("dimension") or ""
        if dim:
            fire_counts[dim] = fire_counts.get(dim, 0) + 1

    summaries = [
        {
            "dimension": dim,
            "current_score": float(score),
            "rule_fire_count": fire_counts.get(dim, 0),
        }
        for dim, score in dimension_scores.items()
    ]

    return sorted(summaries, key=lambda s: s["dimension"])


def _normalize_recent_fires(
    fire_rows: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return up to *limit* fires sorted most-recent-first then by rule_id.

    Ties on snapshot_date are broken by rule_id ascending for determinism.
    """
    sorted_fires = sorted(
        fire_rows,
        key=lambda r: (r.get("snapshot_date") or date.min, r.get("rule_id") or ""),
        reverse=True,
    )
    return [
        {
            "rule_id": f["rule_id"],
            "evidence_card_id": f["evidence_card_id"],
            "short_explanation": f["short_explanation"],
            "score_delta": float(f["score_delta"]),
            "snapshot_date": f["snapshot_date"],
        }
        for f in sorted_fires[:limit]
    ]


def _normalize_committees(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and sort committee membership rows by name then role."""
    seen: set[tuple[str, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (row["committee_name"], row.get("role"))
        if key not in seen:
            seen.add(key)
            unique.append({"committee_name": row["committee_name"], "role": row.get("role")})

    return sorted(unique, key=lambda c: (c["committee_name"], c["role"] or ""))


def _latest_snapshot_date(snapshot_rows: list[dict[str, Any]]) -> date:
    """Return the most recent snapshot_at date, or today if rows is empty."""
    if not snapshot_rows:
        return date.today()
    return max(r["snapshot_at"] for r in snapshot_rows)


def _count_distinct_evidence_cards(fire_rows: list[dict[str, Any]]) -> int:
    """Count distinct evidence_card_id values across fire rows."""
    return len({f["evidence_card_id"] for f in fire_rows if f.get("evidence_card_id")})


# ---------------------------------------------------------------------------
# Public assembler
# ---------------------------------------------------------------------------


def assemble_member_profile(
    member_row: dict[str, Any],
    score_snapshot_rows: list[dict[str, Any]],
    rule_fire_rows: list[dict[str, Any]],
    committee_rows: list[dict[str, Any]],
) -> MemberProfilePayload:
    """Assemble a MemberProfilePayload from canonical row-shaped inputs.

    Args:
        member_row: Single row from the ``member`` table.
        score_snapshot_rows: Rows from the ``score_snapshot`` table for this
            member.  Each row must have ``snapshot_at`` (date) and
            ``dimension_scores`` (dict mapping dimension str to numeric score).
        rule_fire_rows: Rows from a join of ``rule_fire`` and ``evidence_card``
            for this member.  Each row must have: ``rule_id``, ``dimension``,
            ``evidence_card_id``, ``short_explanation``, ``score_delta``,
            ``snapshot_date``.
        committee_rows: Rows from a join of ``committee_membership`` and
            ``committee`` for this member.  Each row must have
            ``committee_name`` and optionally ``role``.

    Returns:
        Validated MemberProfilePayload.
    """
    member = _normalize_member(member_row)
    scores = _extract_score_summaries(score_snapshot_rows, rule_fire_rows)
    fires = _normalize_recent_fires(rule_fire_rows)
    committees = _normalize_committees(committee_rows)
    snapshot_date = _latest_snapshot_date(score_snapshot_rows)
    total_cards = _count_distinct_evidence_cards(rule_fire_rows)

    return build_member_profile(
        member=member,
        score_rows=scores,
        recent_fires=fires,
        committee_rows=committees,
        total_evidence_cards=total_cards,
        snapshot_date=snapshot_date,
    )
