"""Tests for src/runtime/disclosures_parse_inputs.py.

No live DB, no network.  The DB boundary (fetch_unparsed_disclosure_artifact_rows)
is mocked; local files are written to a tmp_path fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.disclosures_parse_inputs import (
    DisclosureParseInput,
    load_unparsed_disclosure_artifacts,
)

_FETCH = "src.runtime.disclosures_parse_inputs.fetch_unparsed_disclosure_artifact_rows"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PDF_BYTES = b"%PDF-1.4 senate fake"
_HOUSE_BYTES = b"%PDF-1.4 house fake"

_SENATE_ROW: dict[str, Any] = {
    "id": 1,
    "artifact_kind": "pdf",
    "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC1/",
    "storage_uri": "disclosures/senate/2024/S000001/DOC1.pdf",
    "sha256": "a" * 64,
    "mime_type": "application/pdf",
    "fetched_at": None,
    "source_record_id": "DOC1",
    "is_immutable": True,
    "ingestion_run_id": 10,
    "data_source_id": 7,
    "source_slug": "senate-disclosures",
    "chamber": "senate",
    "filing_year": 2024,
}

_HOUSE_ROW: dict[str, Any] = {
    "id": 2,
    "artifact_kind": "pdf",
    "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC2.pdf",
    "storage_uri": "disclosures/house/2024/H000001/DOC2.pdf",
    "sha256": "b" * 64,
    "mime_type": "application/pdf",
    "fetched_at": None,
    "source_record_id": "DOC2",
    "is_immutable": True,
    "ingestion_run_id": 11,
    "data_source_id": 8,
    "source_slug": "house-disclosures",
    "chamber": "house",
    "filing_year": 2024,
}


def _write_artifact(root: Path, row: dict[str, Any], data: bytes) -> None:
    dest = root / row["storage_uri"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _load_artifacts(
    tmp_path: Path,
    rows: list[dict[str, Any]],
    blobs: list[bytes] | None = None,
) -> list[DisclosureParseInput]:
    """Write artifacts to tmp_path, mock fetch, and return loaded inputs.

    *blobs* defaults to [_PDF_BYTES] * len(rows) when omitted.
    """
    if blobs is None:
        blobs = [_PDF_BYTES] * len(rows)
    for row, data in zip(rows, blobs):
        _write_artifact(tmp_path, row, data)
    conn = MagicMock()
    with patch(_FETCH, return_value=rows):
        return load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)


# ---------------------------------------------------------------------------
# Return type and shape
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_returns_list(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert isinstance(result, list)

    def test_returns_typed_inputs(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert all(isinstance(inp, DisclosureParseInput) for inp in result)

    def test_empty_rows_returns_empty_list(self, tmp_path):
        result = _load_artifacts(tmp_path, [])
        assert result == []

    def test_one_input_per_row(self, tmp_path):
        result = _load_artifacts(
            tmp_path,
            [_SENATE_ROW, _HOUSE_ROW],
            [_PDF_BYTES, _HOUSE_BYTES],
        )
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Field values
# ---------------------------------------------------------------------------


class TestFieldValues:
    def test_chamber_from_row(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert result[0].chamber == "senate"

    def test_source_record_id_from_row(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert result[0].source_record_id == "DOC1"

    def test_artifact_row_is_original_dict(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert result[0].artifact_row is _SENATE_ROW

    def test_local_bytes_match_file(self, tmp_path):
        result = _load_artifacts(tmp_path, [_SENATE_ROW])
        assert result[0].local_bytes == _PDF_BYTES

    def test_house_chamber_field(self, tmp_path):
        result = _load_artifacts(tmp_path, [_HOUSE_ROW], [_HOUSE_BYTES])
        assert result[0].chamber == "house"

    def test_house_source_record_id(self, tmp_path):
        result = _load_artifacts(tmp_path, [_HOUSE_ROW], [_HOUSE_BYTES])
        assert result[0].source_record_id == "DOC2"

    def test_bytes_distinguished_across_rows(self, tmp_path):
        result = _load_artifacts(
            tmp_path,
            [_SENATE_ROW, _HOUSE_ROW],
            [_PDF_BYTES, _HOUSE_BYTES],
        )
        assert result[0].local_bytes == _PDF_BYTES
        assert result[1].local_bytes == _HOUSE_BYTES


# ---------------------------------------------------------------------------
# DB wiring
# ---------------------------------------------------------------------------


class TestDbWiring:
    def test_passes_chamber_to_fetch(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[]) as mock_fetch:
            load_unparsed_disclosure_artifacts(
                conn, local_root=tmp_path, chamber="senate"
            )
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("chamber") == "senate"

    def test_passes_limit_to_fetch(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[]) as mock_fetch:
            load_unparsed_disclosure_artifacts(conn, local_root=tmp_path, limit=5)
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("limit") == 5

    def test_passes_conn_to_fetch(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[]) as mock_fetch:
            load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)
        assert mock_fetch.call_args[0][0] is conn

    def test_default_chamber_is_none(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[]) as mock_fetch:
            load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("chamber") is None

    def test_default_limit_is_none(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[]) as mock_fetch:
            load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("limit") is None


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def test_raises_file_not_found_when_artifact_absent(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[_SENATE_ROW]):
            with pytest.raises(FileNotFoundError):
                load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)

    def test_error_message_contains_path(self, tmp_path):
        conn = MagicMock()
        with patch(_FETCH, return_value=[_SENATE_ROW]):
            with pytest.raises(FileNotFoundError, match="DOC1.pdf"):
                load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)

    def test_first_missing_raises_before_second(self, tmp_path):
        conn = MagicMock()
        _write_artifact(tmp_path, _HOUSE_ROW, _HOUSE_BYTES)
        with patch(_FETCH, return_value=[_SENATE_ROW, _HOUSE_ROW]):
            with pytest.raises(FileNotFoundError, match="DOC1.pdf"):
                load_unparsed_disclosure_artifacts(conn, local_root=tmp_path)
