"""Tests for src/query/member_profile.py.

Pure unit tests — no DB, no network, no I/O.
All helpers and the public assembler are covered.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.export.contracts import MemberProfilePayload
from src.query.member_profile import (
    _count_distinct_evidence_cards,
    _extract_score_summaries,
    _latest_snapshot_date,
    _normalize_committees,
    _normalize_member,
    _normalize_recent_fires,
    _top_evidence_card_ids,
    assemble_member_profile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MEMBER_ROW: dict = {
    "bioguide_id": "A000001",
    "full_name": "Jane Smith",
    "slug": "jane-smith",
    "state": "CA",
    "district": 12,  # int from DB; normalizer coerces to str
    "chamber": "house",
    "party": "D",
}

SNAPSHOT_ROWS: list[dict] = [
    {
        "snapshot_at": dt.date(2024, 1, 1),
        "dimension_scores": {"conflict_of_interest_risk": 15.0},
    },
    {
        "snapshot_at": dt.date(2024, 6, 1),
        "dimension_scores": {"conflict_of_interest_risk": 25.0},
    },
]

FIRE_ROWS: list[dict] = [
    {
        "rule_id": "committee_sector_trade.v1",
        "dimension": "conflict_of_interest_risk",
        "evidence_card_id": "ec-001",
        "short_explanation": "Traded in committee sector.",
        "score_delta": 10.0,
        "snapshot_date": dt.date(2024, 3, 15),
    },
    {
        "rule_id": "late_or_amended_disclosure.v1",
        "dimension": "conflict_of_interest_risk",
        "evidence_card_id": "ec-002",
        "short_explanation": "Filed disclosure 36 days late.",
        "score_delta": 5.0,
        "snapshot_date": dt.date(2024, 5, 20),
    },
    {
        "rule_id": "sector_holdings_overlap.v1",
        "dimension": "conflict_of_interest_risk",
        "evidence_card_id": "ec-003",
        "short_explanation": "Holds stock in committee sector.",
        "score_delta": 8.0,
        "snapshot_date": dt.date(2024, 2, 10),
    },
]

COMMITTEE_ROWS: list[dict] = [
    {"committee_name": "Committee on Energy", "role": "member"},
    {"committee_name": "Committee on Finance", "role": "chair"},
]


# ---------------------------------------------------------------------------
# _normalize_member
# ---------------------------------------------------------------------------


class TestNormalizeMember:
    def test_maps_full_name_to_name(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["name"] == "Jane Smith"

    def test_bioguide_id_forwarded(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["bioguide_id"] == "A000001"

    def test_slug_forwarded(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["slug"] == "jane-smith"

    def test_state_forwarded(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["state"] == "CA"

    def test_district_coerced_to_str(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["district"] == "12"

    def test_district_none_when_absent(self):
        row = {k: v for k, v in MEMBER_ROW.items() if k != "district"}
        result = _normalize_member(row)
        assert result["district"] is None

    def test_chamber_forwarded(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["chamber"] == "house"

    def test_party_forwarded(self):
        result = _normalize_member(MEMBER_ROW)
        assert result["party"] == "D"


# ---------------------------------------------------------------------------
# _extract_score_summaries
# ---------------------------------------------------------------------------


class TestExtractScoreSummaries:
    def test_uses_most_recent_snapshot(self):
        result = _extract_score_summaries(SNAPSHOT_ROWS, [])
        assert len(result) == 1
        assert result[0]["current_score"] == 25.0  # June snapshot, not January

    def test_dimension_key_forwarded(self):
        result = _extract_score_summaries(SNAPSHOT_ROWS, [])
        assert result[0]["dimension"] == "conflict_of_interest_risk"

    def test_fire_count_aggregated_per_dimension(self):
        result = _extract_score_summaries(SNAPSHOT_ROWS, FIRE_ROWS)
        assert result[0]["rule_fire_count"] == 3

    def test_fire_count_zero_when_no_fires(self):
        result = _extract_score_summaries(SNAPSHOT_ROWS, [])
        assert result[0]["rule_fire_count"] == 0

    def test_empty_snapshots_returns_empty(self):
        assert _extract_score_summaries([], FIRE_ROWS) == []

    def test_multiple_dimensions_sorted_by_name(self):
        snapshot = [
            {
                "snapshot_at": dt.date(2024, 6, 1),
                "dimension_scores": {"zzz_dim": 5.0, "aaa_dim": 3.0},
            }
        ]
        result = _extract_score_summaries(snapshot, [])
        assert [r["dimension"] for r in result] == ["aaa_dim", "zzz_dim"]

    def test_fires_from_missing_dimension_do_not_appear(self):
        fires = [
            {
                "dimension": "other_dimension",
                "evidence_card_id": "ec-x",
                "rule_id": "r",
                "short_explanation": "",
                "score_delta": 1.0,
                "snapshot_date": dt.date(2024, 1, 1),
            }
        ]
        result = _extract_score_summaries(SNAPSHOT_ROWS, fires)
        # other_dimension is not in dimension_scores → not in output
        dims = {r["dimension"] for r in result}
        assert "other_dimension" not in dims

    def test_null_dimension_scores_returns_empty(self):
        snapshot = [{"snapshot_at": dt.date(2024, 1, 1), "dimension_scores": None}]
        assert _extract_score_summaries(snapshot, []) == []

    def test_score_is_float(self):
        result = _extract_score_summaries(SNAPSHOT_ROWS, [])
        assert isinstance(result[0]["current_score"], float)


# ---------------------------------------------------------------------------
# _normalize_recent_fires
# ---------------------------------------------------------------------------


class TestNormalizeRecentFires:
    def test_sorted_most_recent_first(self):
        result = _normalize_recent_fires(FIRE_ROWS)
        dates = [r["snapshot_date"] for r in result]
        assert dates == sorted(dates, reverse=True)

    def test_all_required_keys_present(self):
        result = _normalize_recent_fires(FIRE_ROWS)
        for r in result:
            assert "rule_id" in r
            assert "evidence_card_id" in r
            assert "short_explanation" in r
            assert "score_delta" in r
            assert "snapshot_date" in r

    def test_score_delta_is_float(self):
        result = _normalize_recent_fires(FIRE_ROWS)
        for r in result:
            assert isinstance(r["score_delta"], float)

    def test_default_limit_five(self):
        many = FIRE_ROWS * 4  # 12 rows
        result = _normalize_recent_fires(many)
        assert len(result) == 5

    def test_custom_limit_respected(self):
        result = _normalize_recent_fires(FIRE_ROWS, limit=2)
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert _normalize_recent_fires([]) == []

    def test_ties_broken_by_rule_id_ascending(self):
        same_date = dt.date(2024, 4, 1)
        fires = [
            {
                "rule_id": "zzz.v1",
                "evidence_card_id": "ec-z",
                "short_explanation": "z",
                "score_delta": 1.0,
                "snapshot_date": same_date,
            },
            {
                "rule_id": "aaa.v1",
                "evidence_card_id": "ec-a",
                "short_explanation": "a",
                "score_delta": 2.0,
                "snapshot_date": same_date,
            },
        ]
        result = _normalize_recent_fires(fires)
        assert result[0]["rule_id"] == "aaa.v1"
        assert result[1]["rule_id"] == "zzz.v1"

    def test_none_snapshot_date_sorts_last(self):
        fires = [
            *FIRE_ROWS,
            {
                "rule_id": "r.v1",
                "evidence_card_id": "ec-none",
                "short_explanation": "x",
                "score_delta": 1.0,
                "snapshot_date": None,
            },
        ]
        result = _normalize_recent_fires(fires, limit=10)
        assert result[-1]["evidence_card_id"] == "ec-none"


# ---------------------------------------------------------------------------
# _top_evidence_card_ids
# ---------------------------------------------------------------------------


class TestTopEvidenceCardIds:
    def test_returns_three_most_recent_distinct_ids(self):
        rows = FIRE_ROWS + [
            {
                "rule_id": "later_rule.v1",
                "dimension": "conflict_of_interest_risk",
                "evidence_card_id": "ec-004",
                "short_explanation": "Latest card.",
                "score_delta": 2.0,
                "snapshot_date": dt.date(2024, 6, 1),
            }
        ]
        assert _top_evidence_card_ids(rows) == ["ec-004", "ec-002", "ec-001"]

    def test_deduplicates_card_ids(self):
        rows = FIRE_ROWS + [
            {
                "rule_id": "duplicate_rule.v1",
                "dimension": "conflict_of_interest_risk",
                "evidence_card_id": "ec-002",
                "short_explanation": "Duplicate card.",
                "score_delta": 1.0,
                "snapshot_date": dt.date(2024, 5, 20),
            }
        ]
        assert _top_evidence_card_ids(rows) == ["ec-002", "ec-001", "ec-003"]

    def test_ignores_missing_card_ids(self):
        rows = FIRE_ROWS + [
            {
                "rule_id": "missing_card.v1",
                "dimension": "conflict_of_interest_risk",
                "evidence_card_id": None,
                "short_explanation": "No card id.",
                "score_delta": 1.0,
                "snapshot_date": dt.date(2024, 7, 1),
            }
        ]
        with pytest.raises(ValueError, match="evidence_card_id"):
            _top_evidence_card_ids(rows)


# ---------------------------------------------------------------------------
# _normalize_committees
# ---------------------------------------------------------------------------


class TestNormalizeCommittees:
    def test_sorted_by_name(self):
        result = _normalize_committees(COMMITTEE_ROWS)
        names = [r["committee_name"] for r in result]
        assert names == sorted(names)

    def test_deduplication(self):
        duplicate = COMMITTEE_ROWS + [{"committee_name": "Committee on Energy", "role": "member"}]
        result = _normalize_committees(duplicate)
        energy = [r for r in result if r["committee_name"] == "Committee on Energy"]
        assert len(energy) == 1

    def test_none_role_preserved(self):
        rows = [{"committee_name": "Budget Committee", "role": None}]
        result = _normalize_committees(rows)
        assert result[0]["role"] is None

    def test_missing_role_key_treated_as_none(self):
        rows = [{"committee_name": "Budget Committee"}]
        result = _normalize_committees(rows)
        assert result[0]["role"] is None

    def test_same_committee_different_roles_kept_separate(self):
        rows = [
            {"committee_name": "Finance", "role": "member"},
            {"committee_name": "Finance", "role": "chair"},
        ]
        result = _normalize_committees(rows)
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert _normalize_committees([]) == []


# ---------------------------------------------------------------------------
# _latest_snapshot_date
# ---------------------------------------------------------------------------


class TestLatestSnapshotDate:
    def test_returns_max_date(self):
        assert _latest_snapshot_date(SNAPSHOT_ROWS) == dt.date(2024, 6, 1)

    def test_empty_returns_today(self):
        result = _latest_snapshot_date([])
        assert result == dt.date.today()

    def test_single_row(self):
        rows = [{"snapshot_at": dt.date(2023, 12, 31)}]
        assert _latest_snapshot_date(rows) == dt.date(2023, 12, 31)


# ---------------------------------------------------------------------------
# _count_distinct_evidence_cards
# ---------------------------------------------------------------------------


class TestCountDistinctEvidenceCards:
    def test_counts_distinct_ids(self):
        assert _count_distinct_evidence_cards(FIRE_ROWS) == 3

    def test_deduplicates(self):
        fires = FIRE_ROWS + [
            {**FIRE_ROWS[0], "rule_id": "other.v1"},  # same ec-001
        ]
        assert _count_distinct_evidence_cards(fires) == 3

    def test_empty_returns_zero(self):
        assert _count_distinct_evidence_cards([]) == 0

    def test_missing_evidence_card_id_ignored(self):
        fires = [{"evidence_card_id": None, "rule_id": "r.v1"}]
        with pytest.raises(ValueError, match="evidence_card_id"):
            _count_distinct_evidence_cards(fires)


# ---------------------------------------------------------------------------
# assemble_member_profile — integration
# ---------------------------------------------------------------------------


class TestAssembleMemberProfile:
    def test_returns_member_profile_payload(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert isinstance(result, MemberProfilePayload)

    def test_bioguide_id(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.bioguide_id == "A000001"

    def test_name(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.name == "Jane Smith"

    def test_snapshot_date_from_latest_row(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.snapshot_date == dt.date(2024, 6, 1)

    def test_total_evidence_cards(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.total_evidence_cards == 3

    def test_top_evidence_card_ids_surface_recent_cards(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.top_evidence_card_ids == ["ec-002", "ec-001", "ec-003"]

    def test_scores_sorted_by_dimension(self):
        snapshot = [
            {
                "snapshot_at": dt.date(2024, 6, 1),
                "dimension_scores": {"zzz": 1.0, "aaa": 2.0},
            }
        ]
        result = assemble_member_profile(MEMBER_ROW, snapshot, [], [])
        assert [s.dimension for s in result.scores] == ["aaa", "zzz"]

    def test_recent_fires_limited_and_sorted(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        dates = [f.snapshot_date for f in result.recent_rule_fires]
        assert dates == sorted(dates, reverse=True)

    def test_committees_deduplicated(self):
        dup_committees = COMMITTEE_ROWS + [
            {"committee_name": "Committee on Energy", "role": "member"}
        ]
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, dup_committees)
        energy = [c for c in result.committees if c.committee_name == "Committee on Energy"]
        assert len(energy) == 1

    def test_empty_snapshot_rows(self):
        result = assemble_member_profile(MEMBER_ROW, [], FIRE_ROWS, COMMITTEE_ROWS)
        assert result.scores == []
        assert result.total_evidence_cards == 3

    def test_empty_fire_rows(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, [], COMMITTEE_ROWS)
        assert result.recent_rule_fires == []
        assert result.total_evidence_cards == 0

    def test_empty_committee_rows(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, [])
        assert result.committees == []

    def test_district_none_for_senator(self):
        senator = {**MEMBER_ROW, "chamber": "senate", "district": None}
        result = assemble_member_profile(senator, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.district is None

    def test_chamber_forwarded(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.chamber == "house"

    def test_party_forwarded(self):
        result = assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
        assert result.party == "D"

    def test_orphan_rule_fire_without_evidence_card_rejected(self):
        orphan = {
            "rule_id": "orphan.v1",
            "dimension": "conflict_of_interest_risk",
            "evidence_card_id": None,
            "short_explanation": "Missing card.",
            "score_delta": 1.0,
            "snapshot_date": dt.date(2024, 7, 1),
        }
        with pytest.raises(ValueError, match="evidence_card_id"):
            assemble_member_profile(MEMBER_ROW, SNAPSHOT_ROWS, [orphan], COMMITTEE_ROWS)
