"""DB runtime contract tests — no live database required.

Tests cover:
  - build_connection_kwargs DSN parsing / field mapping
  - bootstrap file loading (read_schema_sql, read_migration_sql)
  - repository function signatures (inspect-based, no DB)
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db.bootstrap import apply_sql, read_migration_sql, read_schema_sql
from src.db.connection import build_connection_kwargs
from src.db.repositories import execute_many, execute_one, fetch_all

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]


def _make_settings(**overrides):
    defaults = dict(
        db_host="localhost",
        db_port=5432,
        db_name="openpact",
        db_user="openpact_user",
        db_password="secret",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# connection.py — build_connection_kwargs
# ---------------------------------------------------------------------------


class TestBuildConnectionKwargs:
    def test_returns_dict(self):
        result = build_connection_kwargs(_make_settings())
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        result = build_connection_kwargs(_make_settings())
        assert set(result.keys()) == {"host", "port", "dbname", "user", "password"}

    def test_host_mapped(self):
        result = build_connection_kwargs(_make_settings(db_host="db.example.com"))
        assert result["host"] == "db.example.com"

    def test_port_is_int(self):
        result = build_connection_kwargs(_make_settings(db_port="5433"))
        assert result["port"] == 5433
        assert isinstance(result["port"], int)

    def test_dbname_mapped(self):
        result = build_connection_kwargs(_make_settings(db_name="mydb"))
        assert result["dbname"] == "mydb"

    def test_user_mapped(self):
        result = build_connection_kwargs(_make_settings(db_user="alice"))
        assert result["user"] == "alice"

    def test_password_mapped(self):
        result = build_connection_kwargs(_make_settings(db_password="hunter2"))
        assert result["password"] == "hunter2"

    def test_missing_attribute_raises(self):
        bad_settings = SimpleNamespace(db_host="localhost")
        with pytest.raises(AttributeError):
            build_connection_kwargs(bad_settings)

    def test_accepts_settings_with_derived_db_properties(self):
        from src.core.settings import Settings

        settings = Settings(
            postgres_dsn="postgresql://alice:secret@db.example.com:5433/openpact_prod"
        )
        result = build_connection_kwargs(settings)
        assert result == {
            "host": "db.example.com",
            "port": 5433,
            "dbname": "openpact_prod",
            "user": "alice",
            "password": "secret",
        }


# ---------------------------------------------------------------------------
# connection.py — connect (mocked psycopg)
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_calls_psycopg_connect(self):
        settings = _make_settings()
        mock_conn = MagicMock()
        with patch("psycopg.connect", return_value=mock_conn) as mock_connect:
            from src.db.connection import connect

            result = connect(settings)

        expected_kwargs = build_connection_kwargs(settings)
        mock_connect.assert_called_once_with(**expected_kwargs)
        assert result is mock_conn


# ---------------------------------------------------------------------------
# bootstrap.py — file loading
# ---------------------------------------------------------------------------


class TestBootstrapFileLoading:
    def test_read_schema_sql_returns_str(self):
        sql = read_schema_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_read_schema_sql_contains_create_table(self):
        sql = read_schema_sql().lower()
        assert "create table" in sql

    def test_read_schema_sql_contains_member_table(self):
        sql = read_schema_sql().lower()
        assert "create table member" in sql

    def test_read_migration_sql_returns_str(self):
        sql = read_migration_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_read_migration_sql_is_transactional(self):
        sql = read_migration_sql().lower()
        assert "begin;" in sql
        assert "commit;" in sql

    def test_read_migration_sql_contains_all_core_tables(self):
        core_tables = [
            "member", "member_term", "committee", "committee_membership",
            "bill", "bill_sponsor", "vote_event", "vote_cast",
            "fec_committee", "contribution", "financial_disclosure",
            "holding", "rule_fire", "evidence_card", "score_snapshot",
        ]
        sql = read_migration_sql().lower()
        for table in core_tables:
            pattern_plain = f"create table {table}"
            pattern_quoted = f'create table "{table}"'
            assert pattern_plain in sql or pattern_quoted in sql, (
                f"Migration missing CREATE TABLE for '{table}'"
            )

    def test_schema_and_migration_files_exist(self):
        schema_path = _ROOT / "db" / "schema.sql"
        migration_path = _ROOT / "db" / "migrations" / "0001_init.sql"
        assert schema_path.exists()
        assert migration_path.exists()


class TestApplySql:
    def test_apply_sql_executes_and_commits(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        apply_sql(mock_conn, "SELECT 1")

        mock_cur.execute.assert_called_once_with("SELECT 1")
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# repositories.py — signature / contract checks (no DB)
# ---------------------------------------------------------------------------


class TestRepositorySignatures:
    def test_execute_one_signature(self):
        sig = inspect.signature(execute_one)
        params = list(sig.parameters.keys())
        assert params[0] == "conn"
        assert params[1] == "sql"
        assert params[2] == "params"

    def test_execute_many_signature(self):
        sig = inspect.signature(execute_many)
        params = list(sig.parameters.keys())
        assert params[0] == "conn"
        assert params[1] == "sql"
        assert params[2] == "params_seq"

    def test_fetch_all_signature(self):
        sig = inspect.signature(fetch_all)
        params = list(sig.parameters.keys())
        assert params[0] == "conn"
        assert params[1] == "sql"
        assert params[2] == "params"


class TestExecuteOne:
    def test_executes_and_commits(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        execute_one(mock_conn, "INSERT INTO foo VALUES (%s)", (1,))

        mock_cur.execute.assert_called_once_with("INSERT INTO foo VALUES (%s)", (1,))
        mock_conn.commit.assert_called_once()

    def test_accepts_none_params(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        execute_one(mock_conn, "DELETE FROM foo")

        mock_cur.execute.assert_called_once_with("DELETE FROM foo", None)


class TestExecuteMany:
    def test_executemany_called_and_committed(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        rows = [(1, "a"), (2, "b")]
        execute_many(mock_conn, "INSERT INTO foo VALUES (%s, %s)", rows)

        mock_cur.executemany.assert_called_once_with(
            "INSERT INTO foo VALUES (%s, %s)", rows
        )
        mock_conn.commit.assert_called_once()


class TestFetchAll:
    def test_returns_list(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg.rows.dict_row", MagicMock()):
            result = fetch_all(mock_conn, "SELECT * FROM foo")

        assert isinstance(result, list)
