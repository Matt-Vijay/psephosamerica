"""DuckDB catalog, temporal macros, status and a deliberately small query API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from src.time_machine.model import TABLE_SCHEMAS

CATALOG_FILENAME = "time_machine.duckdb"
PARQUET_DIRNAME = "parquet"
_FACT_TABLES = tuple(name for name in TABLE_SCHEMAS if name != "source_artifacts")


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def create_catalog(output_root: Path) -> Path:
    """Create a tiny catalog of views/macros; Parquet remains canonical storage."""
    output_root.mkdir(parents=True, exist_ok=True)
    catalog_path = output_root / CATALOG_FILENAME
    connection = duckdb.connect(str(catalog_path))
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS tm")
        for table in TABLE_SCHEMAS:
            parquet = output_root / PARQUET_DIRNAME / f"{table}.parquet"
            connection.execute(
                f"CREATE OR REPLACE VIEW tm.{table} AS "
                f"SELECT * FROM read_parquet('{_sql_path(parquet)}')"
            )
        for table in _FACT_TABLES:
            condition = """
                available_at IS NOT NULL
                AND available_at <= cutoff
                AND (event_at IS NULL OR event_at <= cutoff)
                AND (valid_from IS NULL OR valid_from <= cutoff)
                AND (valid_to IS NULL OR cutoff < valid_to)
            """
            connection.execute(
                f"CREATE OR REPLACE MACRO tm.{table}_as_of(cutoff) AS TABLE "
                f"SELECT * FROM tm.{table} WHERE {condition}"
            )
            connection.execute(
                f"CREATE OR REPLACE MACRO tm.{table}_as_observed(cutoff) AS TABLE "
                f"SELECT * FROM tm.{table} WHERE {condition} AND observed_at <= cutoff"
            )

        union_parts: list[str] = []
        for table in _FACT_TABLES:
            key = TABLE_SCHEMAS[table].names[0]
            union_parts.append(
                "SELECT "
                f"'{table}' AS table_name, CAST({key} AS VARCHAR) AS record_id, "
                "event_at, available_at, availability_basis, observed_at, valid_from, valid_to, "
                f"source_artifact_id FROM tm.{table}"
            )
        connection.execute(
            "CREATE OR REPLACE VIEW tm.fact_index AS " + " UNION ALL ".join(union_parts)
        )
        connection.execute(
            """
            CREATE OR REPLACE MACRO tm.as_of(cutoff) AS TABLE
            SELECT * FROM tm.fact_index
            WHERE available_at IS NOT NULL
              AND available_at <= cutoff
              AND (event_at IS NULL OR event_at <= cutoff)
              AND (valid_from IS NULL OR valid_from <= cutoff)
              AND (valid_to IS NULL OR cutoff < valid_to)
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE MACRO tm.as_observed(cutoff) AS TABLE
            SELECT * FROM tm.as_of(cutoff) WHERE observed_at <= cutoff
            """
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return catalog_path


def connect(output_root: Path, *, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    path = output_root / CATALOG_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"catalog missing: run build first ({path})")
    return duckdb.connect(str(path), read_only=read_only)


def query(output_root: Path, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run one read-only analytical query and return columns plus rows."""
    allowed = ("select", "with", "show", "describe", "explain", "pragma")
    if not sql.lstrip().lower().startswith(allowed):
        raise ValueError("query command accepts read-only SQL only")
    connection = connect(output_root, read_only=True)
    try:
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        return columns, cursor.fetchall()
    finally:
        connection.close()


def status(output_root: Path) -> dict[str, Any]:
    """Return concise machine-readable state without touching source data."""
    result: dict[str, Any] = {
        "output_root": str(output_root.resolve()),
        "inventory": None,
        "build": None,
        "tables": {},
        "integrity": None,
    }
    inventory_path = output_root / "inventory.json"
    if inventory_path.exists():
        raw = json.loads(inventory_path.read_text(encoding="utf-8"))
        result["inventory"] = {
            key: raw.get(key)
            for key in (
                "schema_version",
                "generated_at",
                "source_root",
                "state_limit",
                "artifact_count",
                "byte_count",
            )
        }
    build_path = output_root / "build.json"
    if build_path.exists():
        result["build"] = json.loads(build_path.read_text(encoding="utf-8"))
    integrity_path = output_root / "integrity.json"
    if integrity_path.exists():
        raw = json.loads(integrity_path.read_text(encoding="utf-8"))
        result["integrity"] = {
            "status": raw.get("status"),
            "generated_at": raw.get("generated_at"),
            "hard_failures": raw.get("hard_failures"),
            "text_version_coverage": raw.get("text_version_coverage"),
        }
    if (output_root / CATALOG_FILENAME).exists():
        connection = connect(output_root)
        try:
            for table in TABLE_SCHEMAS:
                result["tables"][table] = connection.execute(
                    f"SELECT count(*) FROM tm.{table}"
                ).fetchone()[0]
        finally:
            connection.close()
    return result


__all__ = ["CATALOG_FILENAME", "PARQUET_DIRNAME", "connect", "create_catalog", "query", "status"]
