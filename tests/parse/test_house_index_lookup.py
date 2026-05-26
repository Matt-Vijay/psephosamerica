"""Tests for parse.disclosures.house_index_lookup.

No network calls; httpx boundaries are mocked throughout.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.parse.disclosures.house_index import (
    HouseFilingKind,
    HouseIndexRow,
    parse_house_index,
)
from src.parse.disclosures.house_index_lookup import (
    fetch_house_rows_by_doc_id,
    index_house_rows_by_doc_id,
)


# ---------------------------------------------------------------------------
# Shared XML fixtures (inline, no network)
# ---------------------------------------------------------------------------

_PTR_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <New>
    <Last>GARCIA</Last>
    <First>Mike</First>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>CA27</StateDst>
    <Year>2023</Year>
    <FilingDate>01/10/2024</FilingDate>
    <DocID>10023432</DocID>
  </New>
  <New>
    <Last>SMITH</Last>
    <First>Jane</First>
    <Suffix>Jr</Suffix>
    <FilingType>A</FilingType>
    <StateDst>TX05</StateDst>
    <Year>2023</Year>
    <FilingDate>03/15/2024</FilingDate>
    <DocID>10023999</DocID>
  </New>
</DISCLOSURE>
"""

_ANNUAL_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <Member>
    <Last>ADAMS</Last>
    <First>Alma</First>
    <Suffix></Suffix>
    <FilingType>O</FilingType>
    <StateDst>NC12</StateDst>
    <Year>2023</Year>
    <FilingDate>06/14/2024</FilingDate>
    <DocID>20024001</DocID>
  </Member>
</DISCLOSURE>
"""

_EMPTY_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
</DISCLOSURE>
"""


def _ptr_rows() -> list[HouseIndexRow]:
    return parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)


def _annual_rows() -> list[HouseIndexRow]:
    return parse_house_index(_ANNUAL_XML, year=2023, filing_kind=HouseFilingKind.ANNUAL)


# ---------------------------------------------------------------------------
# index_house_rows_by_doc_id — happy paths
# ---------------------------------------------------------------------------


class TestIndexHouseRowsByDocId:
    def test_empty_input_returns_empty_dict(self):
        assert index_house_rows_by_doc_id([]) == {}

    def test_keys_match_doc_ids(self):
        result = index_house_rows_by_doc_id(_ptr_rows())
        assert set(result.keys()) == {"10023432", "10023999"}

    def test_values_are_house_index_rows(self):
        result = index_house_rows_by_doc_id(_ptr_rows())
        for row in result.values():
            assert isinstance(row, HouseIndexRow)

    def test_lookup_by_known_doc_id_returns_correct_row(self):
        result = index_house_rows_by_doc_id(_ptr_rows())
        row = result["10023432"]
        assert row.last_name == "GARCIA"
        assert row.first_name == "Mike"
        assert row.state_dst == "CA27"
        assert row.filing_date == date(2024, 1, 10)

    def test_second_row_accessible_by_its_doc_id(self):
        result = index_house_rows_by_doc_id(_ptr_rows())
        row = result["10023999"]
        assert row.last_name == "SMITH"
        assert row.suffix == "Jr"
        assert row.raw_filing_type == "A"

    def test_annual_rows_indexed_correctly(self):
        result = index_house_rows_by_doc_id(_annual_rows())
        assert "20024001" in result
        assert result["20024001"].filing_kind == HouseFilingKind.ANNUAL

    def test_single_row_produces_single_entry(self):
        result = index_house_rows_by_doc_id(_annual_rows())
        assert len(result) == 1

    def test_two_rows_produce_two_entries(self):
        result = index_house_rows_by_doc_id(_ptr_rows())
        assert len(result) == 2

    def test_row_identity_is_preserved(self):
        rows = _ptr_rows()
        result = index_house_rows_by_doc_id(rows)
        assert result["10023432"] is rows[0]
        assert result["10023999"] is rows[1]


# ---------------------------------------------------------------------------
# index_house_rows_by_doc_id — duplicate doc_id
# ---------------------------------------------------------------------------


class TestIndexHouseRowsByDocIdDuplicates:
    def test_duplicate_doc_id_raises_value_error(self):
        rows = _ptr_rows()
        # Manufacture a duplicate by repeating the first row.
        duplicate = HouseIndexRow(
            last_name="DUPE",
            first_name="D",
            suffix="",
            raw_filing_type="P",
            state_dst="CA27",
            year=2023,
            filing_date=date(2024, 1, 10),
            doc_id=rows[0].doc_id,  # same doc_id as rows[0]
            filing_kind=HouseFilingKind.PTR,
        )
        with pytest.raises(ValueError, match=rows[0].doc_id):
            index_house_rows_by_doc_id([rows[0], rows[1], duplicate])

    def test_error_message_includes_duplicate_doc_id(self):
        row = _annual_rows()[0]
        with pytest.raises(ValueError, match="20024001"):
            index_house_rows_by_doc_id([row, row])


# ---------------------------------------------------------------------------
# fetch_house_rows_by_doc_id — no network
# ---------------------------------------------------------------------------


class TestFetchHouseRowsByDocId:
    def _mock_client(self, xml: str) -> MagicMock:
        resp = MagicMock()
        resp.text = xml
        resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = resp
        return client

    def test_returns_dict_keyed_by_doc_id(self):
        client = self._mock_client(_PTR_XML)
        result = fetch_house_rows_by_doc_id(2023, HouseFilingKind.PTR, client=client)
        assert isinstance(result, dict)
        assert set(result.keys()) == {"10023432", "10023999"}

    def test_values_are_house_index_rows(self):
        client = self._mock_client(_PTR_XML)
        result = fetch_house_rows_by_doc_id(2023, HouseFilingKind.PTR, client=client)
        for row in result.values():
            assert isinstance(row, HouseIndexRow)

    def test_delegates_to_supplied_client(self):
        client = self._mock_client(_ANNUAL_XML)
        fetch_house_rows_by_doc_id(2023, HouseFilingKind.ANNUAL, client=client)
        assert client.get.call_count == 1

    def test_falls_back_to_httpx_stream_when_no_client(self):
        mock_resp = MagicMock()
        mock_resp.text = _ANNUAL_XML
        mock_resp.raise_for_status = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value = mock_resp
        with patch(
            "src.parse.disclosures.house_index.httpx.stream", return_value=mock_stream
        ) as mock_stream_fn:
            result = fetch_house_rows_by_doc_id(2023, HouseFilingKind.ANNUAL)
        assert mock_stream_fn.call_count == 1
        assert "20024001" in result

    def test_empty_index_returns_empty_dict(self):
        client = self._mock_client(_EMPTY_XML)
        result = fetch_house_rows_by_doc_id(2024, HouseFilingKind.PTR, client=client)
        assert result == {}

    def test_raises_http_status_error_from_client(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        client = MagicMock()
        client.get.return_value = resp
        with pytest.raises(httpx.HTTPStatusError):
            fetch_house_rows_by_doc_id(2023, HouseFilingKind.PTR, client=client)

    def test_ptr_url_issued_to_client(self):
        client = self._mock_client(_EMPTY_XML)
        fetch_house_rows_by_doc_id(2024, HouseFilingKind.PTR, client=client)
        url = client.get.call_args[0][0]
        assert "PTRindex.xml" in url
        assert "ptr-pdfs" in url

    def test_annual_url_issued_to_client(self):
        client = self._mock_client(_EMPTY_XML)
        fetch_house_rows_by_doc_id(2024, HouseFilingKind.ANNUAL, client=client)
        url = client.get.call_args[0][0]
        assert "FDindex.xml" in url
        assert "financial-pdfs" in url

    def test_filing_kind_preserved_in_returned_rows(self):
        client = self._mock_client(_PTR_XML)
        result = fetch_house_rows_by_doc_id(2023, HouseFilingKind.PTR, client=client)
        assert all(r.filing_kind == HouseFilingKind.PTR for r in result.values())
