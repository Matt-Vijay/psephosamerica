"""Tests for parse.disclosures.senate_index_lookup.

No network calls; fetch_senate_index is patched throughout.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.parse.disclosures.senate_index import SenateIndexRow
from src.parse.disclosures.senate_index_lookup import (
    fetch_senate_rows_by_doc_id,
    index_senate_rows_by_doc_id,
)


# ---------------------------------------------------------------------------
# Shared row fixtures
# ---------------------------------------------------------------------------


def _row(doc_id: str, first: str = "A", last: str = "B", year: int = 2023) -> SenateIndexRow:
    return SenateIndexRow(
        first_name=first,
        last_name=last,
        office="Senator, TX",
        report_type="Annual Report for CY2023",
        date_filed="01/15/2024",
        doc_id=doc_id,
        filing_year=year,
    )


_ROW_A = _row("abc-111", first="Alice", last="Adams")
_ROW_B = _row("xyz-222", first="Bob", last="Baker")


# ---------------------------------------------------------------------------
# index_senate_rows_by_doc_id
# ---------------------------------------------------------------------------


class TestIndexSenateRowsByDocId:
    def test_empty_list_returns_empty_dict(self):
        assert index_senate_rows_by_doc_id([]) == {}

    def test_single_row_keyed_by_its_doc_id(self):
        result = index_senate_rows_by_doc_id([_ROW_A])
        assert "abc-111" in result

    def test_single_row_value_is_the_row(self):
        result = index_senate_rows_by_doc_id([_ROW_A])
        assert result["abc-111"] is _ROW_A

    def test_two_rows_both_present(self):
        result = index_senate_rows_by_doc_id([_ROW_A, _ROW_B])
        assert "abc-111" in result
        assert "xyz-222" in result

    def test_two_rows_correct_values(self):
        result = index_senate_rows_by_doc_id([_ROW_A, _ROW_B])
        assert result["abc-111"] is _ROW_A
        assert result["xyz-222"] is _ROW_B

    def test_dict_length_matches_unique_doc_ids(self):
        result = index_senate_rows_by_doc_id([_ROW_A, _ROW_B])
        assert len(result) == 2

    def test_values_are_senate_index_row_instances(self):
        result = index_senate_rows_by_doc_id([_ROW_A, _ROW_B])
        assert all(isinstance(v, SenateIndexRow) for v in result.values())

    def test_duplicate_doc_id_last_occurrence_wins(self):
        # Invariant: last row in sequence takes precedence on collision.
        row_first = _row("dup-999", first="First")
        row_last = _row("dup-999", first="Last")
        result = index_senate_rows_by_doc_id([row_first, row_last])
        assert result["dup-999"].first_name == "Last"

    def test_returns_dict(self):
        assert isinstance(index_senate_rows_by_doc_id([_ROW_A]), dict)

    def test_keys_are_strings(self):
        result = index_senate_rows_by_doc_id([_ROW_A, _ROW_B])
        assert all(isinstance(k, str) for k in result)

    def test_original_list_not_mutated(self):
        rows = [_ROW_A, _ROW_B]
        index_senate_rows_by_doc_id(rows)
        assert rows == [_ROW_A, _ROW_B]


# ---------------------------------------------------------------------------
# fetch_senate_rows_by_doc_id
# ---------------------------------------------------------------------------


class TestFetchSenateRowsByDocId:
    def test_returns_dict(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[],
        ):
            result = fetch_senate_rows_by_doc_id(2023)
        assert isinstance(result, dict)

    def test_empty_fetch_returns_empty_dict(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[],
        ):
            assert fetch_senate_rows_by_doc_id(2023) == {}

    def test_rows_keyed_by_doc_id(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[_ROW_A, _ROW_B],
        ):
            result = fetch_senate_rows_by_doc_id(2023)
        assert set(result.keys()) == {"abc-111", "xyz-222"}

    def test_values_match_fetched_rows(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[_ROW_A, _ROW_B],
        ):
            result = fetch_senate_rows_by_doc_id(2023)
        assert result["abc-111"] is _ROW_A
        assert result["xyz-222"] is _ROW_B

    def test_year_forwarded_to_fetch(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[],
        ) as mock_fetch:
            fetch_senate_rows_by_doc_id(2024)
        mock_fetch.assert_called_once_with(2024, client=None)

    def test_client_forwarded_to_fetch(self):
        mock_client = MagicMock()
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[],
        ) as mock_fetch:
            fetch_senate_rows_by_doc_id(2023, client=mock_client)
        mock_fetch.assert_called_once_with(2023, client=mock_client)

    def test_fetch_called_exactly_once(self):
        with patch(
            "src.parse.disclosures.senate_index_lookup.fetch_senate_index",
            return_value=[_ROW_A],
        ) as mock_fetch:
            fetch_senate_rows_by_doc_id(2023)
        assert mock_fetch.call_count == 1
