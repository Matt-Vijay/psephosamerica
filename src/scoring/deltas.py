"""Snapshot-to-snapshot diff helpers.

Pure helpers — no I/O, no database access.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.scoring.semantics import normalize_dimension_scores


def _coerce_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _dimension_scores(row: dict[str, Any]) -> dict[str, float]:
    return normalize_dimension_scores(row.get("dimension_scores"))


def _bioguide_id(row: dict[str, Any]) -> str:
    bid = row.get("bioguide_id") or row.get("member_bioguide_id")
    if not bid:
        raise KeyError("snapshot row missing 'bioguide_id' (join it from member table)")
    return str(bid)


def diff_one_member(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    include_first_time: bool = False,
) -> list[dict[str, Any]]:
    """Return per-dimension delta dicts for one member.

    Returns [] if *previous* is None and *include_first_time* is False.
    When *include_first_time* is True, absent previous values are treated as 0.0.

    Dimensions in *previous* but not in *current* are silently dropped.
    """
    if previous is None and not include_first_time:
        return []

    bioguide_id = _bioguide_id(current)
    snapshot_date = _coerce_date(current["snapshot_at"])
    curr_dims = _dimension_scores(current)
    prev_dims: dict[str, float] = _dimension_scores(previous) if previous is not None else {}

    return [
        {
            "member_bioguide_id": bioguide_id,
            "dimension": dimension,
            "delta": curr_val - prev_dims.get(dimension, 0.0),
            "snapshot_date": snapshot_date,
            "explanation": "",
        }
        for dimension, curr_val in curr_dims.items()
    ]


def diff_many_members(
    current_rows: list[dict[str, Any]],
    previous_by_bioguide: dict[str, dict[str, Any]],
    *,
    include_first_time: bool = False,
) -> list[dict[str, Any]]:
    """Flat list of per-dimension delta dicts across all members in *current_rows*."""
    result: list[dict[str, Any]] = []
    for row in current_rows:
        bid = _bioguide_id(row)
        result.extend(
            diff_one_member(row, previous_by_bioguide.get(bid), include_first_time=include_first_time)
        )
    return result


def filter_changed(deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only deltas where delta != 0.0.

    Exact float equality is safe here: scores are computed deterministically
    from the same inputs, so there is no floating-point drift across snapshots.
    """
    return [d for d in deltas if d["delta"] != 0.0]
