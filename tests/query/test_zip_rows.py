"""Tests for zip_rows query helpers.

No live DB — fetch_all is patched at the call site in each test.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.query.zip_rows import (
    fetch_recent_evidence_ids_by_bioguide,
    fetch_zip_member_summary_rows,
)

CONN = MagicMock()
MODULE = "src.query.zip_rows.fetch_all"


class TestFetchZipMemberSummaryRows:
    _ROW = {
        "bioguide_id": "A000001",
        "dimension": "conflict_of_interest_risk",
        "current_score": 15.0,
        "rule_fire_count": 3,
    }

    def test_returns_rows(self):
        with patch(MODULE, return_value=[self._ROW]) as mock_fa:
            result = fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001"])
        assert result == [self._ROW]
        assert mock_fa.call_args[0][0] is CONN

    def test_returns_empty_list_when_no_rule_fires(self):
        with patch(MODULE, return_value=[]):
            result = fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001"])
        assert result == []

    def test_accepts_set_of_bioguide_ids(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_zip_member_summary_rows(CONN, bioguide_ids={"A000001", "B000002"})
        params = mock_fa.call_args[0][2]
        assert set(params["bioguide_ids"]) == {"A000001", "B000002"}

    def test_passes_bioguide_ids_as_list(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001", "B000002"])
        params = mock_fa.call_args[0][2]
        assert isinstance(params["bioguide_ids"], list)

    def test_sql_references_score_snapshot(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001"])
        sql = mock_fa.call_args[0][1]
        assert "score_snapshot" in sql

    def test_sql_references_rule_fire(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001"])
        sql = mock_fa.call_args[0][1]
        assert "rule_fire" in sql

    def test_row_shape_is_feed_compatible(self):
        """Rows must carry the four keys assemble_zip_feed expects."""
        with patch(MODULE, return_value=[self._ROW]):
            result = fetch_zip_member_summary_rows(CONN, bioguide_ids=["A000001"])
        row = result[0]
        assert "bioguide_id" in row
        assert "dimension" in row
        assert "current_score" in row
        assert "rule_fire_count" in row

    def test_multiple_members(self):
        rows = [
            {**self._ROW, "bioguide_id": "A000001"},
            {**self._ROW, "bioguide_id": "B000002", "rule_fire_count": 1},
        ]
        with patch(MODULE, return_value=rows):
            result = fetch_zip_member_summary_rows(
                CONN, bioguide_ids=["A000001", "B000002"]
            )
        assert len(result) == 2
        assert {r["bioguide_id"] for r in result} == {"A000001", "B000002"}


class TestFetchRecentEvidenceIdsByBioguide:
    def test_returns_grouped_dict(self):
        raw_rows = [
            {"bioguide_id": "A000001", "public_id": "ec_111"},
            {"bioguide_id": "A000001", "public_id": "ec_222"},
            {"bioguide_id": "B000002", "public_id": "ec_333"},
        ]
        with patch(MODULE, return_value=raw_rows):
            result = fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids=["A000001", "B000002"]
            )
        assert result == {
            "A000001": ["ec_111", "ec_222"],
            "B000002": ["ec_333"],
        }

    def test_returns_empty_dict_when_no_cards(self):
        with patch(MODULE, return_value=[]):
            result = fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids=["A000001"]
            )
        assert result == {}

    def test_accepts_set_of_bioguide_ids(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids={"A000001", "B000002"}
            )
        params = mock_fa.call_args[0][2]
        assert set(params["bioguide_ids"]) == {"A000001", "B000002"}

    def test_passes_bioguide_ids_as_list(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids=["A000001"]
            )
        params = mock_fa.call_args[0][2]
        assert isinstance(params["bioguide_ids"], list)

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recent_evidence_ids_by_bioguide(CONN, bioguide_ids=["A000001"])
        assert mock_fa.call_args[0][0] is CONN

    def test_sql_filters_rendered_at_not_null(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recent_evidence_ids_by_bioguide(CONN, bioguide_ids=["A000001"])
        sql = mock_fa.call_args[0][1]
        assert "rendered_at IS NOT NULL" in sql

    def test_sql_references_evidence_card(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recent_evidence_ids_by_bioguide(CONN, bioguide_ids=["A000001"])
        sql = mock_fa.call_args[0][1]
        assert "evidence_card" in sql

    def test_preserves_order(self):
        """Order from DB (most-recent-first) must be preserved in the output list."""
        raw_rows = [
            {"bioguide_id": "A000001", "public_id": "ec_newest"},
            {"bioguide_id": "A000001", "public_id": "ec_older"},
            {"bioguide_id": "A000001", "public_id": "ec_oldest"},
        ]
        with patch(MODULE, return_value=raw_rows):
            result = fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids=["A000001"]
            )
        assert result["A000001"] == ["ec_newest", "ec_older", "ec_oldest"]

    def test_member_not_in_input_excluded(self):
        """Only requested bioguide_ids should appear; DB does the filtering."""
        raw_rows = [{"bioguide_id": "A000001", "public_id": "ec_111"}]
        with patch(MODULE, return_value=raw_rows):
            result = fetch_recent_evidence_ids_by_bioguide(
                CONN, bioguide_ids=["A000001"]
            )
        assert "B000002" not in result
