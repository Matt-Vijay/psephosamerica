"""DB-backed lookup-map loading.

Fetches the minimal row sets needed to build :class:`~src.db.lookups.LookupBundle`
maps, then delegates construction to the pure builders in :mod:`src.db.lookups`.

Public API
----------
fetch_member_lookup_rows(conn)
fetch_committee_lookup_rows(conn)
fetch_fec_committee_lookup_rows(conn)
fetch_financial_disclosure_lookup_rows(conn)
load_lookup_bundle(conn) -> LookupBundle
"""

from __future__ import annotations

from typing import Any

from src.db.lookups import LookupBundle, build_lookup_bundle
from src.db.repositories import ConnectionLike, fetch_all

# ---------------------------------------------------------------------------
# Row fetchers — each pulls only the columns the builder needs
# ---------------------------------------------------------------------------

_MEMBER_SQL = """
SELECT id, bioguide_id, lis_member_id, fec_candidate_id
FROM member
"""

_COMMITTEE_SQL = """
SELECT id, committee_code, congress
FROM committee
"""

_FEC_COMMITTEE_SQL = """
SELECT id, fec_committee_id
FROM fec_committee
"""

_FINANCIAL_DISCLOSURE_SQL = """
SELECT id, member_id, filing_year, filing_type, amendment_number
FROM financial_disclosure
"""


def fetch_member_lookup_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(conn, _MEMBER_SQL)


def fetch_committee_lookup_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(conn, _COMMITTEE_SQL)


def fetch_fec_committee_lookup_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(conn, _FEC_COMMITTEE_SQL)


def fetch_financial_disclosure_lookup_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(conn, _FINANCIAL_DISCLOSURE_SQL)


# ---------------------------------------------------------------------------
# Bundle loader — one call to populate all maps
# ---------------------------------------------------------------------------


def load_lookup_bundle(conn: ConnectionLike) -> LookupBundle:
    """Fetch all lookup rows and build a complete :class:`LookupBundle`.

    Makes four small sequential queries; each touches only the columns the
    corresponding builder needs.  Raises :class:`~src.db.lookups.LookupBuildError`
    if any table has duplicate natural keys.
    """
    return build_lookup_bundle(
        member_rows=fetch_member_lookup_rows(conn),
        committee_rows=fetch_committee_lookup_rows(conn),
        fec_committee_rows=fetch_fec_committee_lookup_rows(conn),
        financial_disclosure_rows=fetch_financial_disclosure_lookup_rows(conn),
    )
