from __future__ import annotations

from typing import Any

from src.runtime.output import summarize_status
from src.runtime.status import (
    current_data_sources,
    latest_ingestion_runs,
    latest_parse_runs,
    latest_source_artifacts,
    runtime_status_dict,
)

_DEFAULT_LIMIT = 20


def get_runtime_status(conn: Any, *, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    ingestion_runs = latest_ingestion_runs(conn, limit=limit)
    parse_runs = latest_parse_runs(conn, limit=limit)
    data_sources = current_data_sources(conn)
    source_artifacts = latest_source_artifacts(conn, limit=limit)
    return runtime_status_dict(
        ingestion_runs,
        parse_runs,
        data_sources,
        source_artifacts=source_artifacts,
    )


def get_runtime_status_summary(conn: Any, *, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    return summarize_status(get_runtime_status(conn, limit=limit))
