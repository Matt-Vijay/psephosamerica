from __future__ import annotations

import src.db.bootstrap as _db


def load_schema_sql() -> str:
    return _db.read_schema_sql()


def load_initial_migration_sql() -> str:
    return _db.read_migration_sql()


def bootstrap_database(conn) -> None:
    _db.apply_sql(conn, load_schema_sql())
    _db.apply_sql(conn, load_initial_migration_sql())
