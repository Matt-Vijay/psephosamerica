"""Tests for src/db/sql.py — pure SQL generation, no DB I/O."""

import pytest

from src.db.sql import build_insert, build_upsert, derive_update_columns

# ---------------------------------------------------------------------------
# build_insert
# ---------------------------------------------------------------------------


class TestBuildInsert:
    def test_single_column(self):
        sql, params = build_insert("member", {"bioguide_id": "A000001"})
        assert sql == 'INSERT INTO "member" ("bioguide_id") VALUES (%s)'
        assert params == ["A000001"]

    def test_multiple_columns_order_preserved(self):
        row = {"bioguide_id": "A000001", "last_name": "Smith", "chamber": "house"}
        sql, params = build_insert("member", row)
        assert sql == (
            'INSERT INTO "member" ("bioguide_id", "last_name", "chamber") VALUES (%s, %s, %s)'
        )
        assert params == ["A000001", "Smith", "house"]

    def test_none_value_included(self):
        row = {"slug": "john-doe", "middle_name": None}
        sql, params = build_insert("member", row)
        assert "middle_name" in sql
        assert params == ["john-doe", None]

    def test_empty_row_raises(self):
        with pytest.raises(ValueError, match="at least one column"):
            build_insert("member", {})

    def test_table_name_is_literal(self):
        sql, _ = build_insert("financial_disclosure", {"filing_year": 2024})
        assert sql.startswith('INSERT INTO "financial_disclosure"')

    def test_reserved_table_name_is_quoted(self):
        sql, _ = build_insert(
            "transaction",
            {"financial_disclosure_id": 1, "line_number": 1},
        )
        assert sql.startswith('INSERT INTO "transaction"')

    def test_invalid_identifier_rejected(self):
        with pytest.raises(ValueError, match="invalid SQL identifier"):
            build_insert("member; DROP TABLE member", {"bioguide_id": "A000001"})

    def test_placeholders_count_matches_columns(self):
        row = {f"col{i}": i for i in range(5)}
        sql, params = build_insert("some_table", row)
        assert sql.count("%s") == 5
        assert len(params) == 5


# ---------------------------------------------------------------------------
# derive_update_columns
# ---------------------------------------------------------------------------


class TestDeriveUpdateColumns:
    def test_excludes_listed_columns(self):
        row = {"id": 1, "bioguide_id": "A1", "last_name": "Doe"}
        result = derive_update_columns(row, ["id", "bioguide_id"])
        assert result == ["last_name"]

    def test_preserves_insertion_order(self):
        row = {"c": 3, "a": 1, "b": 2}
        result = derive_update_columns(row, ["a"])
        assert result == ["c", "b"]

    def test_exclude_all_returns_empty(self):
        row = {"id": 1, "slug": "x"}
        assert derive_update_columns(row, ["id", "slug"]) == []

    def test_empty_exclude(self):
        row = {"id": 1, "name": "Alice"}
        assert derive_update_columns(row, []) == ["id", "name"]

    def test_exclude_not_in_row_is_safe(self):
        row = {"id": 1, "name": "Alice"}
        result = derive_update_columns(row, ["nonexistent"])
        assert result == ["id", "name"]


# ---------------------------------------------------------------------------
# build_upsert
# ---------------------------------------------------------------------------


class TestBuildUpsert:
    def test_basic_upsert_structure(self):
        row = {"bioguide_id": "A000001", "last_name": "Smith", "chamber": "house"}
        sql, params = build_upsert("member", row, conflict_columns=["bioguide_id"])
        assert 'INSERT INTO "member"' in sql
        assert 'ON CONFLICT ("bioguide_id") DO UPDATE SET' in sql
        assert '"last_name" = %s' in sql
        assert '"chamber" = %s' in sql
        # bioguide_id should NOT appear in the SET clause
        set_part = sql.split("DO UPDATE SET")[1]
        assert "bioguide_id" not in set_part

    def test_params_insert_then_update(self):
        row = {"slug": "john-doe", "full_name": "John Doe", "last_name": "Doe"}
        sql, params = build_upsert("member", row, conflict_columns=["slug"])
        # First 3 params are insert values
        assert params[:3] == ["john-doe", "John Doe", "Doe"]
        # Next 2 are update values (full_name, last_name, no slug)
        assert params[3:] == ["John Doe", "Doe"]

    def test_multi_column_conflict(self):
        row = {
            "member_id": 1,
            "congress": 119,
            "chamber": "house",
            "start_date": "2025-01-03",
            "is_current": True,
        }
        sql, params = build_upsert(
            "member_term", row, conflict_columns=["member_id", "congress", "chamber", "start_date"]
        )
        assert 'ON CONFLICT ("member_id", "congress", "chamber", "start_date")' in sql
        assert '"is_current" = %s' in sql
        set_part = sql.split("DO UPDATE SET")[1]
        for c in ["member_id", "congress", "chamber", "start_date"]:
            assert c not in set_part

    def test_only_conflict_columns_uses_do_nothing(self):
        row = {"member_id": 1, "congress": 119}
        sql, params = build_upsert("member_term", row, conflict_columns=["member_id", "congress"])
        assert "DO NOTHING" in sql
        assert "DO UPDATE" not in sql
        assert params == [1, 119]

    def test_empty_row_raises(self):
        with pytest.raises(ValueError, match="at least one column"):
            build_upsert("member", {}, conflict_columns=["id"])

    def test_empty_conflict_columns_raises(self):
        with pytest.raises(ValueError, match="conflict_columns must contain"):
            build_upsert("member", {"id": 1}, conflict_columns=[])

    def test_conflict_column_not_in_row_raises(self):
        with pytest.raises(ValueError, match="not found in row"):
            build_upsert("member", {"name": "Alice"}, conflict_columns=["id"])

    def test_placeholder_count_consistency(self):
        row = {"a": 1, "b": 2, "c": 3}
        sql, params = build_upsert("t", row, conflict_columns=["a"])
        # insert: 3 placeholders; update SET: 2 placeholders → total %s = 5
        assert sql.count("%s") == 5
        assert len(params) == 5

    def test_deterministic_column_order(self):
        row = {"z": 26, "a": 1, "m": 13}
        sql1, params1 = build_upsert("t", row, conflict_columns=["z"])
        sql2, params2 = build_upsert("t", row, conflict_columns=["z"])
        assert sql1 == sql2
        assert params1 == params2
