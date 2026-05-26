"""Tests for src/db/recompute_resolvers.py.

All DB access is mocked via unittest.mock.patch on
src.db.recompute_resolvers.fetch_all — no live DB required.

Covers:
- RecomputeResolverMaps: default field values
- build_recompute_resolvers: hint keys, target columns, resolver callables
- build_recompute_resolvers: cache hit, cache miss (→ None)
- build_recompute_resolvers: output is structurally valid Resolvers dict
- load_recompute_resolver_maps: maps populated from fetched rows
- load_recompute_resolver_maps: empty tables → empty maps
- Round-trip: load maps → build resolvers → resolve rows
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.db.recompute_resolvers import (
    RecomputeResolverMaps,
    build_recompute_resolvers,
    load_recompute_resolver_maps,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONN = MagicMock()


def _maps(
    *,
    members: dict[str, int] | None = None,
    disclosures: dict[str, int] | None = None,
    rule_fires: dict[str, int] | None = None,
) -> RecomputeResolverMaps:
    return RecomputeResolverMaps(
        member_by_bioguide=members or {},
        disclosure_by_source_record=disclosures or {},
        rule_fire_by_source_record=rule_fires or {},
    )


def _side_effect(
    member_rows: list[dict[str, Any]],
    disclosure_rows: list[dict[str, Any]],
    rule_fire_rows: list[dict[str, Any]],
):
    """Dispatch mock fetch_all by SQL keyword."""

    def _se(conn, sql, params=None):
        sql_l = sql.lower()
        if "rule_fire" in sql_l:
            return rule_fire_rows
        if "financial_disclosure" in sql_l:
            return disclosure_rows
        if "member" in sql_l:
            return member_rows
        return []

    return _se


# ---------------------------------------------------------------------------
# RecomputeResolverMaps — defaults
# ---------------------------------------------------------------------------


class TestRecomputeResolverMapsDefaults:
    def test_default_maps_are_empty(self) -> None:
        m = RecomputeResolverMaps()
        assert m.member_by_bioguide == {}
        assert m.disclosure_by_source_record == {}
        assert m.rule_fire_by_source_record == {}


# ---------------------------------------------------------------------------
# build_recompute_resolvers — structure
# ---------------------------------------------------------------------------


class TestBuildRecomputeResolversStructure:
    def test_returns_exactly_three_hint_keys(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        assert set(resolvers.keys()) == {
            "_member_bioguide_id",
            "_financial_disclosure_source_record_id",
            "_rule_fire_source_record_id",
        }

    def test_member_resolver_target_column_is_member_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        target_col, _ = resolvers["_member_bioguide_id"]
        assert target_col == "member_id"

    def test_disclosure_resolver_target_column_is_financial_disclosure_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        target_col, _ = resolvers["_financial_disclosure_source_record_id"]
        assert target_col == "financial_disclosure_id"

    def test_rule_fire_resolver_target_column_is_rule_fire_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        target_col, _ = resolvers["_rule_fire_source_record_id"]
        assert target_col == "rule_fire_id"

    def test_all_resolver_fns_are_callable(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        for key, (col, fn) in resolvers.items():
            assert callable(fn), f"resolver for {key!r} is not callable"

    def test_resolver_values_are_two_tuples(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        for key, value in resolvers.items():
            assert isinstance(value, tuple) and len(value) == 2


# ---------------------------------------------------------------------------
# build_recompute_resolvers — member resolver behaviour
# ---------------------------------------------------------------------------


class TestMemberResolver:
    def test_hit_returns_integer_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps(members={"A000001": 42}))
        _, fn = resolvers["_member_bioguide_id"]
        assert fn({"_member_bioguide_id": "A000001"}) == 42

    def test_miss_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps(members={"A000001": 42}))
        _, fn = resolvers["_member_bioguide_id"]
        assert fn({"_member_bioguide_id": "UNKNOWN"}) is None

    def test_empty_map_always_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        _, fn = resolvers["_member_bioguide_id"]
        assert fn({"_member_bioguide_id": "A000001"}) is None

    def test_multiple_members_resolved_independently(self) -> None:
        resolvers = build_recompute_resolvers(_maps(members={"A000001": 1, "B000002": 2}))
        _, fn = resolvers["_member_bioguide_id"]
        assert fn({"_member_bioguide_id": "A000001"}) == 1
        assert fn({"_member_bioguide_id": "B000002"}) == 2


# ---------------------------------------------------------------------------
# build_recompute_resolvers — disclosure resolver behaviour
# ---------------------------------------------------------------------------


class TestDisclosureResolver:
    def test_hit_returns_integer_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps(disclosures={"src-fd-001": 99}))
        _, fn = resolvers["_financial_disclosure_source_record_id"]
        assert fn({"_financial_disclosure_source_record_id": "src-fd-001"}) == 99

    def test_miss_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps(disclosures={"src-fd-001": 99}))
        _, fn = resolvers["_financial_disclosure_source_record_id"]
        assert fn({"_financial_disclosure_source_record_id": "not-there"}) is None

    def test_empty_map_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        _, fn = resolvers["_financial_disclosure_source_record_id"]
        assert fn({"_financial_disclosure_source_record_id": "src-fd-001"}) is None


# ---------------------------------------------------------------------------
# build_recompute_resolvers — rule_fire resolver behaviour
# ---------------------------------------------------------------------------


class TestRuleFireResolver:
    def test_hit_returns_integer_id(self) -> None:
        resolvers = build_recompute_resolvers(_maps(rule_fires={"src-rf-001": 77}))
        _, fn = resolvers["_rule_fire_source_record_id"]
        assert fn({"_rule_fire_source_record_id": "src-rf-001"}) == 77

    def test_miss_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps(rule_fires={"src-rf-001": 77}))
        _, fn = resolvers["_rule_fire_source_record_id"]
        assert fn({"_rule_fire_source_record_id": "nope"}) is None

    def test_empty_map_returns_none(self) -> None:
        resolvers = build_recompute_resolvers(_maps())
        _, fn = resolvers["_rule_fire_source_record_id"]
        assert fn({"_rule_fire_source_record_id": "src-rf-001"}) is None


# ---------------------------------------------------------------------------
# build_recompute_resolvers — Resolvers type compatibility
# ---------------------------------------------------------------------------


class TestResolversCompatibility:
    def test_output_satisfies_resolvers_type_shape(self) -> None:
        """Each entry must be (str, callable) to satisfy Resolvers duck-typing."""
        resolvers = build_recompute_resolvers(
            _maps(members={"A000001": 1}, disclosures={"fd-1": 2}, rule_fires={"rf-1": 3})
        )
        for hint_key, (target_col, fn) in resolvers.items():
            assert isinstance(hint_key, str)
            assert isinstance(target_col, str)
            assert callable(fn)

    def test_resolver_fns_accept_full_row_dict(self) -> None:
        """Resolver fn receives the full raw row and must not raise on extra keys."""
        resolvers = build_recompute_resolvers(_maps(members={"A000001": 5}))
        _, fn = resolvers["_member_bioguide_id"]
        row = {
            "_member_bioguide_id": "A000001",
            "some_other_column": "irrelevant",
            "recompute_run_id": 100,
        }
        assert fn(row) == 5


# ---------------------------------------------------------------------------
# load_recompute_resolver_maps — behavioral checks
# ---------------------------------------------------------------------------


class TestLoadRecomputeResolverMaps:
    def test_member_rows_populate_member_by_bioguide(self) -> None:
        member_rows = [
            {"id": 1, "bioguide_id": "A000001"},
            {"id": 2, "bioguide_id": "B000002"},
        ]
        with patch(
            "src.db.recompute_resolvers.fetch_all",
            side_effect=_side_effect(member_rows, [], []),
        ):
            maps = load_recompute_resolver_maps(CONN)

        assert maps.member_by_bioguide == {"A000001": 1, "B000002": 2}

    def test_disclosure_rows_populate_disclosure_by_source_record(self) -> None:
        fd_rows = [
            {"id": 10, "source_record_id": "src-fd-001"},
            {"id": 11, "source_record_id": "src-fd-002"},
        ]
        with patch(
            "src.db.recompute_resolvers.fetch_all",
            side_effect=_side_effect([], fd_rows, []),
        ):
            maps = load_recompute_resolver_maps(CONN)

        assert maps.disclosure_by_source_record == {
            "src-fd-001": 10,
            "src-fd-002": 11,
        }

    def test_rule_fire_rows_populate_rule_fire_by_source_record(self) -> None:
        rf_rows = [
            {"id": 20, "source_record_id": "src-rf-001"},
        ]
        with patch(
            "src.db.recompute_resolvers.fetch_all",
            side_effect=_side_effect([], [], rf_rows),
        ):
            maps = load_recompute_resolver_maps(CONN)

        assert maps.rule_fire_by_source_record == {"src-rf-001": 20}

    def test_empty_tables_return_empty_maps(self) -> None:
        with patch("src.db.recompute_resolvers.fetch_all", return_value=[]):
            maps = load_recompute_resolver_maps(CONN)

        assert maps.member_by_bioguide == {}
        assert maps.disclosure_by_source_record == {}
        assert maps.rule_fire_by_source_record == {}

    def test_returned_maps_feed_resolvers_correctly(self) -> None:
        """Round-trip: load maps → build resolvers → resolve rows."""
        member_rows = [{"id": 5, "bioguide_id": "A000001"}]
        fd_rows = [{"id": 15, "source_record_id": "fd-src-1"}]
        rf_rows = [{"id": 25, "source_record_id": "rf-src-1"}]

        with patch(
            "src.db.recompute_resolvers.fetch_all",
            side_effect=_side_effect(member_rows, fd_rows, rf_rows),
        ):
            maps = load_recompute_resolver_maps(CONN)

        resolvers = build_recompute_resolvers(maps)

        _, member_fn = resolvers["_member_bioguide_id"]
        _, fd_fn = resolvers["_financial_disclosure_source_record_id"]
        _, rf_fn = resolvers["_rule_fire_source_record_id"]

        assert member_fn({"_member_bioguide_id": "A000001"}) == 5
        assert fd_fn({"_financial_disclosure_source_record_id": "fd-src-1"}) == 15
        assert rf_fn({"_rule_fire_source_record_id": "rf-src-1"}) == 25
