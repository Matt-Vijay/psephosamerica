from __future__ import annotations

from pathlib import Path

from src.db.repositories import ConnectionLike

_DB_DIR = Path(__file__).resolve().parents[2] / "db"
_SCHEMA_PATH = _DB_DIR / "schema.sql"
_MIGRATION_PATH = _DB_DIR / "migrations" / "0001_init.sql"


def read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def read_migration_sql() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def apply_sql(conn: ConnectionLike, sql: str) -> None:
    # sql is executed as a single string; wrap in BEGIN/COMMIT when transactional behaviour is needed
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
