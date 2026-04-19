"""DB-backed row fetchers for disclosure source artifacts.

Returns plain row dicts from fetch_all — no parsing, no normalization.
Chamber is inferred from data_source.slug, which encodes the portal
origin ('house-disclosures' or 'senate-disclosures').

Boundary: callers drive iteration; these functions answer
"give me the artifact rows I need to process disclosures."
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all

_HOUSE_SLUG = "house-disclosures"
_SENATE_SLUG = "senate-disclosures"
_DISCLOSURE_SLUGS = (_HOUSE_SLUG, _SENATE_SLUG)

# Chamber is a function of the data_source portal, not a stored column on
# source_artifact.  The CASE expression makes that derivation explicit.
_CHAMBER_CASE = "CASE ds.slug WHEN 'house-disclosures' THEN 'house' WHEN 'senate-disclosures' THEN 'senate' END"

_SELECT_COLS = f"""
    sa.id,
    sa.artifact_kind,
    sa.source_url,
    sa.storage_uri,
    sa.sha256,
    sa.mime_type,
    sa.fetched_at,
    sa.source_record_id,
    sa.is_immutable,
    sa.ingestion_run_id,
    sa.data_source_id,
    ds.slug        AS source_slug,
    {_CHAMBER_CASE} AS chamber,
    fd.filing_year
""".strip()

_FROM_CLAUSE = """
FROM source_artifact sa
JOIN  data_source        ds ON ds.id = sa.data_source_id
LEFT JOIN financial_disclosure fd ON fd.source_artifact_id = sa.id
""".strip()

_ORDER_CLAUSE = "ORDER BY sa.fetched_at DESC NULLS LAST, sa.id"


def _build_params(
    chamber: str | None,
    year: int | None,
    limit: int | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"slugs": _DISCLOSURE_SLUGS}
    if chamber is not None:
        params["chamber_slug"] = f"{chamber}-disclosures"
    if year is not None:
        params["year"] = year
    if limit is not None:
        params["limit"] = limit
    return params


def fetch_disclosure_artifact_rows(
    conn: ConnectionLike,
    *,
    chamber: str | None = None,
    year: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return source_artifact rows for House and Senate disclosure PDFs.

    chamber: 'house' or 'senate'; None returns both.
    year: filters by financial_disclosure.filing_year; artifacts not yet
          linked to a financial_disclosure row will have filing_year=NULL
          and will be excluded when year is set.
    limit: caps returned rows; None means no cap.
    """
    parts = [
        f"SELECT {_SELECT_COLS}",
        _FROM_CLAUSE,
        "WHERE ds.slug IN %(slugs)s",
    ]
    if chamber is not None:
        parts.append("  AND ds.slug = %(chamber_slug)s")
    if year is not None:
        parts.append("  AND fd.filing_year = %(year)s")
    parts.append(_ORDER_CLAUSE)
    if limit is not None:
        parts.append("LIMIT %(limit)s")

    return fetch_all(conn, "\n".join(parts), _build_params(chamber, year, limit))


def fetch_unparsed_disclosure_artifact_rows(
    conn: ConnectionLike,
    *,
    chamber: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return disclosure source_artifact rows with no succeeded parse_run.

    An artifact is unparsed when no parse_run with status='succeeded' exists
    for it.  Use this to drive the parse queue.
    """
    parts = [
        f"SELECT {_SELECT_COLS}",
        _FROM_CLAUSE,
        "WHERE ds.slug IN %(slugs)s",
        """  AND NOT EXISTS (
        SELECT 1
          FROM parse_run pr
         WHERE pr.source_artifact_id = sa.id
           AND pr.status = 'succeeded'
    )""",
    ]
    if chamber is not None:
        parts.append("  AND ds.slug = %(chamber_slug)s")
    parts.append(_ORDER_CLAUSE)
    if limit is not None:
        parts.append("LIMIT %(limit)s")

    return fetch_all(conn, "\n".join(parts), _build_params(chamber, None, limit))
