"""DB-backed row fetchers for the recompute runtime.

These helpers are the read side of a full recompute pass.  They return
plain row dicts from fetch_all — no payload shaping, no sector resolution,
no rule logic.

Boundary: callers (rule engine, score writer) drive iteration.  These
functions only answer "give me the rows needed to start a recompute."
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all


# ---------------------------------------------------------------------------
# fetch_recompute_members
# ---------------------------------------------------------------------------

_RECOMPUTE_MEMBERS_ALL_SQL = """
SELECT
    id,
    bioguide_id,
    slug,
    full_name,
    first_name,
    last_name,
    party,
    state,
    chamber,
    current_term_start,
    current_term_end
FROM member
WHERE is_current = true
ORDER BY bioguide_id
"""

_RECOMPUTE_MEMBERS_FILTERED_SQL = """
SELECT
    id,
    bioguide_id,
    slug,
    full_name,
    first_name,
    last_name,
    party,
    state,
    chamber,
    current_term_start,
    current_term_end
FROM member
WHERE is_current = true
  AND bioguide_id = ANY(%(bioguide_ids)s)
ORDER BY bioguide_id
"""


def fetch_recompute_members(
    conn: ConnectionLike,
    *,
    bioguide_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return one row per current member to be processed in this recompute run.

    When bioguide_ids is None, all is_current members are returned.
    When a list is provided, only those members are returned — useful for
    partial reruns scoped to a specific subset of members.
    """
    if bioguide_ids is None:
        return fetch_all(conn, _RECOMPUTE_MEMBERS_ALL_SQL)
    return fetch_all(conn, _RECOMPUTE_MEMBERS_FILTERED_SQL, {"bioguide_ids": bioguide_ids})


# ---------------------------------------------------------------------------
# fetch_previous_score_snapshot_rows
# ---------------------------------------------------------------------------

_PREVIOUS_SCORE_SNAPSHOTS_SQL = """
SELECT
    id,
    member_id,
    recompute_run_id,
    snapshot_at,
    score_total,
    dimension_scores,
    published_at
FROM score_snapshot
WHERE member_id = ANY(%(member_ids)s)
ORDER BY member_id, snapshot_at DESC
"""


def fetch_previous_score_snapshot_rows(
    conn: ConnectionLike,
    *,
    member_ids: list[int],
) -> list[dict[str, Any]]:
    """Return all score_snapshot rows for the given members, newest first.

    Returns all historical snapshots so the caller can decide which to treat
    as the baseline.  Use index_latest_previous_snapshots for a fast O(1)
    lookup of each member's most recent snapshot.
    """
    if not member_ids:
        return []
    return fetch_all(conn, _PREVIOUS_SCORE_SNAPSHOTS_SQL, {"member_ids": member_ids})


# ---------------------------------------------------------------------------
# index_latest_previous_snapshots
# ---------------------------------------------------------------------------


def index_latest_previous_snapshots(
    snapshot_rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Index snapshot rows by member_id, keeping only the latest per member.

    Expects rows ordered by (member_id, snapshot_at DESC) as returned by
    fetch_previous_score_snapshot_rows.  The first row seen for each
    member_id is therefore the most recent snapshot.

    Returns a dict mapping member_id -> latest snapshot row.  Members with
    no prior snapshot are absent from the dict — callers must handle None.
    """
    index: dict[int, dict[str, Any]] = {}
    for row in snapshot_rows:
        member_id: int = row["member_id"]
        if member_id not in index:
            index[member_id] = row
    return index
