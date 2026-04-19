"""DB row fetchers for disclosure member resolution.

Provides the raw member roster slices needed to resolve the name/state/chamber
keys carried by House and Senate disclosure index rows.  No payload shaping,
no match logic, no scoring — callers own resolution.

House disclosures use name + state_dst (e.g. "CA08") as resolution keys, so
district is included via member_term.  Senate disclosures use name + state, so
no term join is required.
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all

# ---------------------------------------------------------------------------
# fetch_house_member_rows
# ---------------------------------------------------------------------------

_HOUSE_MEMBER_SQL = """
SELECT DISTINCT ON (m.bioguide_id)
    m.bioguide_id,
    m.first_name,
    m.last_name,
    m.state,
    mt.district
FROM member m
JOIN member_term mt ON mt.member_id = m.id AND mt.chamber = 'house'
WHERE m.is_current = true
  AND m.chamber = 'house'
ORDER BY m.bioguide_id, mt.start_date DESC
"""


def fetch_house_member_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    """Return one row per current House member with state and district.

    District comes from the most recent house member_term.  Callers combine
    state + zero-padded district to match the state_dst field on HouseIndexRow.
    """
    return fetch_all(conn, _HOUSE_MEMBER_SQL)


# ---------------------------------------------------------------------------
# fetch_senate_member_rows
# ---------------------------------------------------------------------------

_SENATE_MEMBER_SQL = """
SELECT
    bioguide_id,
    first_name,
    last_name,
    state
FROM member
WHERE is_current = true
  AND chamber = 'senate'
ORDER BY bioguide_id
"""


def fetch_senate_member_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    """Return one row per current Senate member with state.

    Callers match against the name + state encoded in SenateIndexRow.office
    (e.g. "Senator, TX").
    """
    return fetch_all(conn, _SENATE_MEMBER_SQL)


# ---------------------------------------------------------------------------
# fetch_member_rows_for_disclosures
# ---------------------------------------------------------------------------


def fetch_member_rows_for_disclosures(
    conn: ConnectionLike,
    *,
    chamber: str,
) -> list[dict[str, Any]]:
    """Dispatch to the chamber-appropriate row fetcher.

    Args:
        conn:    DB connection passed through to fetch_all.
        chamber: ``"house"`` or ``"senate"``.

    Raises:
        ValueError: If *chamber* is not ``"house"`` or ``"senate"``.
    """
    if chamber == "house":
        return fetch_house_member_rows(conn)
    if chamber == "senate":
        return fetch_senate_member_rows(conn)
    raise ValueError(f"chamber must be 'house' or 'senate', got {chamber!r}")
