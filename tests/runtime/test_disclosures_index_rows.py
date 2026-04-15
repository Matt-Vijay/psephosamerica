"""Tests for src/runtime/disclosures_index_rows.py.

No live network.  fetch_disclosure_rows_by_doc_id is the only external
boundary; all tests mock it.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_index_rows import (
    ArtifactIndexMatch,
    fetch_index_rows_for_artifacts,
    group_artifacts_by_chamber_year,
)

_LOOKUP = "src.runtime.disclosures_index_rows.fetch_disclosure_rows_by_doc_id"

# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _house_row(doc_id: str, year: int = 2024) -> HouseIndexRow:
    return HouseIndexRow(
        last_name="Smith",
        first_name="Jane",
        suffix="",
        raw_filing_type="O",
        state_dst="CA08",
        year=year,
        filing_date=date(year, 3, 15),
        doc_id=doc_id,
        filing_kind=HouseFilingKind.ANNUAL,
    )


def _senate_row(doc_id: str, year: int = 2024) -> SenateIndexRow:
    return SenateIndexRow(
        first_name="John",
        last_name="Doe",
        office="Senator, TX",
        report_type="Annual Report for CY2024",
        date_filed="01/15/2025",
        doc_id=doc_id,
        filing_year=year,
    )


def _artifact(
    chamber: str,
    year: int,
    source_record_id: str,
    artifact_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": artifact_id,
        "chamber": chamber,
        "filing_year": year,
        "source_record_id": source_record_id,
    }


# ---------------------------------------------------------------------------
# group_artifacts_by_chamber_year
# ---------------------------------------------------------------------------


class TestGroupArtifactsByChamberYear:
    def test_empty_input_returns_empty_dict(self):
        assert group_artifacts_by_chamber_year([]) == {}

    def test_single_artifact_creates_one_group(self):
        rows = [_artifact("senate", 2024, "DOC1")]
        result = group_artifacts_by_chamber_year(rows)
        assert list(result.keys()) == [("senate", 2024)]

    def test_single_artifact_group_contains_row(self):
        row = _artifact("senate", 2024, "DOC1")
        result = group_artifacts_by_chamber_year([row])
        assert result[("senate", 2024)] == [row]

    def test_different_chambers_produce_separate_groups(self):
        ha = _artifact("house", 2024, "DOC1", artifact_id=1)
        sa = _artifact("senate", 2024, "DOC2", artifact_id=2)
        result = group_artifacts_by_chamber_year([ha, sa])
        assert set(result.keys()) == {("house", 2024), ("senate", 2024)}

    def test_same_chamber_year_grouped_together(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        result = group_artifacts_by_chamber_year([a1, a2])
        assert result[("senate", 2024)] == [a1, a2]

    def test_different_years_produce_separate_groups(self):
        a1 = _artifact("house", 2023, "DOC1", artifact_id=1)
        a2 = _artifact("house", 2024, "DOC2", artifact_id=2)
        result = group_artifacts_by_chamber_year([a1, a2])
        assert ("house", 2023) in result
        assert ("house", 2024) in result

    def test_none_filing_year_excluded(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": None, "source_record_id": "DOC1"}
        assert group_artifacts_by_chamber_year([row]) == {}

    def test_none_chamber_excluded(self):
        row: dict[str, Any] = {"id": 1, "chamber": None, "filing_year": 2024, "source_record_id": "DOC1"}
        assert group_artifacts_by_chamber_year([row]) == {}

    def test_year_cast_to_int(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": "2024", "source_record_id": "DOC1"}
        result = group_artifacts_by_chamber_year([row])
        assert ("senate", 2024) in result

    def test_order_within_group_preserved(self):
        a1 = _artifact("house", 2024, "D1", artifact_id=1)
        a2 = _artifact("house", 2024, "D2", artifact_id=2)
        a3 = _artifact("house", 2024, "D3", artifact_id=3)
        result = group_artifacts_by_chamber_year([a1, a2, a3])
        assert result[("house", 2024)] == [a1, a2, a3]


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — return shape
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_empty_artifacts_returns_empty_list(self):
        result = fetch_index_rows_for_artifacts([])
        assert result == []

    def test_returns_one_match_per_artifact(self):
        row = _artifact("senate", 2024, "DOC1")
        with patch(_LOOKUP, return_value={"DOC1": _senate_row("DOC1")}):
            result = fetch_index_rows_for_artifacts([row])
        assert len(result) == 1

    def test_returns_artifact_index_match_instances(self):
        row = _artifact("senate", 2024, "DOC1")
        with patch(_LOOKUP, return_value={"DOC1": _senate_row("DOC1")}):
            result = fetch_index_rows_for_artifacts([row])
        assert all(isinstance(m, ArtifactIndexMatch) for m in result)

    def test_match_order_follows_artifact_order(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        with patch(_LOOKUP, return_value={}):
            result = fetch_index_rows_for_artifacts([a1, a2])
        assert result[0].artifact is a1
        assert result[1].artifact is a2


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — artifact row identity
# ---------------------------------------------------------------------------


class TestArtifactIdentity:
    def test_match_preserves_artifact_row_identity(self):
        row = _artifact("senate", 2024, "DOC1")
        with patch(_LOOKUP, return_value={"DOC1": _senate_row("DOC1")}):
            result = fetch_index_rows_for_artifacts([row])
        assert result[0].artifact is row

    def test_artifact_row_not_copied_on_miss(self):
        row = _artifact("senate", 2024, "DOC_MISSING")
        with patch(_LOOKUP, return_value={}):
            result = fetch_index_rows_for_artifacts([row])
        assert result[0].artifact is row


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — resolution by source_record_id
# ---------------------------------------------------------------------------


class TestResolutionBySrcRecordId:
    def test_matching_doc_id_returns_index_row(self):
        row = _artifact("senate", 2024, "DOC1")
        s_row = _senate_row("DOC1")
        with patch(_LOOKUP, return_value={"DOC1": s_row}):
            result = fetch_index_rows_for_artifacts([row])
        assert result[0].index_row is s_row

    def test_unmatched_doc_id_returns_none_index_row(self):
        row = _artifact("senate", 2024, "DOC_MISSING")
        with patch(_LOOKUP, return_value={"DOC1": _senate_row("DOC1")}):
            result = fetch_index_rows_for_artifacts([row])
        assert result[0].index_row is None

    def test_house_doc_id_matched(self):
        row = _artifact("house", 2024, "HDOC1")
        h_row = _house_row("HDOC1")
        with patch(_LOOKUP, return_value={"HDOC1": h_row}):
            result = fetch_index_rows_for_artifacts([row])
        assert result[0].index_row is h_row

    def test_multiple_artifacts_different_matches(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        s1 = _senate_row("DOC1")
        s2 = _senate_row("DOC2")
        with patch(_LOOKUP, return_value={"DOC1": s1, "DOC2": s2}):
            result = fetch_index_rows_for_artifacts([a1, a2])
        assert result[0].index_row is s1
        assert result[1].index_row is s2


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — lookup call count (one per group)
# ---------------------------------------------------------------------------


class TestLookupCallCount:
    def test_lookup_called_once_for_single_chamber_year(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([a1, a2])
        assert mock_lookup.call_count == 1

    def test_lookup_called_once_per_distinct_year(self):
        a1 = _artifact("senate", 2023, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([a1, a2])
        assert mock_lookup.call_count == 2

    def test_lookup_called_once_per_distinct_chamber(self):
        ha = _artifact("house", 2024, "DOC1", artifact_id=1)
        sa = _artifact("senate", 2024, "DOC2", artifact_id=2)
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([ha, sa])
        assert mock_lookup.call_count == 2

    def test_no_lookup_call_for_empty_artifacts(self):
        with patch(_LOOKUP) as mock_lookup:
            fetch_index_rows_for_artifacts([])
        mock_lookup.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — lookup receives correct arguments
# ---------------------------------------------------------------------------


class TestLookupArguments:
    def test_lookup_receives_chamber(self):
        row = _artifact("senate", 2024, "DOC1")
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([row])
        assert mock_lookup.call_args[0][0] == "senate"

    def test_lookup_receives_year(self):
        row = _artifact("senate", 2023, "DOC1")
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([row])
        assert mock_lookup.call_args[0][1] == 2023

    def test_lookup_receives_house_chamber(self):
        row = _artifact("house", 2024, "DOC1")
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([row])
        assert mock_lookup.call_args[0][0] == "house"

    def test_client_forwarded_to_lookup(self):
        row = _artifact("senate", 2024, "DOC1")
        fake_client = object()
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([row], client=fake_client)
        _, kwargs = mock_lookup.call_args
        assert kwargs.get("client") is fake_client

    def test_none_client_is_default(self):
        row = _artifact("senate", 2024, "DOC1")
        with patch(_LOOKUP, return_value={}) as mock_lookup:
            fetch_index_rows_for_artifacts([row])
        _, kwargs = mock_lookup.call_args
        assert kwargs.get("client") is None


# ---------------------------------------------------------------------------
# fetch_index_rows_for_artifacts — missing chamber or filing_year
# ---------------------------------------------------------------------------


class TestMissingFields:
    def test_none_filing_year_produces_none_index_row(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": None, "source_record_id": "DOC1"}
        result = fetch_index_rows_for_artifacts([row])
        assert result[0].index_row is None

    def test_none_chamber_produces_none_index_row(self):
        row: dict[str, Any] = {"id": 1, "chamber": None, "filing_year": 2024, "source_record_id": "DOC1"}
        result = fetch_index_rows_for_artifacts([row])
        assert result[0].index_row is None

    def test_no_lookup_call_when_year_is_none(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": None, "source_record_id": "DOC1"}
        with patch(_LOOKUP) as mock_lookup:
            fetch_index_rows_for_artifacts([row])
        mock_lookup.assert_not_called()

    def test_artifact_with_none_year_still_in_result(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": None, "source_record_id": "DOC1"}
        result = fetch_index_rows_for_artifacts([row])
        assert len(result) == 1
        assert result[0].artifact is row

    def test_valid_and_invalid_rows_mixed(self):
        valid = _artifact("senate", 2024, "DOC1", artifact_id=1)
        invalid: dict[str, Any] = {"id": 2, "chamber": "senate", "filing_year": None, "source_record_id": "DOC2"}
        with patch(_LOOKUP, return_value={"DOC1": _senate_row("DOC1")}):
            result = fetch_index_rows_for_artifacts([valid, invalid])
        assert len(result) == 2
        assert result[0].index_row is not None
        assert result[1].index_row is None
