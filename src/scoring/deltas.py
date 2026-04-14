"""Snapshot-to-snapshot diff helpers.

Pure helpers only.  No I/O, no database access, no network calls.

Input shape: row-shaped dicts from ``score_snapshot`` plus joined member
identity fields.  A snapshot row looks like::

    {
        "member_id": 1,
        "bioguide_id": "A000001",        # joined from member table
        "snapshot_at": datetime.date(...),  # or ISO string
        "score_total": 85.5,
        "dimension_scores": {             # keyed by dimension name
            "conflict_of_interest_risk": 85.5,
        },
    }

Output shape: list of score-delta dicts compatible with
``src.feed.changes.events_from_score_deltas``::

    {
        "member_bioguide_id": "A000001",
        "dimension": "conflict_of_interest_risk",
        "delta": -15.0,
        "snapshot_date": datetime.date(...),
        "explanation": "",
    }
"""

from __future__ import annotations

import datetime as dt
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _dimension_scores(row: dict[str, Any]) -> dict[str, float]:
    raw = row.get("dimension_scores") or {}
    return {str(k): float(v) for k, v in raw.items()}


def _bioguide_id(row: dict[str, Any]) -> str:
    bid = row.get("bioguide_id") or row.get("member_bioguide_id")
    if not bid:
        raise KeyError("snapshot row missing 'bioguide_id' (join it from member table)")
    return str(bid)


# ---------------------------------------------------------------------------
# Core diff: one member
# ---------------------------------------------------------------------------


def diff_one_member(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    include_first_time: bool = False,
) -> list[dict[str, Any]]:
    """Return per-dimension delta dicts for a single member.

    Args:
        current: Current snapshot row (must include ``bioguide_id``,
            ``snapshot_at``, and ``dimension_scores``).
        previous: Previous snapshot row, or ``None`` if this is the member's
            first known snapshot.
        include_first_time: When ``True`` and ``previous`` is ``None``,
            treat all previous dimension values as ``0.0`` and emit deltas
            for every dimension in ``current``.  When ``False`` (default),
            returns an empty list if ``previous`` is ``None``.

    Returns:
        List of score-delta dicts, one per dimension in ``current``.
        Dimensions present in ``previous`` but absent from ``current`` are
        silently skipped (the score no longer exists; emit nothing).
        Dimensions present in ``current`` but absent from ``previous`` treat
        the previous value as ``0.0``.
    """
    if previous is None and not include_first_time:
        return []

    bioguide_id = _bioguide_id(current)
    snapshot_date = _coerce_date(current["snapshot_at"])
    curr_dims = _dimension_scores(current)
    prev_dims: dict[str, float] = _dimension_scores(previous) if previous is not None else {}

    deltas: list[dict[str, Any]] = []
    for dimension, curr_val in curr_dims.items():
        prev_val = prev_dims.get(dimension, 0.0)
        delta = curr_val - prev_val
        deltas.append(
            {
                "member_bioguide_id": bioguide_id,
                "dimension": dimension,
                "delta": delta,
                "snapshot_date": snapshot_date,
                "explanation": "",
            }
        )
    return deltas


# ---------------------------------------------------------------------------
# Batch diff: many members
# ---------------------------------------------------------------------------


def diff_many_members(
    current_rows: list[dict[str, Any]],
    previous_by_bioguide: dict[str, dict[str, Any]],
    *,
    include_first_time: bool = False,
) -> list[dict[str, Any]]:
    """Return per-member per-dimension delta dicts for a collection of snapshots.

    Args:
        current_rows: List of current snapshot rows.  Each must include
            ``bioguide_id``, ``snapshot_at``, and ``dimension_scores``.
        previous_by_bioguide: Mapping of bioguide_id → previous snapshot row.
            Members absent from this mapping are treated as first-time
            snapshots.
        include_first_time: Forwarded to :func:`diff_one_member`.

    Returns:
        Flat list of score-delta dicts across all members and dimensions.
        Order matches ``current_rows`` order, then dimension insertion order
        within each member.
    """
    result: list[dict[str, Any]] = []
    for row in current_rows:
        bid = _bioguide_id(row)
        previous = previous_by_bioguide.get(bid)
        result.extend(
            diff_one_member(row, previous, include_first_time=include_first_time)
        )
    return result


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def filter_changed(
    deltas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only delta dicts where ``delta != 0.0``.

    Comparison uses exact float equality.  Because scores are computed
    deterministically from the same inputs this is safe; there is no
    floating-point drift between snapshots for identical inputs.
    """
    return [d for d in deltas if d["delta"] != 0.0]
