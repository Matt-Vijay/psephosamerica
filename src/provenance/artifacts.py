"""DB helpers for source artifacts and parse runs.

Mirrors the lifecycle of source_artifact and parse_run rows defined in
db/schema.sql.  Every function is row-shaped: inputs are plain typed
values, return values are plain dicts or bare ids.

No ORM behaviour; no business logic; no network calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from src.db.repositories import execute_one, fetch_all


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _insert_returning_id(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cast(dict[str, object] | None, cur.fetchone())
    conn.commit()
    if row is None:
        raise ValueError("INSERT ... RETURNING id produced no row")
    row_id = row.get("id")
    if not isinstance(row_id, int):
        raise TypeError(f"expected integer id from INSERT ... RETURNING, got {row_id!r}")
    return row_id


# ---------------------------------------------------------------------------
# Source artifact
# ---------------------------------------------------------------------------


def create_source_artifact(
    conn: Any,
    data_source_id: int,
    artifact_kind: str,
    storage_uri: str,
    sha256: str,
    *,
    ingestion_run_id: int | None = None,
    source_url: str | None = None,
    mime_type: str | None = None,
    fetched_at: datetime | None = None,
    source_record_id: str | None = None,
) -> dict[str, Any]:
    """Insert a source_artifact row and return it.

    storage_uri and sha256 carry UNIQUE constraints; callers must not
    insert the same artifact twice.  fetched_at defaults to now when
    omitted, recording when the artifact entered the system.
    """
    effective_fetched_at = fetched_at or _utcnow()
    artifact_id = _insert_returning_id(
        conn,
        """
        INSERT INTO source_artifact
               (data_source_id, ingestion_run_id, artifact_kind,
                source_url, storage_uri, sha256, mime_type,
                fetched_at, source_record_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            data_source_id,
            ingestion_run_id,
            artifact_kind,
            source_url,
            storage_uri,
            sha256,
            mime_type,
            effective_fetched_at,
            source_record_id,
        ),
    )
    rows = fetch_all(conn, "SELECT * FROM source_artifact WHERE id = %s", (artifact_id,))
    return rows[0]


# ---------------------------------------------------------------------------
# Parse run lifecycle
# ---------------------------------------------------------------------------


def create_parse_run(
    conn: Any,
    source_artifact_id: int,
    parser_name: str,
    parser_version: str,
    *,
    ingestion_run_id: int | None = None,
) -> int:
    """Insert a parse_run row in 'running' status and return its id.

    The (source_artifact_id, parser_name, parser_version) triple is
    unique per the schema; callers must not re-create a run for the
    same artifact and parser combination.
    """
    return _insert_returning_id(
        conn,
        """
        INSERT INTO parse_run
               (source_artifact_id, parser_name, parser_version,
                status, started_at, ingestion_run_id)
        VALUES (%s, %s, %s, 'running', %s, %s)
        RETURNING id
        """,
        (
            source_artifact_id,
            parser_name,
            parser_version,
            _utcnow(),
            ingestion_run_id,
        ),
    )


def finish_parse_run(
    conn: Any,
    run_id: int,
    *,
    page_count: int | None = None,
    ocr_page_count: int | None = None,
    confidence_summary: dict[str, Any] | None = None,
) -> None:
    """Mark a parse_run as succeeded and record extraction metrics."""
    now = _utcnow()
    execute_one(
        conn,
        """
        UPDATE parse_run
           SET status = 'succeeded',
               finished_at = %s,
               page_count = %s,
               ocr_page_count = %s,
               confidence_summary = %s,
               updated_at = %s
         WHERE id = %s
        """,
        (
            now,
            page_count,
            ocr_page_count,
            json.dumps(confidence_summary or {}),
            now,
            run_id,
        ),
    )


def fail_parse_run(conn: Any, run_id: int, error_message: str) -> None:
    """Mark a parse_run as failed and store the error message."""
    now = _utcnow()
    execute_one(
        conn,
        """
        UPDATE parse_run
           SET status = 'failed',
               finished_at = %s,
               error_message = %s,
               updated_at = %s
         WHERE id = %s
        """,
        (now, error_message, now, run_id),
    )
