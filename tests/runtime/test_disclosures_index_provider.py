"""Tests for src/runtime/disclosures_index_provider.py.

No live network.  fetch_index_rows_for_artifacts is the live boundary;
all live_index_matches tests mock it.  bundle_index_matches tests are
fully offline — they drive a DisclosuresLookup built in-process.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_bundle import DisclosuresLookup
from src.runtime.disclosures_index_provider import bundle_index_matches, live_index_matches
from src.runtime.disclosures_index_rows import ArtifactIndexMatch

_LIVE_FETCH = "src.runtime.disclosures_index_provider.fetch_index_rows_for_artifacts"


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _house_index_row(doc_id: str, year: int = 2024) -> HouseIndexRow:
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


def _senate_index_row(doc_id: str, year: int = 2024) -> SenateIndexRow:
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


def _bundle(
    *,
    chamber: str = "senate",
    year: int = 2024,
    rows: dict[str, Any] | None = None,
) -> DisclosuresLookup:
    """Build a minimal DisclosuresLookup for tests."""
    if rows is None:
        rows = {}
    return {(chamber, year): rows}


# ---------------------------------------------------------------------------
# bundle_index_matches — return shape
# ---------------------------------------------------------------------------


class TestBundleReturnShape:
    def test_empty_artifacts_returns_empty_list(self):
        result = bundle_index_matches([], _bundle())
        assert result == []

    def test_returns_one_match_per_artifact(self):
        row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": row})
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert len(result) == 1

    def test_returns_artifact_index_match_instances(self):
        row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": row})
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert all(isinstance(m, ArtifactIndexMatch) for m in result)

    def test_match_order_follows_artifact_order(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        result = bundle_index_matches([a1, a2], _bundle())
        assert result[0].artifact is a1
        assert result[1].artifact is a2


# ---------------------------------------------------------------------------
# bundle_index_matches — resolution by source_record_id
# ---------------------------------------------------------------------------


class TestBundleResolution:
    def test_matching_doc_id_returns_index_row(self):
        s_row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": s_row})
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert result[0].index_row is s_row

    def test_unmatched_doc_id_returns_none_index_row(self):
        bundle = _bundle(rows={"DOC1": _senate_index_row("DOC1")})
        result = bundle_index_matches([_artifact("senate", 2024, "MISSING")], bundle)
        assert result[0].index_row is None

    def test_house_doc_id_resolved_from_bundle(self):
        h_row = _house_index_row("HDOC1")
        bundle: DisclosuresLookup = {("house", 2024): {"HDOC1": h_row}}
        result = bundle_index_matches([_artifact("house", 2024, "HDOC1")], bundle)
        assert result[0].index_row is h_row

    def test_multiple_artifacts_resolved_independently(self):
        s1 = _senate_index_row("DOC1")
        s2 = _senate_index_row("DOC2")
        bundle = _bundle(rows={"DOC1": s1, "DOC2": s2})
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        result = bundle_index_matches([a1, a2], bundle)
        assert result[0].index_row is s1
        assert result[1].index_row is s2

    def test_bundle_without_matching_chamber_year_returns_none(self):
        bundle: DisclosuresLookup = {("house", 2024): {"DOC1": _house_index_row("DOC1")}}
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert result[0].index_row is None

    def test_bundle_without_matching_year_returns_none(self):
        bundle = _bundle(chamber="senate", year=2023, rows={"DOC1": _senate_index_row("DOC1", year=2023)})
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert result[0].index_row is None


# ---------------------------------------------------------------------------
# bundle_index_matches — artifact identity
# ---------------------------------------------------------------------------


class TestBundleArtifactIdentity:
    def test_artifact_row_identity_preserved_on_hit(self):
        s_row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": s_row})
        row = _artifact("senate", 2024, "DOC1")
        result = bundle_index_matches([row], bundle)
        assert result[0].artifact is row

    def test_artifact_row_identity_preserved_on_miss(self):
        bundle = _bundle(rows={})
        row = _artifact("senate", 2024, "MISSING")
        result = bundle_index_matches([row], bundle)
        assert result[0].artifact is row


# ---------------------------------------------------------------------------
# bundle_index_matches — missing chamber or filing_year
# ---------------------------------------------------------------------------


class TestBundleMissingFields:
    def test_none_chamber_produces_none_index_row(self):
        row: dict[str, Any] = {"id": 1, "chamber": None, "filing_year": 2024, "source_record_id": "DOC1"}
        result = bundle_index_matches([row], _bundle())
        assert result[0].index_row is None

    def test_none_filing_year_produces_none_index_row(self):
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": None, "source_record_id": "DOC1"}
        result = bundle_index_matches([row], _bundle())
        assert result[0].index_row is None

    def test_none_chamber_artifact_still_in_result(self):
        row: dict[str, Any] = {"id": 1, "chamber": None, "filing_year": 2024, "source_record_id": "DOC1"}
        result = bundle_index_matches([row], _bundle())
        assert len(result) == 1
        assert result[0].artifact is row

    def test_valid_and_invalid_mixed(self):
        s_row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": s_row})
        valid = _artifact("senate", 2024, "DOC1", artifact_id=1)
        invalid: dict[str, Any] = {"id": 2, "chamber": None, "filing_year": 2024, "source_record_id": "DOC1"}
        result = bundle_index_matches([valid, invalid], bundle)
        assert result[0].index_row is s_row
        assert result[1].index_row is None


# ---------------------------------------------------------------------------
# bundle_index_matches — year coercion
# ---------------------------------------------------------------------------


class TestBundleYearCoercion:
    def test_string_filing_year_resolved_against_int_keyed_bundle(self):
        s_row = _senate_index_row("DOC1")
        bundle: DisclosuresLookup = {("senate", 2024): {"DOC1": s_row}}
        row: dict[str, Any] = {"id": 1, "chamber": "senate", "filing_year": "2024", "source_record_id": "DOC1"}
        result = bundle_index_matches([row], bundle)
        assert result[0].index_row is s_row


# ---------------------------------------------------------------------------
# live_index_matches — delegation to fetch_index_rows_for_artifacts
# ---------------------------------------------------------------------------


class TestLiveDelegation:
    def test_empty_artifacts_returns_empty_list(self):
        with patch(_LIVE_FETCH, return_value=[]) as mock_fetch:
            result = live_index_matches([])
        mock_fetch.assert_called_once_with([], client=None)
        assert result == []

    def test_delegates_to_fetch_index_rows_for_artifacts(self):
        artifact = _artifact("senate", 2024, "DOC1")
        expected = [ArtifactIndexMatch(artifact=artifact, index_row=None)]
        with patch(_LIVE_FETCH, return_value=expected) as mock_fetch:
            result = live_index_matches([artifact])
        mock_fetch.assert_called_once()
        assert result is expected

    def test_client_forwarded_to_fetch(self):
        fake_client = object()
        with patch(_LIVE_FETCH, return_value=[]) as mock_fetch:
            live_index_matches([], client=fake_client)
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("client") is fake_client

    def test_none_client_is_default(self):
        with patch(_LIVE_FETCH, return_value=[]) as mock_fetch:
            live_index_matches([])
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("client") is None

    def test_artifacts_forwarded_unchanged(self):
        a1 = _artifact("senate", 2024, "DOC1", artifact_id=1)
        a2 = _artifact("senate", 2024, "DOC2", artifact_id=2)
        with patch(_LIVE_FETCH, return_value=[]) as mock_fetch:
            live_index_matches([a1, a2])
        positional = mock_fetch.call_args[0]
        assert positional[0] == [a1, a2]

    def test_returns_fetch_result_directly(self):
        s_row = _senate_index_row("DOC1")
        artifact = _artifact("senate", 2024, "DOC1")
        expected = [ArtifactIndexMatch(artifact=artifact, index_row=s_row)]
        with patch(_LIVE_FETCH, return_value=expected):
            result = live_index_matches([artifact])
        assert result is expected


# ---------------------------------------------------------------------------
# Shared contract — IndexMatchResult type alias
# ---------------------------------------------------------------------------


class TestIndexMatchResultContract:
    """Both bundle and live paths must satisfy the same output contract.

    IndexMatchResult (= list[ArtifactIndexMatch]) is the canonical alias.
    Tests verify the shared guarantees: one result per artifact, order
    preserved, artifact identity preserved on both hit and miss.
    """

    def test_bundle_result_is_list_of_artifact_index_match(self):
        s_row = _senate_index_row("DOC1")
        bundle = _bundle(rows={"DOC1": s_row})
        result = bundle_index_matches([_artifact("senate", 2024, "DOC1")], bundle)
        assert isinstance(result, list)
        assert all(isinstance(m, ArtifactIndexMatch) for m in result)

    def test_live_result_is_list_of_artifact_index_match(self):
        expected = [ArtifactIndexMatch(artifact=_artifact("senate", 2024, "DOC1"), index_row=None)]
        with patch(_LIVE_FETCH, return_value=expected):
            result = live_index_matches([_artifact("senate", 2024, "DOC1")])
        assert isinstance(result, list)
        assert all(isinstance(m, ArtifactIndexMatch) for m in result)

    def test_bundle_and_live_return_same_length_as_input(self):
        artifacts = [
            _artifact("senate", 2024, "DOC1", artifact_id=1),
            _artifact("senate", 2024, "DOC2", artifact_id=2),
        ]
        bundle = _bundle(rows={})
        bundle_result = bundle_index_matches(artifacts, bundle)
        assert len(bundle_result) == len(artifacts)

        live_expected = [
            ArtifactIndexMatch(artifact=a, index_row=None) for a in artifacts
        ]
        with patch(_LIVE_FETCH, return_value=live_expected):
            live_result = live_index_matches(artifacts)
        assert len(live_result) == len(artifacts)

    def test_index_match_result_alias_importable(self):
        """IndexMatchResult is exported from the provider module."""
        from src.runtime.disclosures_index_provider import IndexMatchResult
        # It is a generic alias; verify bundle output is assignable.
        result: IndexMatchResult = bundle_index_matches([], _bundle())
        assert result == []
