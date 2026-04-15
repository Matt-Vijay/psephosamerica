"""Tests for parse.disclosures.house_index.

No network calls; httpx boundaries are mocked throughout.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.parse.disclosures.acquire import house_artifact_meta
from src.parse.disclosures.house_index import (
    HouseFilingKind,
    HouseIndexRow,
    fetch_house_index,
    house_index_url,
    parse_house_index,
)


# ---------------------------------------------------------------------------
# Inline XML fixtures
# ---------------------------------------------------------------------------

# PTR index uses <New> as the entry element tag.
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

# Annual (FD) index uses <Member> as the entry element tag.
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


# ---------------------------------------------------------------------------
# house_index_url
# ---------------------------------------------------------------------------


class TestHouseIndexUrl:
    def test_ptr_url_contains_ptr_path(self):
        url = house_index_url(2024, HouseFilingKind.PTR)
        assert url == "https://disclosures.house.gov/public_disc/ptr-pdfs/2024PTRindex.xml"

    def test_annual_url_contains_fd_path(self):
        url = house_index_url(2024, HouseFilingKind.ANNUAL)
        assert url == "https://disclosures.house.gov/public_disc/financial-pdfs/2024FDindex.xml"

    def test_year_is_embedded_in_ptr_url(self):
        assert "2022" in house_index_url(2022, HouseFilingKind.PTR)

    def test_year_is_embedded_in_annual_url(self):
        assert "2021" in house_index_url(2021, HouseFilingKind.ANNUAL)

    def test_base_domain_is_disclosures_house_gov(self):
        for kind in HouseFilingKind:
            assert house_index_url(2024, kind).startswith(
                "https://disclosures.house.gov"
            )


# ---------------------------------------------------------------------------
# parse_house_index — PTR index
# ---------------------------------------------------------------------------


class TestParseHouseIndexPtr:
    def test_returns_two_rows_for_two_entries(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        assert len(rows) == 2

    def test_first_row_all_fields(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        r = rows[0]
        assert r.last_name == "GARCIA"
        assert r.first_name == "Mike"
        assert r.suffix == ""
        assert r.raw_filing_type == "P"
        assert r.state_dst == "CA27"
        assert r.year == 2023
        assert r.filing_date == date(2024, 1, 10)
        assert r.doc_id == "10023432"
        assert r.filing_kind == HouseFilingKind.PTR

    def test_second_row_with_suffix_and_amendment(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        r = rows[1]
        assert r.suffix == "Jr"
        assert r.raw_filing_type == "A"
        assert r.doc_id == "10023999"
        assert r.state_dst == "TX05"
        assert r.filing_date == date(2024, 3, 15)

    def test_empty_index_returns_empty_list(self):
        rows = parse_house_index(_EMPTY_XML, year=2024, filing_kind=HouseFilingKind.PTR)
        assert rows == []

    def test_all_rows_are_house_index_rows(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        for r in rows:
            assert isinstance(r, HouseIndexRow)

    def test_filing_kind_stamped_on_every_row(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        assert all(r.filing_kind == HouseFilingKind.PTR for r in rows)


# ---------------------------------------------------------------------------
# parse_house_index — annual (FD) index
# ---------------------------------------------------------------------------


class TestParseHouseIndexAnnual:
    def test_annual_row_parsed(self):
        rows = parse_house_index(_ANNUAL_XML, year=2023, filing_kind=HouseFilingKind.ANNUAL)
        assert len(rows) == 1
        r = rows[0]
        assert r.last_name == "ADAMS"
        assert r.first_name == "Alma"
        assert r.state_dst == "NC12"
        assert r.raw_filing_type == "O"
        assert r.filing_date == date(2024, 6, 14)
        assert r.doc_id == "20024001"
        assert r.filing_kind == HouseFilingKind.ANNUAL

    def test_filing_kind_is_annual(self):
        rows = parse_house_index(_ANNUAL_XML, year=2023, filing_kind=HouseFilingKind.ANNUAL)
        assert rows[0].filing_kind == HouseFilingKind.ANNUAL

    def test_element_tag_name_is_irrelevant(self):
        # The parser must not care whether entries are <Member> or <New>.
        xml_with_member_tag = _ANNUAL_XML
        xml_with_new_tag = _ANNUAL_XML.replace("<Member>", "<New>").replace("</Member>", "</New>")
        rows_member = parse_house_index(xml_with_member_tag, year=2023, filing_kind=HouseFilingKind.ANNUAL)
        rows_new = parse_house_index(xml_with_new_tag, year=2023, filing_kind=HouseFilingKind.ANNUAL)
        assert rows_member[0].doc_id == rows_new[0].doc_id


# ---------------------------------------------------------------------------
# parse_house_index — error cases
# ---------------------------------------------------------------------------


class TestParseHouseIndexErrors:
    def test_missing_doc_id_raises_value_error(self):
        xml = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <New>
    <Last>JONES</Last>
    <First>Bob</First>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>NY01</StateDst>
    <Year>2023</Year>
    <FilingDate>02/01/2024</FilingDate>
  </New>
</DISCLOSURE>
"""
        with pytest.raises(ValueError, match="DocID"):
            parse_house_index(xml, year=2023, filing_kind=HouseFilingKind.PTR)

    def test_missing_last_name_raises_value_error(self):
        xml = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <New>
    <First>Bob</First>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>NY01</StateDst>
    <Year>2023</Year>
    <FilingDate>02/01/2024</FilingDate>
    <DocID>10000001</DocID>
  </New>
</DISCLOSURE>
"""
        with pytest.raises(ValueError, match="Last"):
            parse_house_index(xml, year=2023, filing_kind=HouseFilingKind.PTR)

    def test_bad_date_raises_value_error(self):
        xml = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <New>
    <Last>JONES</Last>
    <First>Bob</First>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>NY01</StateDst>
    <Year>2023</Year>
    <FilingDate>not-a-date</FilingDate>
    <DocID>10000001</DocID>
  </New>
</DISCLOSURE>
"""
        with pytest.raises(ValueError, match="[Uu]nparseable date"):
            parse_house_index(xml, year=2023, filing_kind=HouseFilingKind.PTR)

    def test_empty_doc_id_raises_value_error(self):
        xml = """\
<?xml version="1.0" encoding="utf-8"?>
<DISCLOSURE>
  <New>
    <Last>JONES</Last>
    <First>Bob</First>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>NY01</StateDst>
    <Year>2023</Year>
    <FilingDate>02/01/2024</FilingDate>
    <DocID>  </DocID>
  </New>
</DISCLOSURE>
"""
        with pytest.raises(ValueError, match="DocID"):
            parse_house_index(xml, year=2023, filing_kind=HouseFilingKind.PTR)


# ---------------------------------------------------------------------------
# HouseIndexRow → house_artifact_meta roundtrip
# ---------------------------------------------------------------------------


class TestHouseIndexRowToArtifactMeta:
    """A parsed row must supply the three arguments house_artifact_meta() needs."""

    def test_ptr_row_roundtrip(self):
        rows = parse_house_index(_PTR_XML, year=2023, filing_kind=HouseFilingKind.PTR)
        row = rows[0]
        # bioguide_id is resolved externally; use a stand-in here.
        meta = house_artifact_meta(
            bioguide_id="G000001",
            filing_year=row.year,
            doc_id=row.doc_id,
        )
        assert meta.filing_year == 2023
        assert meta.source_record_id == "10023432"
        assert meta.member_bioguide_id == "G000001"
        assert "disclosures.house.gov" in meta.source_url

    def test_annual_row_roundtrip(self):
        rows = parse_house_index(_ANNUAL_XML, year=2023, filing_kind=HouseFilingKind.ANNUAL)
        row = rows[0]
        meta = house_artifact_meta(
            bioguide_id="A000001",
            filing_year=row.year,
            doc_id=row.doc_id,
        )
        assert meta.source_record_id == "20024001"
        assert meta.filing_year == 2023


# ---------------------------------------------------------------------------
# fetch_house_index — no network
# ---------------------------------------------------------------------------


class TestFetchHouseIndex:
    def test_uses_supplied_client(self):
        mock_resp = MagicMock()
        mock_resp.text = _PTR_XML
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        rows = fetch_house_index(2023, HouseFilingKind.PTR, client=mock_client)

        assert len(rows) == 2
        mock_client.get.assert_called_once_with(
            house_index_url(2023, HouseFilingKind.PTR),
            follow_redirects=True,
        )

    def test_falls_back_to_httpx_get_when_no_client(self):
        mock_resp = MagicMock()
        mock_resp.text = _ANNUAL_XML
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "src.parse.disclosures.house_index.httpx.get", return_value=mock_resp
        ) as mock_get:
            rows = fetch_house_index(2023, HouseFilingKind.ANNUAL)

        assert len(rows) == 1
        assert mock_get.call_args[0][0] == house_index_url(2023, HouseFilingKind.ANNUAL)

    def test_raises_http_error_from_client(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        with pytest.raises(httpx.HTTPStatusError):
            fetch_house_index(2023, HouseFilingKind.PTR, client=mock_client)

    def test_raises_http_error_from_default_httpx(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with patch("src.parse.disclosures.house_index.httpx.get", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                fetch_house_index(2023, HouseFilingKind.ANNUAL)

    def test_ptr_url_contains_ptr_segment(self):
        mock_resp = MagicMock()
        mock_resp.text = _EMPTY_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        fetch_house_index(2024, HouseFilingKind.PTR, client=mock_client)

        url = mock_client.get.call_args[0][0]
        assert "2024PTRindex.xml" in url
        assert "ptr-pdfs" in url

    def test_annual_url_contains_fd_segment(self):
        mock_resp = MagicMock()
        mock_resp.text = _EMPTY_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        fetch_house_index(2024, HouseFilingKind.ANNUAL, client=mock_client)

        url = mock_client.get.call_args[0][0]
        assert "2024FDindex.xml" in url
        assert "financial-pdfs" in url
