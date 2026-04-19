"""DB-backed row-fetch helpers for ZIP feed assembly.

Supplies rows compatible with zip_feed.assemble_zip_feed:
  - fetch_zip_member_summary_rows -> member_summary_rows param
  - fetch_recent_evidence_ids_by_bioguide -> recent_evidence_ids param
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all


_MEMBER_SUMMARY_SQL = """
WITH latest_snapshot AS (
    SELECT DISTINCT ON (member_id)
        member_id,
        score_total,
        dimension_scores
    FROM score_snapshot
    ORDER BY member_id, snapshot_at DESC
)
SELECT
    m.bioguide_id,
    rf.dimension,
    COALESCE(
        (ls.dimension_scores ->> rf.dimension)::numeric,
        ls.score_total,
        0
    ) AS current_score,
    COUNT(rf.id) AS rule_fire_count
FROM member m
JOIN rule_fire rf ON rf.subject_member_id = m.id
LEFT JOIN latest_snapshot ls ON ls.member_id = m.id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
GROUP BY m.bioguide_id, rf.dimension, ls.score_total, ls.dimension_scores
ORDER BY m.bioguide_id, rf.dimension
"""


def fetch_zip_member_summary_rows(
    conn: ConnectionLike,
    *,
    bioguide_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    """One row per (bioguide_id, dimension) with current_score and rule_fire_count.

    Members with no rule fires are omitted; callers receive an empty list via
    assemble_zip_feed's scores_by_member.get(bioguide_id, []) fallback.
    """
    return fetch_all(conn, _MEMBER_SUMMARY_SQL, {"bioguide_ids": list(bioguide_ids)})


_RECENT_EVIDENCE_SQL = """
SELECT
    m.bioguide_id,
    ec.public_id
FROM evidence_card ec
JOIN member m ON m.id = ec.member_id
WHERE m.bioguide_id = ANY(%(bioguide_ids)s)
  AND ec.rendered_at IS NOT NULL
ORDER BY m.bioguide_id, ec.rendered_at DESC
"""


def fetch_recent_evidence_ids_by_bioguide(
    conn: ConnectionLike,
    *,
    bioguide_ids: list[str] | set[str],
) -> dict[str, list[str]]:
    """Map bioguide_id -> public_ids ordered most-recent-first.

    Callers (zip_feed.assemble_zip_feed) slice to [:3] from the result.
    """
    rows = fetch_all(conn, _RECENT_EVIDENCE_SQL, {"bioguide_ids": list(bioguide_ids)})
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["bioguide_id"], []).append(row["public_id"])
    return out
