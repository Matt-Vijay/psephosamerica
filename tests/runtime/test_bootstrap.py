from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import src.runtime.bootstrap as bootstrap


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


def test_bootstrap_database_applies_schema_then_migration():
    conn = MagicMock()
    with (
        patch("src.db.bootstrap.read_schema_sql", return_value="schema"),
        patch("src.db.bootstrap.read_migration_sql", return_value="migration"),
        patch("src.db.bootstrap.apply_sql") as mock_apply,
    ):
        bootstrap.bootstrap_database(conn)

    assert mock_apply.call_count == 2
    mock_apply.assert_has_calls([
        call(conn, "schema"),
        call(conn, "migration"),
    ])


def test_bootstrap_database_passes_same_conn_to_both_calls():
    conn = MagicMock()
    received_conns: list = []

    def capture_apply(c, _sql):
        received_conns.append(c)

    with (
        patch("src.db.bootstrap.read_schema_sql", return_value="s"),
        patch("src.db.bootstrap.read_migration_sql", return_value="m"),
        patch("src.db.bootstrap.apply_sql", side_effect=capture_apply),
    ):
        bootstrap.bootstrap_database(conn)

    assert received_conns == [conn, conn]
