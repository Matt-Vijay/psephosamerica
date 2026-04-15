"""Tests for src/query/source_artifact_rows.py.

No live DB.  fetch_all is patched at the call site in each test.

Tests focus on behavioral contracts: return values, parameter building,
and filter propagation — not SQL structure.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

from src.query.source_artifact_rows import (
    fetch_disclosure_artifact_rows,
    fetch_unparsed_disclosure_artifact_rows,
)

CONN = MagicMock()
MODULE = "src.query.source_artifact_rows.fetch_all"

_ARTIFACT_HOUSE: dict[str, Any] = {
    "id": 1,
    "artifact_kind": "pdf",
    "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf",
    "storage_uri": "disclosures/house/2024/A000001/12345.pdf",
    "sha256": "a" * 64,
    "mime_type": "application/pdf",
    "fetched_at": dt.datetime(2024, 6, 1, 12, 0),
    "source_record_id": "12345",
    "is_immutable": True,
    "ingestion_run_id": 1,
    "data_source_id": 1,
    "source_slug": "house-disclosures",
    "chamber": "house",
    "filing_year": 2024,
}

_ARTIFACT_SENATE: dict[str, Any] = {
    "id": 2,
    "artifact_kind": "pdf",
    "source_url": "https://efdsearch.senate.gov/search/view/paper/67890/",
    "storage_uri": "disclosures/senate/2024/B000002/67890.pdf",
    "sha256": "b" * 64,
    "mime_type": "application/pdf",
    "fetched_at": dt.datetime(2024, 6, 2, 12, 0),
    "source_record_id": "67890",
    "is_immutable": True,
    "ingestion_run_id": 1,
    "data_source_id": 2,
    "source_slug": "senate-disclosures",
    "chamber": "senate",
    "filing_year": 2024,
}

_ARTIFACT_UNPARSED: dict[str, Any] = {
    **_ARTIFACT_HOUSE,
    "id": 3,
    "sha256": "c" * 64,
    "storage_uri": "disclosures/house/2024/A000001/99999.pdf",
    "source_record_id": "99999",
    "filing_year": None,
}


# ---------------------------------------------------------------------------
# fetch_disclosure_artifact_rows
# ---------------------------------------------------------------------------


class TestFetchDisclosureArtifactRows:
    def test_returns_rows_from_fetch_all(self):
        rows = [_ARTIFACT_HOUSE, _ARTIFACT_SENATE]
        with patch(MODULE, return_value=rows) as mock_fa:
            result = fetch_disclosure_artifact_rows(CONN)
        mock_fa.assert_called_once()
        assert result == rows

    def test_passes_conn_as_first_arg(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN)
        assert mock_fa.call_args[0][0] is CONN

    def test_params_include_both_disclosure_slugs(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "house-disclosures" in params["slugs"]
        assert "senate-disclosures" in params["slugs"]

    def test_no_chamber_filter_by_default(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "chamber_slug" not in params

    def test_chamber_house_sets_slug_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN, chamber="house")
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "house-disclosures"

    def test_chamber_senate_sets_slug_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN, chamber="senate")
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "senate-disclosures"

    def test_year_filter_adds_year_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN, year=2024)
        _, _, params = mock_fa.call_args[0]
        assert params["year"] == 2024

    def test_no_year_param_when_year_is_none(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "year" not in params

    def test_limit_adds_limit_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN, limit=10)
        _, _, params = mock_fa.call_args[0]
        assert params["limit"] == 10

    def test_no_limit_param_when_limit_is_none(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "limit" not in params

    def test_returns_empty_list_when_no_rows(self):
        with patch(MODULE, return_value=[]):
            result = fetch_disclosure_artifact_rows(CONN)
        assert result == []

    def test_all_filters_combined(self):
        with patch(MODULE, return_value=[_ARTIFACT_HOUSE]) as mock_fa:
            result = fetch_disclosure_artifact_rows(CONN, chamber="house", year=2024, limit=5)
        assert result == [_ARTIFACT_HOUSE]
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "house-disclosures"
        assert params["year"] == 2024
        assert params["limit"] == 5

    def test_row_has_chamber_field(self):
        with patch(MODULE, return_value=[_ARTIFACT_HOUSE]):
            result = fetch_disclosure_artifact_rows(CONN)
        assert result[0]["chamber"] == "house"

    def test_row_has_source_slug_field(self):
        with patch(MODULE, return_value=[_ARTIFACT_SENATE]):
            result = fetch_disclosure_artifact_rows(CONN)
        assert result[0]["source_slug"] == "senate-disclosures"


# ---------------------------------------------------------------------------
# fetch_unparsed_disclosure_artifact_rows
# ---------------------------------------------------------------------------


class TestFetchUnparsedDisclosureArtifactRows:
    def test_returns_rows_from_fetch_all(self):
        with patch(MODULE, return_value=[_ARTIFACT_UNPARSED]) as mock_fa:
            result = fetch_unparsed_disclosure_artifact_rows(CONN)
        mock_fa.assert_called_once()
        assert result == [_ARTIFACT_UNPARSED]

    def test_passes_conn_as_first_arg(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN)
        assert mock_fa.call_args[0][0] is CONN

    def test_params_include_both_disclosure_slugs(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "house-disclosures" in params["slugs"]
        assert "senate-disclosures" in params["slugs"]

    def test_no_chamber_param_by_default(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "chamber_slug" not in params

    def test_chamber_house_sets_slug_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN, chamber="house")
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "house-disclosures"

    def test_chamber_senate_sets_slug_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN, chamber="senate")
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "senate-disclosures"

    def test_limit_adds_limit_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN, limit=20)
        _, _, params = mock_fa.call_args[0]
        assert params["limit"] == 20

    def test_no_limit_param_when_limit_is_none(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_unparsed_disclosure_artifact_rows(CONN)
        _, _, params = mock_fa.call_args[0]
        assert "limit" not in params

    def test_returns_empty_when_all_parsed(self):
        with patch(MODULE, return_value=[]):
            result = fetch_unparsed_disclosure_artifact_rows(CONN)
        assert result == []

    def test_chamber_and_limit_combined(self):
        with patch(MODULE, return_value=[_ARTIFACT_UNPARSED]) as mock_fa:
            result = fetch_unparsed_disclosure_artifact_rows(CONN, chamber="house", limit=50)
        assert result == [_ARTIFACT_UNPARSED]
        _, _, params = mock_fa.call_args[0]
        assert params["chamber_slug"] == "house-disclosures"
        assert params["limit"] == 50
