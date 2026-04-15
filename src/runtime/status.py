"""Runtime status query layer.

Operator-facing helpers for fetching pipeline health rows from the DB.
All DB access goes through fetch_all from src.db.repositories.
No live DB required in tests — mock fetch_all at this module's import path.
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import fetch_all

_DEFAULT_LIMIT = 20


def latest_ingestion_runs(conn: Any, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Return the most recent ingestion_run rows, newest first."""
    sql = """
        SELECT
            ir.id,
            ir.run_type,
            ir.status,
            ir.started_at,
            ir.finished_at,
            ir.record_count,
            ir.error_message,
            ir.parameters,
            ds.slug AS data_source_slug,
            ds.name AS data_source_name
        FROM ingestion_run ir
        LEFT JOIN data_source ds ON ds.id = ir.data_source_id
        ORDER BY ir.id DESC
        LIMIT %(limit)s
    """
    return fetch_all(conn, sql, {"limit": limit})


def latest_source_artifacts(conn: Any, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Return the most recent source_artifact rows, newest first."""
    sql = """
        SELECT
            sa.id,
            sa.artifact_kind,
            sa.source_url,
            sa.storage_uri,
            sa.sha256,
            sa.mime_type,
            sa.fetched_at,
            sa.is_immutable,
            sa.source_record_id,
            sa.created_at,
            ds.slug AS data_source_slug,
            ds.name AS data_source_name
        FROM source_artifact sa
        LEFT JOIN data_source ds ON ds.id = sa.data_source_id
        ORDER BY sa.id DESC
        LIMIT %(limit)s
    """
    return fetch_all(conn, sql, {"limit": limit})


def latest_parse_runs(conn: Any, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Return the most recent parse_run rows, newest first."""
    sql = """
        SELECT
            pr.id,
            pr.parser_name,
            pr.parser_version,
            pr.status,
            pr.started_at,
            pr.finished_at,
            pr.page_count,
            pr.ocr_page_count,
            pr.error_message
        FROM parse_run pr
        ORDER BY pr.id DESC
        LIMIT %(limit)s
    """
    return fetch_all(conn, sql, {"limit": limit})


def current_data_sources(conn: Any) -> list[dict[str, Any]]:
    """Return all active data_source rows ordered by slug."""
    sql = """
        SELECT
            id,
            slug,
            name,
            source_kind,
            base_url,
            active,
            created_at,
            updated_at
        FROM data_source
        WHERE active = true
        ORDER BY slug
    """
    return fetch_all(conn, sql)


def runtime_status_dict(
    ingestion_runs: list[dict[str, Any]],
    parse_runs: list[dict[str, Any]],
    data_sources: list[dict[str, Any]],
    *,
    source_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble fetched rows into a single operator status snapshot.

    Pure function: takes already-fetched row lists, returns a plain dict.
    Pass source_artifacts to include artifact rows and count in the output.
    """
    result: dict[str, Any] = {
        "ingestion_runs": ingestion_runs,
        "parse_runs": parse_runs,
        "data_sources": data_sources,
        "summary": {
            "ingestion_run_count": len(ingestion_runs),
            "parse_run_count": len(parse_runs),
            "data_source_count": len(data_sources),
        },
    }
    if source_artifacts is not None:
        result["source_artifacts"] = source_artifacts
        result["summary"]["source_artifact_count"] = len(source_artifacts)
    return result
