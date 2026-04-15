"""Tests for src/pipeline/congress_load_run.py.

No live DB.  execute_load_plan and load_lookup_bundle are mocked.
Tests verify phase ordering, lookup refresh calls, resolver injection,
and LoadSummary consolidation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.lookups import LookupBundle
from src.pipeline.congress_load_run import (
    CongressIngestInputs,
    _build_resolvers,
    _fetch_bill_map,
    _fetch_vote_event_map,
    _merge_warn_errors,
    run_congress_load,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _empty_inputs() -> CongressIngestInputs:
    return CongressIngestInputs(
        members=[],
        member_terms=[],
        committees=[],
        memberships=[],
        bills=[],
        primary_sponsors=[],
        cosponsors=[],
        vote_events=[],
        vote_casts=[],
    )


def _summary(table: str, inserted: int = 1) -> LoadSummary:
    return build_load_summary(
        [TableWriteResult(table=table, inserted=inserted)],
        warn_error=WarnErrorSummary(),
    )


def _bundle(
    bioguide_map: dict | None = None,
    lis_member_map: dict | None = None,
    committee_code_map: dict | None = None,
) -> LookupBundle:
    b = LookupBundle()
    if bioguide_map is not None:
        b.bioguide_map = bioguide_map
    if lis_member_map is not None:
        b.lis_member_map = lis_member_map
    if committee_code_map is not None:
        b.committee_code_map = committee_code_map
    return b


# ---------------------------------------------------------------------------
# _merge_warn_errors
# ---------------------------------------------------------------------------


class TestMergeWarnErrors:
    def test_empty(self):
        merged = _merge_warn_errors([])
        assert merged.warnings == []
        assert merged.errors == []

    def test_combines_warnings_and_errors(self):
        s1 = build_load_summary([], WarnErrorSummary())
        s1.warn_error.add_warning("w1")
        s1.warn_error.add_error("e1")

        s2 = build_load_summary([], WarnErrorSummary())
        s2.warn_error.add_warning("w2")

        merged = _merge_warn_errors([s1, s2])
        assert merged.warnings == ["w1", "w2"]
        assert merged.errors == ["e1"]

    def test_no_mutation_of_inputs(self):
        s = build_load_summary([], WarnErrorSummary())
        s.warn_error.add_warning("x")
        _merge_warn_errors([s])
        assert len(s.warn_error.warnings) == 1


# ---------------------------------------------------------------------------
# _build_resolvers
# ---------------------------------------------------------------------------


class TestBuildResolvers:
    def test_bioguide_resolves_member_id(self):
        bundle = _bundle(bioguide_map={"B001": 10})
        resolvers = _build_resolvers(bundle)
        target_col, fn = resolvers["_bioguide_id"]
        assert target_col == "member_id"
        assert fn({"_bioguide_id": "B001", "_lis_member_id": None}) == 10

    def test_bioguide_falls_back_to_lis_member(self):
        bundle = _bundle(bioguide_map={}, lis_member_map={"S00042": 20})
        resolvers = _build_resolvers(bundle)
        _, fn = resolvers["_bioguide_id"]
        assert fn({"_bioguide_id": None, "_lis_member_id": "S00042"}) == 20

    def test_bioguide_returns_none_when_both_absent(self):
        bundle = _bundle(bioguide_map={}, lis_member_map={})
        resolvers = _build_resolvers(bundle)
        _, fn = resolvers["_bioguide_id"]
        assert fn({"_bioguide_id": None, "_lis_member_id": None}) is None

    def test_committee_code_resolver(self):
        bundle = _bundle(committee_code_map={("HJUD", 119): 5})
        resolvers = _build_resolvers(bundle)
        target_col, fn = resolvers["_committee_code"]
        assert target_col == "committee_id"
        assert fn({"_committee_code": "HJUD", "congress": 119}) == 5

    def test_no_bill_resolver_by_default(self):
        bundle = _bundle()
        resolvers = _build_resolvers(bundle)
        assert "_bill_key" not in resolvers

    def test_bill_resolver_injected(self):
        bundle = _bundle()
        bill_map = {(119, "hr", 1): 99}
        resolvers = _build_resolvers(bundle, bill_map=bill_map)
        target_col, fn = resolvers["_bill_key"]
        assert target_col == "bill_id"
        assert fn({"_bill_key": (119, "hr", 1)}) == 99

    def test_vote_event_resolver_injected(self):
        bundle = _bundle()
        ve_map = {("house", 119, 1, 42): 77}
        resolvers = _build_resolvers(bundle, vote_event_map=ve_map)
        target_col, fn = resolvers["_vote_event_key"]
        assert target_col == "vote_event_id"
        assert fn({"_vote_event_key": ("house", 119, 1, 42)}) == 77

    def test_no_vote_event_resolver_by_default(self):
        bundle = _bundle()
        resolvers = _build_resolvers(bundle)
        assert "_vote_event_key" not in resolvers


# ---------------------------------------------------------------------------
# _fetch_bill_map and _fetch_vote_event_map
# ---------------------------------------------------------------------------


class TestFetchMaps:
    def test_fetch_bill_map(self):
        conn = MagicMock()
        with patch(
            "src.pipeline.congress_load_run.fetch_all",
            return_value=[
                {"id": 1, "congress": 119, "bill_type": "hr", "bill_number": 100},
                {"id": 2, "congress": 119, "bill_type": "s", "bill_number": 5},
            ],
        ):
            result = _fetch_bill_map(conn)
        assert result == {(119, "hr", 100): 1, (119, "s", 5): 2}

    def test_fetch_vote_event_map(self):
        conn = MagicMock()
        with patch(
            "src.pipeline.congress_load_run.fetch_all",
            return_value=[
                {
                    "id": 7,
                    "chamber": "house",
                    "congress": 119,
                    "session_number": 1,
                    "roll_call_number": 42,
                },
            ],
        ):
            result = _fetch_vote_event_map(conn)
        assert result == {("house", 119, 1, 42): 7}


# ---------------------------------------------------------------------------
# run_congress_load — phase ordering and load summary consolidation
# ---------------------------------------------------------------------------


MOCK_EXECUTE = "src.pipeline.congress_load_run.execute_load_plan"
MOCK_LOAD_BUNDLE = "src.pipeline.congress_load_run.load_lookup_bundle"
MOCK_FETCH_ALL = "src.pipeline.congress_load_run.fetch_all"


class TestRunCongressLoad:
    def _patched_run(
        self,
        call_results: list[LoadSummary],
        bundle: LookupBundle | None = None,
    ):
        """Run run_congress_load with mocked execute_load_plan and load_lookup_bundle."""
        conn = MagicMock()
        inputs = _empty_inputs()

        execute_calls: list[dict] = []
        bundle_calls: list[Any] = []

        call_iter = iter(call_results)

        def fake_execute(conn, ops, *, resolvers=None, run_id=None):
            execute_calls.append({"ops": ops, "resolvers": resolvers, "run_id": run_id})
            return next(call_iter)

        def fake_bundle(conn):
            bundle_calls.append(True)
            return bundle or _bundle()

        with (
            patch(MOCK_EXECUTE, side_effect=fake_execute),
            patch(MOCK_LOAD_BUNDLE, side_effect=fake_bundle),
            patch(MOCK_FETCH_ALL, return_value=[]),
        ):
            summary = run_congress_load(inputs, conn, run_id=42)

        return summary, execute_calls, bundle_calls

    def test_returns_load_summary(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        result, _, _ = self._patched_run(summaries)
        assert isinstance(result, LoadSummary)

    def test_four_execute_calls(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        assert len(calls) == 4

    def test_two_lookup_refreshes(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, _, bundle_calls = self._patched_run(summaries)
        assert len(bundle_calls) == 2

    def test_phase1_has_no_resolvers(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        assert calls[0]["resolvers"] is None

    def test_phase2_has_resolvers(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        assert calls[1]["resolvers"] is not None
        assert "_bioguide_id" in calls[1]["resolvers"]
        assert "_committee_code" in calls[1]["resolvers"]

    def test_phase3_has_no_resolvers(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        assert calls[2]["resolvers"] is None

    def test_phase4_has_bill_and_vote_event_resolvers(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        r = calls[3]["resolvers"]
        assert r is not None
        assert "_bill_key" in r
        assert "_vote_event_key" in r

    def test_run_id_forwarded_to_all_phases(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        for c in calls:
            assert c["run_id"] == 42

    def test_table_results_consolidated(self):
        s1 = build_load_summary([TableWriteResult("member", inserted=3)], WarnErrorSummary())
        s2 = build_load_summary([TableWriteResult("member_term", inserted=5)], WarnErrorSummary())
        s3 = build_load_summary([TableWriteResult("bill", inserted=7)], WarnErrorSummary())
        s4 = build_load_summary([TableWriteResult("bill_sponsor", inserted=2)], WarnErrorSummary())
        result, _, _ = self._patched_run([s1, s2, s3, s4])
        assert result.total_inserted == 17
        assert len(result.table_results) == 4

    def test_warn_errors_consolidated(self):
        we1 = WarnErrorSummary()
        we1.add_warning("warn-a")
        we2 = WarnErrorSummary()
        we2.add_error("err-b")
        s1 = build_load_summary([], we1)
        s2 = build_load_summary([], we2)
        s3 = build_load_summary([], WarnErrorSummary())
        s4 = build_load_summary([], WarnErrorSummary())
        result, _, _ = self._patched_run([s1, s2, s3, s4])
        assert result.warn_error.warnings == ["warn-a"]
        assert result.warn_error.errors == ["err-b"]

    def test_run_id_in_final_summary(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        result, _, _ = self._patched_run(summaries)
        assert result.run_id == 42

    def test_phase1_ops_contain_member_and_committee(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        tables_phase1 = [op["table"] for op in calls[0]["ops"]]
        assert "member" in tables_phase1
        assert "committee" in tables_phase1

    def test_phase2_ops_contain_member_term_and_membership(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        tables_phase2 = [op["table"] for op in calls[1]["ops"]]
        assert "member_term" in tables_phase2
        assert "committee_membership" in tables_phase2

    def test_phase3_ops_contain_bill_and_vote_event(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        tables_phase3 = [op["table"] for op in calls[2]["ops"]]
        assert "bill" in tables_phase3
        assert "vote_event" in tables_phase3

    def test_phase4_ops_contain_bill_sponsor_and_vote_cast(self):
        summaries = [_summary(t) for t in ("member", "member_term", "bill", "bill_sponsor")]
        _, calls, _ = self._patched_run(summaries)
        tables_phase4 = [op["table"] for op in calls[3]["ops"]]
        assert "bill_sponsor" in tables_phase4
        assert "vote_cast" in tables_phase4
