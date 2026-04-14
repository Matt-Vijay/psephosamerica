"""Bootstrap helpers: read and apply schema/migration SQL files.

Exposes:
  read_schema_sql()    -> str   — contents of db/schema.sql
  read_migration_sql() -> str   — contents of db/migrations/0001_init.sql
  apply_sql(conn, sql) -> None  — execute raw SQL against an open connection
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_DB_DIR = Path(__file__).resolve().parents[2] / "db"
_SCHEMA_PATH = _DB_DIR / "schema.sql"
_MIGRATION_PATH = _DB_DIR / "migrations" / "0001_init.sql"


def read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def read_migration_sql() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def apply_sql(conn, sql: str) -> None:
    """Execute *sql* against *conn* and commit.

    conn must be a psycopg Connection (or any object with .cursor() and
    .commit()).  The SQL is executed as a single string; wrap statements in
    BEGIN/COMMIT if transactional behaviour is required.
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
