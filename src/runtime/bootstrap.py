from __future__ import annotations

from typing import Any

import src.db.bootstrap as _db

_BOOTSTRAP_AUTHORITY = "db/schema.sql"


def load_schema_sql() -> str:
    return _db.read_schema_sql()


def load_initial_migration_sql() -> str:
    return _db.read_migration_sql()


def load_bootstrap_sql() -> str:
    return load_schema_sql()


def describe_bootstrap_plan() -> dict[str, str | int]:
    sql = load_bootstrap_sql()
    return {
        "bootstrap_authority": _BOOTSTRAP_AUTHORITY,
        "bootstrap_sql_bytes": len(sql.encode("utf-8")),
    }


def bootstrap_database(conn: Any) -> None:
    _db.apply_sql(conn, load_bootstrap_sql())
