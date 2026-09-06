"""DB helpers for data source registration and ingestion run lifecycle.

Wraps the low-level repository functions in src/db/repositories.py.
Every function is row-shaped: inputs are plain values, outputs are
dicts that mirror the underlying table columns.

No ORM behaviour; no business logic; no network calls.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.db.repositories import execute_one, fetch_all, insert_returning_id as _insert_returning_id


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


# Data source


def ensure_data_source(
    conn: Any,
    slug: str,
    name: str,
    source_kind: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Return the data_source row for *slug*, inserting it if absent.

    ON CONFLICT DO NOTHING preserves any existing row; the caller always
    gets back the current DB state.
    """
    execute_one(
        conn,
        """
        INSERT INTO data_source (slug, name, source_kind, base_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (slug) DO NOTHING
        """,
        (slug, name, source_kind, base_url),
    )
    rows = fetch_all(conn, "SELECT * FROM data_source WHERE slug = %s", (slug,))
    return rows[0]


# Ingestion run lifecycle


def start_ingestion_run(
    conn: Any,
    data_source_id: int,
    run_type: str,
    parameters: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> int:
    """Insert a new ingestion_run row in 'running' status and return its id."""
    return _insert_returning_id(
        conn,
        """
        INSERT INTO ingestion_run
               (data_source_id, run_type, status, started_at, parameters)
        VALUES (%s, %s, 'running', %s, %s)
        RETURNING id
        """,
        (data_source_id, run_type, _utcnow(), json.dumps(parameters or {})),
        commit=commit,
    )


def finish_ingestion_run(
    conn: Any,
    run_id: int,
    record_count: int,
    *,
    commit: bool = True,
) -> None:
    """Mark an ingestion_run as succeeded and record the final row count."""
    now = _utcnow()
    execute_one(
        conn,
        """
        UPDATE ingestion_run
           SET status = 'succeeded',
               finished_at = %s,
               record_count = %s,
               updated_at = %s
        WHERE id = %s
        """,
        (now, record_count, now, run_id),
        commit=commit,
    )


def fail_ingestion_run(
    conn: Any,
    run_id: int,
    error_message: str,
    *,
    commit: bool = True,
) -> None:
    """Mark an ingestion_run as failed and store the error message."""
    now = _utcnow()
    execute_one(
        conn,
        """
        UPDATE ingestion_run
           SET status = 'failed',
               finished_at = %s,
               error_message = %s,
               updated_at = %s
         WHERE id = %s
        """,
        (now, error_message, now, run_id),
        commit=commit,
    )
