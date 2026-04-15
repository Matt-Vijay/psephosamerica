"""Tests for src/parse/disclosures/discovery.py.

No network calls; fetch_house_index and fetch_senate_index are mocked.
Bioguide resolution is deliberately out-of-scope here (downstream step).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.senate_index import SenateIndexRow

_PATCH_HOUSE = "src.parse.disclosures.discovery.fetch_house_index"
_PATCH_SENATE = "src.parse.disclosures.discovery.fetch_senate_index"


# ---------------------------------------------------------------------------
# Inline fixture helpers
# ---------------------------------------------------------------------------


def _house_row(
    doc_id: str = "20240001",
    state_dst: str = "CA08",
    year: int = 2024,
    filing_kind: HouseFilingKind = HouseFilingKind.PTR,
) -> HouseIndexRow:
    return HouseIndexRow(
        last_name="Smith",
        first_name="Jane",
        suffix="",
        raw_filing_type="P",
        state_dst=state_dst,
        year=year,
        filing_date=date(year, 3, 15),
        doc_id=doc_id,
        filing_kind=filing_kind,
    )


def _senate_row(
    doc_id: str = "abc-uuid-001",
    filing_year: int = 2024,
) -> SenateIndexRow:
    return SenateIndexRow(
        first_name="Alice",
        last_name="Jones",
        office="Senator, TX",
        report_type="Annual Report for CY2023",
        date_filed="01/15/2024",
        doc_id=doc_id,
        filing_year=filing_year,
    )


# ---------------------------------------------------------------------------
# fetch_house_artifacts
# ---------------------------------------------------------------------------


class TestFetchHouseArtifacts:
    def test_returns_artifact_meta_list(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        rows = [_house_row("20240001"), _house_row("20240002")]
        with patch(_PATCH_HOUSE, return_value=rows):
            results = fetch_house_artifacts(2024, "ptr")

        assert len(results) == 2
        assert all(isinstance(r, ArtifactMeta) for r in results)

    def test_chamber_is_house(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row()]):
            results = fetch_house_artifacts(2024, "ptr")

        assert all(r.chamber == Chamber.HOUSE for r in results)

    def test_artifact_kind_is_pdf(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row()]):
            results = fetch_house_artifacts(2024, "ptr")

        assert all(r.artifact_kind == ArtifactKind.PDF for r in results)

    def test_source_record_id_is_doc_id(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        rows = [_house_row("20240001"), _house_row("20240002")]
        with patch(_PATCH_HOUSE, return_value=rows):
            results = fetch_house_artifacts(2024, "ptr")

        assert results[0].source_record_id == "20240001"
        assert results[1].source_record_id == "20240002"

    def test_filing_year_preserved(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row(year=2023)]):
            results = fetch_house_artifacts(2023, "ptr")

        assert results[0].filing_year == 2023

    def test_ptr_source_url_uses_ptr_path(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row("DOC123", filing_kind=HouseFilingKind.PTR)]):
            results = fetch_house_artifacts(2024, "ptr")

        assert "ptr-pdfs" in results[0].source_url
        assert "DOC123" in results[0].source_url

    def test_annual_source_url_uses_financial_path(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row("ANN456", filing_kind=HouseFilingKind.ANNUAL)]):
            results = fetch_house_artifacts(2024, "annual")

        assert "financial-pdfs" in results[0].source_url
        assert "ANN456" in results[0].source_url

    def test_source_slug_is_house_disclosures(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row()]):
            results = fetch_house_artifacts(2024, "ptr")

        assert results[0].source_slug == "house-disclosures"

    def test_member_bioguide_id_is_empty_provisional(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row()]):
            results = fetch_house_artifacts(2024, "ptr")

        # bioguide resolution is a downstream normalization step
        assert results[0].member_bioguide_id == ""

    def test_empty_index_returns_empty_list(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[]):
            results = fetch_house_artifacts(2024, "ptr")

        assert results == []

    def test_invalid_filing_kind_raises(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[]):
            with pytest.raises(ValueError):
                fetch_house_artifacts(2024, "unknown_kind")

    def test_filing_kind_forwarded_as_enum_to_index(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[]) as mock_idx:
            fetch_house_artifacts(2024, "annual")

        _, kwargs = mock_idx.call_args
        positional = mock_idx.call_args[0]
        assert positional[1] == HouseFilingKind.ANNUAL

    def test_client_forwarded_to_index(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        fake_client = MagicMock()
        with patch(_PATCH_HOUSE, return_value=[]) as mock_idx:
            fetch_house_artifacts(2024, "ptr", client=fake_client)

        assert mock_idx.call_args[1]["client"] is fake_client

    def test_year_forwarded_to_index(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[]) as mock_idx:
            fetch_house_artifacts(2022, "ptr")

        assert mock_idx.call_args[0][0] == 2022

    def test_storage_key_contains_doc_id(self):
        from src.parse.disclosures.discovery import fetch_house_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row("DOCXYZ")]):
            results = fetch_house_artifacts(2024, "ptr")

        assert "DOCXYZ" in results[0].storage_key


# ---------------------------------------------------------------------------
# fetch_senate_artifacts
# ---------------------------------------------------------------------------


class TestFetchSenateArtifacts:
    def test_returns_artifact_meta_list(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_senate_artifacts(2024)

        assert len(results) == 1
        assert isinstance(results[0], ArtifactMeta)

    def test_chamber_is_senate(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_senate_artifacts(2024)

        assert results[0].chamber == Chamber.SENATE

    def test_artifact_kind_is_pdf(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_senate_artifacts(2024)

        assert results[0].artifact_kind == ArtifactKind.PDF

    def test_source_record_id_is_doc_id(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row("uuid-999")]):
            results = fetch_senate_artifacts(2024)

        assert results[0].source_record_id == "uuid-999"

    def test_filing_year_preserved(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row(filing_year=2022)]):
            results = fetch_senate_artifacts(2022)

        assert results[0].filing_year == 2022

    def test_source_url_contains_doc_id(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row("my-doc-uuid")]):
            results = fetch_senate_artifacts(2024)

        assert "my-doc-uuid" in results[0].source_url

    def test_source_slug_is_senate_disclosures(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_senate_artifacts(2024)

        assert results[0].source_slug == "senate-disclosures"

    def test_member_bioguide_id_is_empty_provisional(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_senate_artifacts(2024)

        assert results[0].member_bioguide_id == ""

    def test_empty_index_returns_empty_list(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[]):
            results = fetch_senate_artifacts(2024)

        assert results == []

    def test_year_forwarded_to_index(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[]) as mock_idx:
            fetch_senate_artifacts(2021)

        assert mock_idx.call_args[0][0] == 2021

    def test_client_forwarded_to_index(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        fake_client = MagicMock()
        with patch(_PATCH_SENATE, return_value=[]) as mock_idx:
            fetch_senate_artifacts(2024, client=fake_client)

        assert mock_idx.call_args[1]["client"] is fake_client

    def test_multiple_rows_all_converted(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        rows = [_senate_row("uuid-A"), _senate_row("uuid-B"), _senate_row("uuid-C")]
        with patch(_PATCH_SENATE, return_value=rows):
            results = fetch_senate_artifacts(2024)

        assert len(results) == 3
        assert [r.source_record_id for r in results] == ["uuid-A", "uuid-B", "uuid-C"]

    def test_storage_key_contains_doc_id(self):
        from src.parse.disclosures.discovery import fetch_senate_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row("my-uuid")]):
            results = fetch_senate_artifacts(2024)

        assert "my-uuid" in results[0].storage_key


# ---------------------------------------------------------------------------
# fetch_disclosure_artifacts — house branch
# ---------------------------------------------------------------------------


class TestFetchDisclosureArtifactsHouse:
    def test_house_delegates_to_house_index(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        rows = [_house_row("20240001"), _house_row("20240002")]
        with patch(_PATCH_HOUSE, return_value=rows) as mock_idx:
            results = fetch_disclosure_artifacts("house", 2024, filing_kind="ptr")

        mock_idx.assert_called_once()
        assert len(results) == 2

    def test_house_results_have_house_chamber(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_HOUSE, return_value=[_house_row()]):
            results = fetch_disclosure_artifacts("house", 2024, filing_kind="ptr")

        assert all(r.chamber == Chamber.HOUSE for r in results)

    def test_house_requires_filing_kind(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with pytest.raises(ValueError, match="filing_kind"):
            fetch_disclosure_artifacts("house", 2024)

    def test_house_filing_kind_forwarded(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_HOUSE, return_value=[]) as mock_idx:
            fetch_disclosure_artifacts("house", 2024, filing_kind="annual")

        positional = mock_idx.call_args[0]
        assert positional[1] == HouseFilingKind.ANNUAL

    def test_house_client_forwarded(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        fake_client = MagicMock()
        with patch(_PATCH_HOUSE, return_value=[]) as mock_idx:
            fetch_disclosure_artifacts("house", 2024, filing_kind="ptr", client=fake_client)

        assert mock_idx.call_args[1]["client"] is fake_client

    def test_senate_index_not_called_for_house(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_HOUSE, return_value=[]):
            with patch(_PATCH_SENATE) as mock_senate:
                fetch_disclosure_artifacts("house", 2024, filing_kind="ptr")

        mock_senate.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_disclosure_artifacts — senate branch
# ---------------------------------------------------------------------------


class TestFetchDisclosureArtifactsSenate:
    def test_senate_delegates_to_senate_index(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]) as mock_idx:
            results = fetch_disclosure_artifacts("senate", 2024)

        mock_idx.assert_called_once()
        assert len(results) == 1

    def test_senate_results_have_senate_chamber(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]):
            results = fetch_disclosure_artifacts("senate", 2024)

        assert all(r.chamber == Chamber.SENATE for r in results)

    def test_senate_filing_kind_kwarg_is_ignored(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_SENATE, return_value=[_senate_row()]) as mock_idx:
            fetch_disclosure_artifacts("senate", 2024, filing_kind="annual")

        # Senate index receives only year and client — not filing_kind
        assert mock_idx.call_args[0][0] == 2024

    def test_senate_client_forwarded(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        fake_client = MagicMock()
        with patch(_PATCH_SENATE, return_value=[]) as mock_idx:
            fetch_disclosure_artifacts("senate", 2024, client=fake_client)

        assert mock_idx.call_args[1]["client"] is fake_client

    def test_house_index_not_called_for_senate(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with patch(_PATCH_SENATE, return_value=[]):
            with patch(_PATCH_HOUSE) as mock_house:
                fetch_disclosure_artifacts("senate", 2024)

        mock_house.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_disclosure_artifacts — invalid chamber
# ---------------------------------------------------------------------------


class TestFetchDisclosureArtifactsInvalidChamber:
    def test_both_rejected(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with pytest.raises(ValueError, match="'both'"):
            fetch_disclosure_artifacts("both", 2024, filing_kind="ptr")

    def test_unknown_string_rejected(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with pytest.raises(ValueError, match="chamber must be"):
            fetch_disclosure_artifacts("state", 2024)

    def test_error_includes_bad_value(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with pytest.raises(ValueError, match="'congress'"):
            fetch_disclosure_artifacts("congress", 2024)

    def test_empty_string_rejected(self):
        from src.parse.disclosures.discovery import fetch_disclosure_artifacts

        with pytest.raises(ValueError):
            fetch_disclosure_artifacts("", 2024)
