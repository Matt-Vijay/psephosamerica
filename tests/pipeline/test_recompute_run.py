"""Tests for src/pipeline/recompute_run.py.

No live DB — all fetch/query/write boundaries are mocked.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import LoadSummary, WarnErrorSummary, build_load_summary
from src.db.recompute_resolvers import RecomputeResolverMaps
from src.normalize.taxonomy_runtime import CommitteeMapping
from src.pipeline.conflict_recompute import RecomputeResult
from src.pipeline.recompute_run import (
    RecomputeRunResult,
    _build_committee_sector_resolver,
    _build_recompute_resolvers,
    _delta_rows_by_member_id,
    run_recompute,
)
from src.rules.models import RuleFire, Severity

# ---------------------------------------------------------------------------
# Constants / shared fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_RUN_ID = 42

_MEMBER = {
    "id": 101,
    "bioguide_id": "A000001",
    "slug": "rep-a",
    "full_name": "Rep A",
    "first_name": "Rep",
    "last_name": "A",
    "party": "D",
    "state": "CA",
    "chamber": "house",
    "current_term_start": dt.date(2023, 1, 3),
    "current_term_end": None,
}


def _empty_load_summary() -> LoadSummary:
    return build_load_summary([], warn_error=WarnErrorSummary(), run_id=_RUN_ID)


def _empty_recompute_result() -> RecomputeResult:
    return RecomputeResult(rule_fires=[], evidence_cards=[], by_member={})


def _rule_fire() -> RuleFire:
    return RuleFire(
        fire_id="fire-abc-001",
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        rule_version=1,
        member_bioguide_id="A000001",
        dimension="conflict_of_interest_risk",
        severity=Severity.high,
        source_types_required=["committee_membership", "financial_disclosure"],
        sourced_facts={"trade_date": "2024-06-01"},
        derived_values={},
        parameters_used={},
        recompute_run_id=str(_RUN_ID),
        fired_at=dt.datetime(2024, 6, 1, 12, 0, tzinfo=dt.UTC),
        explanation="Trade overlaps committee service.",
    )


def _evidence_card() -> MagicMock:
    card = MagicMock()
    card.member_bioguide_id = "A000001"
    card.dimension = "conflict_of_interest_risk"
    card.score_delta = 2.0
    return card


def _make_taxonomy(sector_id: str | None = None) -> Any:
    taxonomy = MagicMock()
    if sector_id is None:
        taxonomy.committee_sector.return_value = None
    else:
        mapping = MagicMock()
        mapping.sector_id = sector_id
        taxonomy.committee_sector.return_value = mapping
    return taxonomy


def _null_issuer_resolver(issuer_name: str, issuer_ticker: str | None) -> str | None:
    return None


def _energy_issuer_resolver(issuer_name: str, issuer_ticker: str | None) -> str | None:
    return "energy" if "Exxon" in issuer_name else None


def _energy_contribution_resolver(row: dict[str, Any]) -> str | None:
    return "energy" if "SOLAR" in str(row.get("donor_name", "")) else None


def _committee_mapping(
    *,
    sector_id: str = "energy",
    mapping_tier: str = "deterministic",
) -> CommitteeMapping:
    return CommitteeMapping(
        congress=119,
        chamber="House",
        committee_name="Committee on Energy",
        subcommittee_name="",
        sector_id=sector_id,
        mapping_tier=mapping_tier,
        jurisdiction_basis="Test basis",
        basis_source="test",
        notes="",
    )


_MEMBERSHIP_ROW = {
    "committee_membership_id": 10,
    "member_bioguide_id": "A000001",
    "committee_code": "HSEN",
    "committee_name": "Committee on Energy",
    "congress": 119,
    "committee_start_date": dt.date(2024, 1, 1),
    "committee_end_date": None,
    "committee_membership_source_url": "https://api.congress.gov/v3/committee/house/HSEN?format=json",
}

_HOLDING_ROW = {
    "holding_id": 20,
    "financial_disclosure_id": 100,
    "member_bioguide_id": "A000001",
    "disclosure_period_start": dt.date(2024, 1, 1),
    "disclosure_period_end": dt.date(2024, 12, 31),
    "issuer_name": "Exxon Corp",
    "issuer_ticker": "XOM",
    "holding_value_min": 15_001.0,
    "holding_value_max": 50_000.0,
    "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-100.pdf",
}

_TRANSACTION_ROW = {
    "transaction_id": 30,
    "financial_disclosure_id": 100,
    "member_bioguide_id": "A000001",
    "transaction_date": dt.date(2024, 6, 1),
    "issuer_name": "Exxon Corp",
    "issuer_ticker": "XOM",
    "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-100.pdf",
}

_CONTRIBUTION_ROW = {
    "contribution_id": 90,
    "source_record_id": "4073020241987654321",
    "source_url": "https://www.fec.gov/data/receipts/individual-contributions/",
    "member_bioguide_id": "A000001",
    "member_name": "Rep A",
    "fec_candidate_id": "H4CA00001",
    "recipient_fec_committee_id": "C00431445",
    "fec_committee_name": "Rep A for Congress",
    "donor_name": "SOLAR BUILDERS PAC",
    "donor_type": "committee",
    "contribution_type": "contribution",
    "contribution_date": dt.date(2024, 5, 1),
    "amount": 2500.0,
    "memo": None,
}

_STATEMENT_ROW = {
    "member_bioguide_id": "A000001",
    "member_name": "Rep A",
    "statement_id": "stmt-001",
    "source_record_id": "rep-a-energy-2024-05-02",
    "statement_date": dt.date(2024, 5, 2),
    "statement_title": "Rep A Statement on Energy",
    "statement_excerpt": "Energy permitting reform matters.",
    "sector": "energy",
    "alignment_score": 0.65,
    "statement_source_url": "https://a.house.gov/news/press-releases/energy",
}


# ---------------------------------------------------------------------------
# Context-manager helper to patch all six DB boundaries in run_recompute
# ---------------------------------------------------------------------------

_MODULE = "src.pipeline.recompute_run"


def _all_fetch_patches(
    members: list[dict] | None = None,
    snapshot_rows: list[dict] | None = None,
    late_rows: list[dict] | None = None,
    membership_rows: list[dict] | None = None,
    holding_rows: list[dict] | None = None,
    transaction_rows: list[dict] | None = None,
    contribution_rows: list[dict] | None = None,
    recompute_result: RecomputeResult | None = None,
    load_summary: LoadSummary | None = None,
    bioguide_map: dict | None = None,
) -> list:
    bundle = MagicMock()
    bundle.bioguide_map = bioguide_map if bioguide_map is not None else {"A000001": 101}
    recompute_maps = RecomputeResolverMaps(
        member_by_bioguide=bundle.bioguide_map,
        rule_fire_by_source_record={},
    )
    return [
        patch(
            f"{_MODULE}.fetch_recompute_members",
            return_value=members if members is not None else [_MEMBER],
        ),
        patch(f"{_MODULE}.fetch_previous_score_snapshot_rows", return_value=snapshot_rows or []),
        patch(f"{_MODULE}.fetch_late_or_amended_rows", return_value=late_rows or []),
        patch(f"{_MODULE}.fetch_committee_membership_rows", return_value=membership_rows or []),
        patch(f"{_MODULE}.fetch_holding_rows", return_value=holding_rows or []),
        patch(f"{_MODULE}.fetch_transaction_rows", return_value=transaction_rows or []),
        patch(f"{_MODULE}.fetch_contribution_rows", return_value=contribution_rows or []),
        patch(
            f"{_MODULE}.recompute_conflicts",
            return_value=recompute_result or _empty_recompute_result(),
        ),
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
        patch(f"{_MODULE}.load_recompute_resolver_maps", return_value=recompute_maps),
        patch(f"{_MODULE}.execute_load_plan", return_value=load_summary or _empty_load_summary()),
    ]


def _run_with_patches(conn, taxonomy=None, issuer_resolver=None, **patch_kwargs):
    """Run run_recompute with all boundaries patched; return (result, mocks_dict)."""
    taxonomy = taxonomy or _make_taxonomy()
    issuer_resolver = issuer_resolver or _null_issuer_resolver
    patches = _all_fetch_patches(**patch_kwargs)
    mocks = {}
    with contextlib.ExitStack() as stack:
        for p in patches:
            m = stack.enter_context(p)
            mocks[p.attribute] = m
        result = run_recompute(
            conn,
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            taxonomy=taxonomy,
            issuer_sector_resolver=issuer_resolver,
        )
    return result, mocks


# ---------------------------------------------------------------------------
# _build_committee_sector_resolver — pure helper tests
# ---------------------------------------------------------------------------


class TestBuildCommitteeSectorResolver:
    def test_empty_membership_rows_returns_none_for_any_code(self) -> None:
        taxonomy = _make_taxonomy()
        resolve = _build_committee_sector_resolver([], taxonomy)
        assert resolve("HFSC", 119) is None

    def test_known_committee_resolves_to_sector(self) -> None:
        rows = [
            {
                "committee_code": "HFSC",
                "congress": 119,
                "committee_name": "House Financial Services Committee",
            }
        ]
        taxonomy = _make_taxonomy(sector_id="finance")
        resolve = _build_committee_sector_resolver(rows, taxonomy)
        assert resolve("HFSC", 119).sector_id == "finance"

    def test_unknown_committee_returns_none(self) -> None:
        rows = [
            {
                "committee_code": "HFSC",
                "congress": 119,
                "committee_name": "House Financial Services Committee",
            }
        ]
        taxonomy = _make_taxonomy(sector_id="finance")
        resolve = _build_committee_sector_resolver(rows, taxonomy)
        assert resolve("UNKN", 119) is None

    def test_taxonomy_called_once_per_unique_code_congress(self) -> None:
        rows = [
            {"committee_code": "HFSC", "congress": 119, "committee_name": "Committee A"},
            {"committee_code": "HFSC", "congress": 119, "committee_name": "Committee A"},
        ]
        taxonomy = _make_taxonomy()
        _build_committee_sector_resolver(rows, taxonomy)
        assert taxonomy.committee_sector.call_count == 1

    def test_different_congress_resolved_separately(self) -> None:
        rows = [
            {"committee_code": "HFSC", "congress": 118, "committee_name": "Committee A"},
            {"committee_code": "HFSC", "congress": 119, "committee_name": "Committee A"},
        ]
        taxonomy = _make_taxonomy()
        _build_committee_sector_resolver(rows, taxonomy)
        assert taxonomy.committee_sector.call_count == 2

    def test_subcommittee_resolution_uses_parent_name_and_chamber(self) -> None:
        rows = [
            {
                "committee_code": "HSIF15",
                "congress": 119,
                "committee_name": "Subcommittee on Health",
                "committee_chamber": "House",
                "committee_type": "subcommittee",
                "parent_committee_name": "Committee on Energy and Commerce",
            }
        ]
        mapping = MagicMock()
        taxonomy = MagicMock()
        taxonomy.committee_sector.return_value = mapping

        resolve = _build_committee_sector_resolver(rows, taxonomy)

        assert resolve("HSIF15", 119) is mapping
        taxonomy.committee_sector.assert_called_once_with(
            "Committee on Energy and Commerce",
            "Subcommittee on Health",
            congress=119,
            chamber="House",
        )


# ---------------------------------------------------------------------------
# _build_recompute_resolvers — pure helper tests
# ---------------------------------------------------------------------------


class TestBuildRecomputeResolvers:
    def test_subject_member_bioguide_id_resolves_to_subject_member_id(self) -> None:
        resolvers = _build_recompute_resolvers({"A000001": 101})
        target_col, fn = resolvers["_subject_member_bioguide_id"]
        assert target_col == "subject_member_id"
        assert fn({"_subject_member_bioguide_id": "A000001"}) == 101

    def test_missing_subject_member_bioguide_returns_none(self) -> None:
        resolvers = _build_recompute_resolvers({"A000001": 101})
        _, fn = resolvers["_subject_member_bioguide_id"]
        assert fn({"_subject_member_bioguide_id": "Z999999"}) is None

    def test_member_bioguide_id_resolves_to_member_id(self) -> None:
        resolvers = _build_recompute_resolvers({})
        col, fn = resolvers["_member_bioguide_id"]
        assert col == "member_id"
        assert fn({"_member_bioguide_id": "Z999999"}) is None

    def test_rule_fire_source_record_id_resolves_to_rule_fire_id(self) -> None:
        resolvers = _build_recompute_resolvers({}, rule_fire_map={"fire-abc-001": 701})
        col, fn = resolvers["_rule_fire_source_record_id"]
        assert col == "rule_fire_id"
        assert fn({"_rule_fire_source_record_id": "fire-abc-001"}) == 701


# ---------------------------------------------------------------------------
# _delta_rows_by_member_id — pure helper tests
# ---------------------------------------------------------------------------


class TestDeltaRowsByMemberId:
    def _card(self, bioguide: str, dimension: str, delta: float) -> Any:
        card = MagicMock()
        card.member_bioguide_id = bioguide
        card.dimension = dimension
        card.score_delta = delta
        return card

    def test_empty_cards_returns_empty_dict(self) -> None:
        assert _delta_rows_by_member_id([], {}) == {}

    def test_card_not_in_map_is_skipped(self) -> None:
        card = self._card("Z999", "conflict_of_interest_risk", 2.0)
        result = _delta_rows_by_member_id([card], {"A000001": 101})
        assert result == {}

    def test_card_grouped_by_member_id(self) -> None:
        card = self._card("A000001", "conflict_of_interest_risk", 2.0)
        result = _delta_rows_by_member_id([card], {"A000001": 101})
        assert 101 in result
        assert len(result[101]) == 1
        assert result[101][0]["score_delta"] == 2.0
        assert result[101][0]["dimension"] == "conflict_of_interest_risk"

    def test_multiple_cards_same_member_accumulate(self) -> None:
        cards = [
            self._card("A000001", "conflict_of_interest_risk", 2.0),
            self._card("A000001", "conflict_of_interest_risk", 4.0),
        ]
        result = _delta_rows_by_member_id(cards, {"A000001": 101})
        assert len(result[101]) == 2


# ---------------------------------------------------------------------------
# run_recompute — orchestration tests
# ---------------------------------------------------------------------------


class TestRunRecomputeEmptyMembers:
    def test_no_members_returns_empty_result_without_any_db_writes(self) -> None:
        conn = MagicMock()
        with patch(f"{_MODULE}.fetch_recompute_members", return_value=[]):
            result = run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        assert result.rule_fires == []
        assert result.evidence_cards == []
        assert result.load_summary is None

    def test_no_members_skips_all_downstream_queries(self) -> None:
        conn = MagicMock()
        with (
            patch(f"{_MODULE}.fetch_recompute_members", return_value=[]),
            patch(f"{_MODULE}.fetch_committee_membership_rows") as mock_memberships,
        ):
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        mock_memberships.assert_not_called()


class TestRunRecomputeResult:
    def test_result_is_recompute_run_result_instance(self) -> None:
        result, _ = _run_with_patches(MagicMock())
        assert isinstance(result, RecomputeRunResult)

    def test_rule_fires_and_evidence_cards_forwarded_from_conflict_result(self) -> None:
        fire = MagicMock()
        fire.source_types_required = ["financial_disclosure"]
        card = MagicMock()
        card.member_bioguide_id = "A000001"
        card.dimension = "conflict_of_interest_risk"
        card.score_delta = 2.0
        recompute_result = RecomputeResult(rule_fires=[fire], evidence_cards=[card], by_member={})
        result, _ = _run_with_patches(MagicMock(), recompute_result=recompute_result)
        assert result.rule_fires == [fire]
        assert result.evidence_cards == [card]

    def test_load_summary_returned_from_execute_load_plan(self) -> None:
        expected = _empty_load_summary()
        result, _ = _run_with_patches(MagicMock(), load_summary=expected)
        assert result.load_summary is expected


class TestRunRecomputeFetchBoundaries:
    def test_previous_snapshots_fetched_with_member_ids(self) -> None:
        conn = MagicMock()
        spy = MagicMock(return_value=[])
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.fetch_previous_score_snapshot_rows", spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        spy.assert_called_once()
        _, kwargs = spy.call_args
        assert kwargs["member_ids"] == [101]

    def test_conflict_inputs_fetched_with_bioguide_ids(self) -> None:
        conn = MagicMock()
        late_spy = MagicMock(return_value=[])
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.fetch_late_or_amended_rows", late_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        late_spy.assert_called_once()
        _, kwargs = late_spy.call_args
        assert "A000001" in kwargs["bioguide_ids"]
        assert kwargs["filing_year"] == _SNAPSHOT_DATE.year

    def test_filing_year_derived_from_snapshot_date(self) -> None:
        conn = MagicMock()
        holding_spy = MagicMock(return_value=[])
        snap = dt.date(2025, 3, 15)
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.fetch_holding_rows", holding_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=snap,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        _, kwargs = holding_spy.call_args
        assert kwargs["filing_year"] == 2025


class TestRunRecomputeConflictWiring:
    def test_recompute_conflicts_called_with_snapshot_date(self) -> None:
        conn = MagicMock()
        conflict_spy = MagicMock(return_value=_empty_recompute_result())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.recompute_conflicts", conflict_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        conflict_spy.assert_called_once()
        _, kwargs = conflict_spy.call_args
        assert kwargs["snapshot_date"] == _SNAPSHOT_DATE

    def test_recompute_run_id_passed_as_string_to_conflict_engine(self) -> None:
        conn = MagicMock()
        conflict_spy = MagicMock(return_value=_empty_recompute_result())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.recompute_conflicts", conflict_spy))
            run_recompute(
                conn,
                recompute_run_id=99,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        _, kwargs = conflict_spy.call_args
        assert kwargs["recompute_run_id"] == "99"

    def test_four_families_passed_to_conflict_engine(self) -> None:
        conn = MagicMock()
        conflict_spy = MagicMock(return_value=_empty_recompute_result())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.recompute_conflicts", conflict_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        _, kwargs = conflict_spy.call_args
        families = set(kwargs["rows_by_family"].keys())
        assert families == {
            "late_or_amended_disclosure",
            "committee_sector_trade",
            "repeated_committee_linked_trading",
            "sector_holdings_overlap",
        }

    def test_deterministic_rows_reach_conflict_engine(self) -> None:
        conn = MagicMock()
        taxonomy = MagicMock()
        taxonomy.committee_sector.return_value = _committee_mapping()
        conflict_spy = MagicMock(return_value=_empty_recompute_result())
        patches = _all_fetch_patches(
            membership_rows=[_MEMBERSHIP_ROW],
            holding_rows=[_HOLDING_ROW],
            transaction_rows=[_TRANSACTION_ROW],
        )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.recompute_conflicts", conflict_spy))
            result = run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=taxonomy,
                issuer_sector_resolver=_energy_issuer_resolver,
            )

        rows_by_family = conflict_spy.call_args.kwargs["rows_by_family"]
        assert len(rows_by_family["committee_sector_trade"]) == 1
        assert len(rows_by_family["repeated_committee_linked_trading"]) == 1
        assert len(rows_by_family["sector_holdings_overlap"]) == 1
        assert result.review_required_rows_by_family == {}
        assert result.unresolved_committee_matches == []
        assert [edge.edge_type for edge in result.ontology_edges] == [
            "member_committee_assignment",
            "committee_sector_jurisdiction",
            "member_sector_holding_exposure",
            "member_sector_transaction_exposure",
        ]
        assert result.ontology_edges[0].subject.node_id == "A000001"
        assert result.ontology_edges[0].object.node_id == "HSEN"
        assert result.ontology_edges[2].object.node_id == "energy"
        assert result.ontology_edges[2].source_anchors[0].source_type == "financial_disclosure"

    def test_contribution_rows_reach_ontology_edges_when_sector_resolves(self) -> None:
        conn = MagicMock()
        taxonomy = MagicMock()
        taxonomy.committee_sector.return_value = _committee_mapping()
        patches = _all_fetch_patches(
            membership_rows=[],
            holding_rows=[],
            transaction_rows=[],
            contribution_rows=[_CONTRIBUTION_ROW],
        )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=taxonomy,
                issuer_sector_resolver=_energy_issuer_resolver,
                contribution_sector_resolver=_energy_contribution_resolver,
            )

        assert [edge.edge_type for edge in result.ontology_edges] == [
            "member_sector_contribution_exposure"
        ]
        edge = result.ontology_edges[0]
        assert edge.subject.node_id == "A000001"
        assert edge.object.node_id == "energy"
        assert edge.attributes["contribution_date"] == "2024-05-01"
        assert edge.source_anchors[0].source_type == "fec_contribution"

    def test_prepared_statement_rows_reach_ontology_edges(self) -> None:
        conn = MagicMock()
        taxonomy = MagicMock()
        taxonomy.committee_sector.return_value = _committee_mapping()
        patches = _all_fetch_patches(
            membership_rows=[],
            holding_rows=[],
            transaction_rows=[],
        )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=taxonomy,
                issuer_sector_resolver=_energy_issuer_resolver,
                statement_rows=[_STATEMENT_ROW],
            )

        assert [edge.edge_type for edge in result.ontology_edges] == [
            "member_sector_public_statement_alignment"
        ]
        edge = result.ontology_edges[0]
        assert edge.subject.node_id == "A000001"
        assert edge.object.node_id == "energy"
        assert edge.attributes["statement_date"] == "2024-05-02"
        assert edge.attributes["alignment_score"] == 0.65
        assert edge.source_anchors[0].source_type == "public_statement"

    def test_review_required_rows_are_traced_but_not_scored(self) -> None:
        conn = MagicMock()
        taxonomy = MagicMock()
        taxonomy.committee_sector.return_value = _committee_mapping(mapping_tier="review_required")
        conflict_spy = MagicMock(return_value=_empty_recompute_result())
        patches = _all_fetch_patches(
            membership_rows=[_MEMBERSHIP_ROW],
            holding_rows=[_HOLDING_ROW],
            transaction_rows=[_TRANSACTION_ROW],
        )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.recompute_conflicts", conflict_spy))
            result = run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=taxonomy,
                issuer_sector_resolver=_energy_issuer_resolver,
            )

        rows_by_family = conflict_spy.call_args.kwargs["rows_by_family"]
        assert rows_by_family["committee_sector_trade"] == []
        assert rows_by_family["repeated_committee_linked_trading"] == []
        assert rows_by_family["sector_holdings_overlap"] == []
        assert len(result.review_required_rows_by_family["committee_sector_trade"]) == 1
        assert len(result.review_required_rows_by_family["repeated_committee_linked_trading"]) == 1
        assert len(result.review_required_rows_by_family["sector_holdings_overlap"]) == 1
        assert (
            result.review_required_rows_by_family["committee_sector_trade"][0][
                "committee_mapping_tier"
            ]
            == "review_required"
        )
        assert len(result.unresolved_committee_matches) == 3

        matches_by_family = {match.family: match for match in result.unresolved_committee_matches}
        assert matches_by_family["committee_sector_trade"].status == "unresolved_review_required"
        assert matches_by_family["committee_sector_trade"].scored is False
        assert matches_by_family["committee_sector_trade"].deterministic is False
        assert matches_by_family["committee_sector_trade"].financial_disclosure_id == 100
        assert (
            matches_by_family["committee_sector_trade"].committee_mapping_tier == "review_required"
        )
        assert matches_by_family["repeated_committee_linked_trading"].signal_count == 1
        assert matches_by_family["sector_holdings_overlap"].signal_count == 1


class TestRunRecomputePersistPhase:
    def test_execute_load_plan_called_with_run_id(self) -> None:
        conn = MagicMock()
        exec_spy = MagicMock(return_value=_empty_load_summary())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.execute_load_plan", exec_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        exec_spy.assert_called_once()
        _, kwargs = exec_spy.call_args
        assert kwargs["run_id"] == _RUN_ID

    def test_no_fire_write_defers_commit_to_outer_transaction(self) -> None:
        conn = MagicMock()
        exec_spy = MagicMock(return_value=_empty_load_summary())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.execute_load_plan", exec_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )

        exec_spy.assert_called_once()
        _, kwargs = exec_spy.call_args
        assert kwargs["commit"] is False
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_commit_true_rejects_autocommit_connection_before_fetches(self) -> None:
        conn = MagicMock()
        conn.autocommit = True

        with patch(f"{_MODULE}.fetch_recompute_members") as fetch_members:
            with pytest.raises(RuntimeError, match="autocommit"):
                run_recompute(
                    conn,
                    recompute_run_id=_RUN_ID,
                    snapshot_date=_SNAPSHOT_DATE,
                    taxonomy=_make_taxonomy(),
                    issuer_sector_resolver=_null_issuer_resolver,
                )

        fetch_members.assert_not_called()
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_final_commit_failure_rolls_back_outer_transaction(self) -> None:
        conn = MagicMock()
        conn.commit.side_effect = RuntimeError("commit failed")
        recompute_result = RecomputeResult(rule_fires=[], evidence_cards=[], by_member={})
        patches = _all_fetch_patches(recompute_result=recompute_result)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(f"{_MODULE}.execute_load_plan", return_value=_empty_load_summary())
            )
            with pytest.raises(RuntimeError, match="commit failed"):
                run_recompute(
                    conn,
                    recompute_run_id=_RUN_ID,
                    snapshot_date=_SNAPSHOT_DATE,
                    taxonomy=_make_taxonomy(),
                    issuer_sector_resolver=_null_issuer_resolver,
                )

        conn.commit.assert_called_once()
        conn.rollback.assert_called_once()

    def test_commit_false_defers_final_commit(self) -> None:
        conn = MagicMock()
        exec_spy = MagicMock(return_value=_empty_load_summary())
        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.execute_load_plan", exec_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
                commit=False,
            )

        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_split_rule_fire_write_defers_commit_to_outer_transaction(self) -> None:
        conn = MagicMock()
        exec_spy = MagicMock(return_value=_empty_load_summary())
        recompute_result = RecomputeResult(
            rule_fires=[_rule_fire()],
            evidence_cards=[],
            by_member={},
        )
        patches = _all_fetch_patches(recompute_result=recompute_result)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.execute_load_plan", exec_spy))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )

        assert exec_spy.call_count == 2
        assert [call.kwargs["commit"] for call in exec_spy.call_args_list] == [False, False]
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_split_rule_fire_write_failure_rolls_back_outer_transaction(self) -> None:
        conn = MagicMock()
        recompute_result = RecomputeResult(
            rule_fires=[_rule_fire()],
            evidence_cards=[],
            by_member={},
        )
        patches = _all_fetch_patches(recompute_result=recompute_result)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    f"{_MODULE}.execute_load_plan",
                    side_effect=[_empty_load_summary(), RuntimeError("phase failed")],
                )
            )
            try:
                run_recompute(
                    conn,
                    recompute_run_id=_RUN_ID,
                    snapshot_date=_SNAPSHOT_DATE,
                    taxonomy=_make_taxonomy(),
                    issuer_sector_resolver=_null_issuer_resolver,
                )
            except RuntimeError as exc:
                assert str(exc) == "phase failed"
            else:
                raise AssertionError("run_recompute should have raised")

        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_split_rule_fire_write_failure_with_commit_false_defers_rollback_to_caller(
        self,
    ) -> None:
        conn = MagicMock()
        recompute_result = RecomputeResult(
            rule_fires=[_rule_fire()],
            evidence_cards=[],
            by_member={},
        )
        patches = _all_fetch_patches(recompute_result=recompute_result)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    f"{_MODULE}.execute_load_plan",
                    side_effect=[_empty_load_summary(), RuntimeError("phase failed")],
                )
            )
            with pytest.raises(RuntimeError, match="phase failed"):
                run_recompute(
                    conn,
                    recompute_run_id=_RUN_ID,
                    snapshot_date=_SNAPSHOT_DATE,
                    taxonomy=_make_taxonomy(),
                    issuer_sector_resolver=_null_issuer_resolver,
                    commit=False,
                )

        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_split_rule_fire_resolver_refresh_failure_rolls_back_outer_transaction(self) -> None:
        conn = MagicMock()
        recompute_result = RecomputeResult(
            rule_fires=[_rule_fire()],
            evidence_cards=[_evidence_card()],
            by_member={},
        )
        patches = _all_fetch_patches(recompute_result=recompute_result)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            exec_spy = stack.enter_context(
                patch(f"{_MODULE}.execute_load_plan", return_value=_empty_load_summary())
            )
            stack.enter_context(
                patch(
                    f"{_MODULE}.load_recompute_resolver_maps",
                    side_effect=RuntimeError("resolver refresh failed"),
                )
            )
            with pytest.raises(RuntimeError, match="resolver refresh failed"):
                run_recompute(
                    conn,
                    recompute_run_id=_RUN_ID,
                    snapshot_date=_SNAPSHOT_DATE,
                    taxonomy=_make_taxonomy(),
                    issuer_sector_resolver=_null_issuer_resolver,
                )

        exec_spy.assert_called_once()
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_load_lookup_bundle_called_before_execute(self) -> None:
        conn = MagicMock()
        call_order: list[str] = []

        def tracking_lookup(c):
            call_order.append("lookup")
            b = MagicMock()
            b.bioguide_map = {}
            return b

        def tracking_exec(c, ops, *, resolvers, run_id, commit):
            call_order.append("execute")
            return _empty_load_summary()

        patches = _all_fetch_patches()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(f"{_MODULE}.load_lookup_bundle", tracking_lookup))
            stack.enter_context(patch(f"{_MODULE}.execute_load_plan", tracking_exec))
            run_recompute(
                conn,
                recompute_run_id=_RUN_ID,
                snapshot_date=_SNAPSHOT_DATE,
                taxonomy=_make_taxonomy(),
                issuer_sector_resolver=_null_issuer_resolver,
            )
        assert call_order == ["lookup", "execute"]
