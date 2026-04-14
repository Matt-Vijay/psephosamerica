"""Deterministic score snapshot builders for the score_snapshot table.

Pure helpers only — no DB writes, no network calls.

Each public function accepts plain dicts (row-shaped) and returns plain dicts
or primitives. The output shape matches the canonical ``score_snapshot`` table
defined in db/schema.sql.

Locked v1 scoring rules (ENGINEERING_SPEC_V1.md §9):
- Every dimension starts at 100.
- Signed deltas are applied in deterministic order (ascending delta value,
  then ascending rule_fire_id if present, to break ties).
- Dimension scores are clamped to [0, 100].
- score_total: single dimension → equal to that dimension score;
  multiple dimensions → arithmetic mean rounded to 2 decimal places.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

_SCORE_START: float = 100.0
_SCORE_MIN: float = 0.0
_SCORE_MAX: float = 100.0


def clamp(value: float, lo: float = _SCORE_MIN, hi: float = _SCORE_MAX) -> float:
    """Clamp *value* to [*lo*, *hi*].

    Args:
        value: The value to clamp.
        lo: Lower bound (inclusive). Defaults to 0.0.
        hi: Upper bound (inclusive). Defaults to 100.0.

    Returns:
        *value* clamped into [lo, hi].
    """
    return max(lo, min(hi, value))


def group_deltas_by_dimension(delta_rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Group score deltas by dimension name.

    Accepts rows that carry the delta under either the ``delta`` key
    (score-delta rows) or the ``score_delta`` key (evidence-card rows).
    Rows that carry neither key are silently skipped so callers can pass
    heterogeneous lists without pre-filtering.

    Ordering within each group is preserved (insertion order of *delta_rows*).

    Args:
        delta_rows: Sequence of dicts, each with a ``dimension`` field and
            one of ``delta`` or ``score_delta``.

    Returns:
        Dict mapping dimension name → ordered list of float deltas.
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


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_dimension_scores(
    delta_by_dimension: dict[str, list[float]],
) -> dict[str, float]:
    """Compute a per-dimension score for each entry in *delta_by_dimension*.

    Each dimension starts at 100.  Deltas are summed and the result is
    clamped to [0, 100].  The order of deltas within a dimension is
    preserved from the input list (callers are responsible for ordering
    before this call if a specific order is required).

    Args:
        delta_by_dimension: Mapping from dimension name to an ordered list
            of signed float deltas.  An empty delta list means no rule fired
            for that dimension; the score stays at 100.

    Returns:
        Dict mapping dimension name → clamped float score.
    """
    return {
        dim: clamp(_SCORE_START + sum(deltas))
        for dim, deltas in delta_by_dimension.items()
    }


def compute_score_total(dimension_scores: dict[str, float]) -> float:
    """Derive the overall score_total from per-dimension scores.

    Rules:
    - Single dimension: score_total equals that dimension's score exactly.
    - Multiple dimensions: arithmetic mean rounded to 2 decimal places.
    - Empty input: raises ValueError (no dimensions means no snapshot can
      be built).

    Args:
        dimension_scores: Mapping from dimension name → float score.

    Returns:
        Computed score_total as a float rounded to 2 decimal places.

    Raises:
        ValueError: If *dimension_scores* is empty.
    """
    if not dimension_scores:
        raise ValueError("dimension_scores must not be empty")
    scores = list(dimension_scores.values())
    if len(scores) == 1:
        return round(scores[0], 2)
    return round(sum(scores) / len(scores), 2)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_snapshot_row(
    member: dict[str, Any],
    delta_rows: list[dict[str, Any]],
    snapshot_at: dt.date,
    recompute_run_id: int,
) -> dict[str, Any]:
    """Build one row-shaped payload for the ``score_snapshot`` table.

    The returned dict contains exactly the columns that the batch loader
    is responsible for writing; provenance columns (``source_artifact_id``,
    ``source_record_id``, ``published_manifest_sha256``, ``published_at``)
    are omitted and must be filled in by the loader.

    Scoring is computed from *delta_rows*: each row must have a ``dimension``
    field and one of ``delta`` (score-delta rows) or ``score_delta``
    (evidence-card rows).  Members with no delta rows receive a baseline
    snapshot with every known dimension at 100 and score_total = 100.

    When *delta_rows* is empty the returned snapshot reflects the v1 baseline
    (no rule fired → no score change → score_total = 100, dimension_scores
    is an empty dict by convention since no dimension was touched).

    Args:
        member: Member row dict; must contain an integer ``id`` field.
        delta_rows: Evidence-card or score-delta rows for this member.
        snapshot_at: Date of the snapshot.
        recompute_run_id: PK of the ``ingestion_run`` that produced this
            recompute pass.

    Returns:
        Dict matching the column set of the ``score_snapshot`` table
        (excluding auto-generated and optional provenance columns).

    Raises:
        KeyError: If *member* does not contain an ``id`` field.
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
    """Build one snapshot row per member in *members*.

    Members that have no entry in *delta_rows_by_member_id* receive a
    baseline snapshot (score_total = 100, no dimension_scores).

    The output list preserves the iteration order of *members*.

    Args:
        members: Ordered list of member row dicts; each must contain ``id``.
        delta_rows_by_member_id: Mapping from member ``id`` (int) to that
            member's delta rows.  Missing keys are treated as an empty list.
        snapshot_at: Date applied to every snapshot row.
        recompute_run_id: PK of the ``ingestion_run`` for this recompute.

    Returns:
        List of score_snapshot row dicts, one per member.
    """
    return [
        build_snapshot_row(
            member=member,
            delta_rows=delta_rows_by_member_id.get(member["id"], []),
            snapshot_at=snapshot_at,
            recompute_run_id=recompute_run_id,
        )
        for member in members
    ]
