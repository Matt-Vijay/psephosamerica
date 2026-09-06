from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.runtime.bootstrap as bootstrap
import src.runtime.commands.core as commands


def test_load_schema_sql_delegates_to_db():
    with patch("src.db.bootstrap.read_schema_sql", return_value="schema") as mock:
        result = bootstrap.load_schema_sql()
    mock.assert_called_once()
    assert result == "schema"


def test_load_initial_migration_sql_delegates_to_db():
    with patch("src.db.bootstrap.read_migration_sql", return_value="migration") as mock:
        result = bootstrap.load_initial_migration_sql()
    mock.assert_called_once()
    assert result == "migration"


def test_bootstrap_database_applies_schema_sql_once():
    conn = MagicMock()
    with (
        patch("src.db.bootstrap.read_schema_sql", return_value="schema"),
        patch("src.db.bootstrap.read_migration_sql") as mock_migration,
        patch("src.db.bootstrap.apply_sql") as mock_apply,
    ):
        bootstrap.bootstrap_database(conn)

    mock_migration.assert_not_called()
    mock_apply.assert_called_once_with(conn, "schema")


def test_bootstrap_database_plan_uses_schema_sql_as_authority():
    with patch("src.db.bootstrap.read_schema_sql", return_value="SELECT 1;"):
        plan = bootstrap.describe_bootstrap_plan()

    assert plan == {
        "bootstrap_authority": "db/schema.sql",
        "bootstrap_sql_bytes": len(b"SELECT 1;"),
    }


def test_handle_bootstrap_db_dry_run_reports_schema_plan_without_db_access():
    args = SimpleNamespace(dry_run=True)
    plan = {
        "bootstrap_authority": "db/schema.sql",
        "bootstrap_sql_bytes": len(b"SELECT 1;"),
    }
    with (
        patch("src.runtime.commands.core.build_runtime") as mock_build,
        patch("src.runtime.commands.core.open_runtime_connection") as mock_open_conn,
        patch("src.runtime.commands.core.describe_bootstrap_plan", return_value=plan) as mock_plan,
    ):
        result = commands._handle_bootstrap_db(args)

    mock_build.assert_not_called()
    mock_open_conn.assert_not_called()
    mock_plan.assert_called_once_with()
    assert result == {
        "ok": True,
        "command": "bootstrap-db",
        "dry_run": True,
        **plan,
    }
