"""Deterministic score snapshot builders for the score_snapshot table.

Pure helpers — no DB writes, no network calls.

Locked v1 scoring rules:
- Every dimension starts at 100.
- Dimension scores are clamped to [0, 100].
- score_total: one dimension → that score exactly; multiple → arithmetic mean
  rounded to 2 decimal places.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

_SCORE_START: float = 100.0
_SCORE_MIN: float = 0.0
_SCORE_MAX: float = 100.0


def clamp(value: float, lo: float = _SCORE_MIN, hi: float = _SCORE_MAX) -> float:
    return max(lo, min(hi, value))


def group_deltas_by_dimension(delta_rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Group delta values by dimension.

    Accepts rows with either a ``delta`` key (score-delta rows) or
    ``score_delta`` key (evidence-card rows). Rows with neither are skipped.
    """
    grouped: dict[str, list[float]] = {}
    for row in delta_rows:
        dimension = row.get("dimension")
        if not dimension:
            continue
        if "delta" in row:
            value = float(row["delta"])
        elif "score_delta" in row:
            value = float(row["score_delta"])
        else:
            continue
        grouped.setdefault(dimension, []).append(value)
    return grouped


def compute_dimension_scores(
    delta_by_dimension: dict[str, list[float]],
) -> dict[str, float]:
    """Sum deltas per dimension from 100, clamped to [0, 100]."""
    return {
        dim: clamp(_SCORE_START + sum(deltas))
        for dim, deltas in delta_by_dimension.items()
    }


def compute_score_total(dimension_scores: dict[str, float]) -> float:
    """Derive score_total from per-dimension scores.

    One dimension → exact score. Multiple → arithmetic mean (2 dp).
    Raises ValueError if *dimension_scores* is empty.
    """
    if not dimension_scores:
        raise ValueError("dimension_scores must not be empty")
    scores = list(dimension_scores.values())
    if len(scores) == 1:
        return round(scores[0], 2)
    return round(sum(scores) / len(scores), 2)


def build_snapshot_row(
    member: dict[str, Any],
    delta_rows: list[dict[str, Any]],
    snapshot_at: dt.date,
    recompute_run_id: int,
) -> dict[str, Any]:
    """Build one score_snapshot row for *member*.

    Provenance columns (source_artifact_id, published_manifest_sha256, etc.)
    are omitted — the batch loader fills those in.

    Empty *delta_rows* → baseline snapshot (score_total = 100, dimension_scores = {}).
    """
    member_id: int = member["id"]

    if not delta_rows:
        return {
            "member_id": member_id,
            "snapshot_at": snapshot_at,
            "recompute_run_id": recompute_run_id,
            "score_total": round(_SCORE_START, 2),
            "dimension_scores": {},
        }

    grouped = group_deltas_by_dimension(delta_rows)
    dim_scores = compute_dimension_scores(grouped)
    score_total = compute_score_total(dim_scores)

    return {
        "member_id": member_id,
        "snapshot_at": snapshot_at,
        "recompute_run_id": recompute_run_id,
        "score_total": score_total,
        "dimension_scores": dim_scores,
    }


def build_snapshot_rows(
    members: list[dict[str, Any]],
    delta_rows_by_member_id: dict[int, list[dict[str, Any]]],
    snapshot_at: dt.date,
    recompute_run_id: int,
) -> list[dict[str, Any]]:
    """Build one snapshot row per member; members absent from the delta map get baseline rows."""
    return [
        build_snapshot_row(
            member=member,
            delta_rows=delta_rows_by_member_id.get(member["id"], []),
            snapshot_at=snapshot_at,
            recompute_run_id=recompute_run_id,
        )
        for member in members
    ]
