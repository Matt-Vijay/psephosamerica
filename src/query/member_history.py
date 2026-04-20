"""Assemble a MemberHistoryPayload from canonical DB row dicts.

Pure helpers — no SQL, no DB, no I/O.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.export.contracts import (
    HistoricalCommitteeMembership,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
)


def _normalize_member(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bioguide_id": row["bioguide_id"],
        "name": row["full_name"],
        "slug": row["slug"],
        "state": row["state"],
        "district": str(row["district"]) if row.get("district") is not None else None,
        "chamber": row["chamber"],
        "party": row["party"],
    }


def _normalize_snapshots(snapshot_rows: list[dict[str, Any]]) -> list[MemberHistorySnapshot]:
    ordered = sorted(snapshot_rows, key=lambda row: row["snapshot_at"])
    history: list[MemberHistorySnapshot] = []
    previous_total: float | None = None
    for row in ordered:
        score_total = float(row["score_total"])
        history.append(
            MemberHistorySnapshot(
                snapshot_date=row["snapshot_at"],
                score_total=score_total,
                score_total_delta=(
                    None if previous_total is None else score_total - previous_total
                ),
                dimension_scores={
                    key: float(value)
                    for key, value in (row.get("dimension_scores") or {}).items()
                },
                published_at=row.get("published_at"),
            )
        )
        previous_total = score_total
    return history


def _normalize_events(fire_rows: list[dict[str, Any]]) -> list[MemberHistoryEvent]:
    ordered = sorted(
        fire_rows,
        key=lambda row: (
            row.get("fired_at") is None,
            row.get("fired_at") or date.min,
            row.get("evidence_card_id") or "",
            row.get("rule_id") or "",
        ),
        reverse=True,
    )
    return [
        MemberHistoryEvent(
            rule_id=row["rule_id"],
            dimension=row["dimension"],
            severity=row["severity"],
            evidence_card_id=row.get("evidence_card_id"),
            short_explanation=row["short_explanation"],
            score_delta=float(row["score_delta"]),
            snapshot_date=row.get("snapshot_date"),
            fired_at=row.get("fired_at"),
        )
        for row in ordered
    ]


def _normalize_committee_history(
    rows: list[dict[str, Any]],
) -> list[HistoricalCommitteeMembership]:
    seen: set[tuple[str, str | None, date | None, date | None]] = set()
    normalized: list[HistoricalCommitteeMembership] = []
    for row in rows:
        key = (
            row["committee_name"],
            row.get("role"),
            row.get("start_date"),
            row.get("end_date"),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            HistoricalCommitteeMembership(
                committee_name=row["committee_name"],
                role=row.get("role"),
                start_date=row.get("start_date"),
                end_date=row.get("end_date"),
                is_current=bool(row.get("is_current")),
                chamber=row["chamber"],
                committee_type=row["committee_type"],
            )
        )
    return sorted(
        normalized,
        key=lambda row: (
            not row.is_current,
            row.start_date or date.min,
            row.committee_name,
            row.role or "",
        ),
    )


def assemble_member_history(
    member_row: dict[str, Any],
    score_snapshot_rows: list[dict[str, Any]],
    rule_fire_rows: list[dict[str, Any]],
    committee_rows: list[dict[str, Any]],
) -> MemberHistoryPayload:
    member = _normalize_member(member_row)
    return MemberHistoryPayload(
        bioguide_id=member["bioguide_id"],
        name=member["name"],
        slug=member["slug"],
        state=member["state"],
        district=member["district"],
        chamber=member["chamber"],
        party=member["party"],
        snapshots=_normalize_snapshots(score_snapshot_rows),
        events=_normalize_events(rule_fire_rows),
        committee_history=_normalize_committee_history(committee_rows),
    )
