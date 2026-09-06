"""Assemble a MemberProfilePayload from canonical DB row dicts.

Pure helpers — no SQL, no DB, no I/O.

Row shapes expected:
  member_row        — member table
  score_snapshot_rows — score_snapshot table; dimension_scores is a jsonb dict
  rule_fire_rows    — rule_fire JOIN evidence_card; needs rule_id, dimension,
                      evidence_card_id, short_explanation, score_delta, snapshot_date
  committee_rows    — committee_membership JOIN committee; needs committee_name, role
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.export.builders import build_member_profile
from src.export.contracts import MemberProfilePayload


def normalize_member_row(row: dict[str, Any]) -> dict[str, Any]:
    # Maps DB column names to the shape build_member_profile expects.
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
    """Per-dimension score summaries from the most recent snapshot + fire counts.

    Scores come from the latest snapshot's dimension_scores dict.
    rule_fire_count is derived from fire_rows.  Sorted by dimension name.
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

    return sorted(
        [
            {
                "dimension": dim,
                "current_score": float(score),
                "rule_fire_count": fire_counts.get(dim, 0),
            }
            for dim, score in dimension_scores.items()
        ],
        key=lambda s: s["dimension"],
    )


def _normalize_recent_fires(
    fire_rows: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    # Sort most-recent-first; break ties by evidence/rule identity ascending.
    sorted_fires = sorted(
        fire_rows,
        key=lambda r: (
            -(r.get("snapshot_date") or date.min).toordinal(),
            r.get("evidence_card_id") or "",
            r.get("rule_id") or "",
        ),
    )
    return [
        {
            "rule_id": f["rule_id"],
            "evidence_card_id": _required_evidence_card_id(f),
            "short_explanation": f["short_explanation"],
            "score_delta": float(f["score_delta"]),
            "snapshot_date": f["snapshot_date"],
        }
        for f in sorted_fires[:limit]
    ]


def _required_evidence_card_id(fire: dict[str, Any]) -> str:
    card_id = fire.get("evidence_card_id")
    if not isinstance(card_id, str) or not card_id:
        rule_id = fire.get("rule_id", "<unknown>")
        raise ValueError(f"rule fire {rule_id!r} is missing evidence_card_id")
    return card_id


def _top_evidence_card_ids(
    fire_rows: list[dict[str, Any]],
    limit: int = 3,
) -> list[str]:
    seen: set[str] = set()
    top_ids: list[str] = []
    for fire in _normalize_recent_fires(fire_rows, limit=len(fire_rows)):
        card_id = fire.get("evidence_card_id")
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        top_ids.append(card_id)
        if len(top_ids) >= limit:
            break
    return top_ids


def _normalize_committees(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (row["committee_name"], row.get("role"))
        if key not in seen:
            seen.add(key)
            unique.append({"committee_name": row["committee_name"], "role": row.get("role")})
    return sorted(unique, key=lambda c: (c["committee_name"], c["role"] or ""))


def _latest_snapshot_date(snapshot_rows: list[dict[str, Any]]) -> date:
    if not snapshot_rows:
        return date.today()
    latest = max(snapshot_rows, key=lambda row: row["snapshot_at"])
    snapshot_at = latest.get("snapshot_at")
    if isinstance(snapshot_at, date):
        return snapshot_at
    return date.today()


def _count_distinct_evidence_cards(fire_rows: list[dict[str, Any]]) -> int:
    return len({_required_evidence_card_id(fire) for fire in fire_rows})


def assemble_member_profile(
    member_row: dict[str, Any],
    score_snapshot_rows: list[dict[str, Any]],
    rule_fire_rows: list[dict[str, Any]],
    committee_rows: list[dict[str, Any]],
) -> MemberProfilePayload:
    return build_member_profile(
        member=normalize_member_row(member_row),
        score_rows=_extract_score_summaries(score_snapshot_rows, rule_fire_rows),
        recent_fires=_normalize_recent_fires(rule_fire_rows),
        top_evidence_card_ids=_top_evidence_card_ids(rule_fire_rows),
        committee_rows=_normalize_committees(committee_rows),
        total_evidence_cards=_count_distinct_evidence_cards(rule_fire_rows),
        snapshot_date=_latest_snapshot_date(score_snapshot_rows),
    )
