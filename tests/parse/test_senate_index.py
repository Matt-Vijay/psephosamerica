"""Tests for parse.disclosures.senate_index.

No network calls; httpx is injected via the client= parameter.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.parse.disclosures.senate_index import (
    SenateIndexRow,
    fetch_senate_index,
    parse_senate_index,
    senate_index_url,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_LINK_A = '<a href="/search/view/paper/abc-123-def/">Annual Report for CY2023</a>'
_LINK_B = '<a href="/search/view/paper/xyz-789/">PTR</a>'

_TWO_ROW_PAYLOAD: dict = {
    "draw": 1,
    "recordsTotal": 2,
    "recordsFiltered": 2,
    "data": [
        ["John", "Smith", "Senator, TX", _LINK_A, "01/15/2024"],
        ["Jane", "Doe", "Senator, CA", _LINK_B, "03/20/2024"],
    ],
}


# ---------------------------------------------------------------------------
# senate_index_url
# ---------------------------------------------------------------------------


class TestSenateIndexUrl:
    def test_returns_string(self):
        assert isinstance(senate_index_url(2024), str)

    def test_contains_year(self):
        assert "2024" in senate_index_url(2024)

    def test_points_to_efdsearch_senate_gov(self):
        assert "efdsearch.senate.gov" in senate_index_url(2024)

    def test_different_years_produce_different_urls(self):
        assert senate_index_url(2023) != senate_index_url(2024)


# ---------------------------------------------------------------------------
# parse_senate_index — JSON dict path
# ---------------------------------------------------------------------------


class TestParseSenateIndexJson:
    def test_correct_row_count(self):
        rows = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)
        assert len(rows) == 2

    def test_first_row_name_fields(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        assert row.first_name == "John"
        assert row.last_name == "Smith"

    def test_first_row_office(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        assert row.office == "Senator, TX"

    def test_first_row_doc_id(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        assert row.doc_id == "abc-123-def"

    def test_second_row_doc_id(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[1]
        assert row.doc_id == "xyz-789"

    def test_first_row_date_filed(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        assert row.date_filed == "01/15/2024"

    def test_filing_year_propagated(self):
        rows = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)
        assert all(r.filing_year == 2023 for r in rows)

    def test_report_type_has_no_html_tags(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        assert "<" not in row.report_type
        assert ">" not in row.report_type

    def test_row_is_frozen(self):
        row = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)[0]
        with pytest.raises((AttributeError, TypeError)):
            row.first_name = "changed"  # type: ignore[misc]

    def test_empty_data_list_returns_empty(self):
        assert parse_senate_index({"data": []}, year=2023) == []

    def test_missing_data_key_returns_empty(self):
        assert parse_senate_index({}, year=2023) == []

    def test_row_without_link_dropped(self):
        payload: dict = {
            "data": [
                ["John", "Smith", "Senator, TX", "No link here", "01/15/2024"],
                ["Jane", "Doe", "Senator, CA", _LINK_A, "03/20/2024"],
            ]
        }
        rows = parse_senate_index(payload, year=2023)
        assert len(rows) == 1
        assert rows[0].last_name == "Doe"

    def test_short_row_dropped(self):
        payload: dict = {"data": [["only", "two"]]}
        assert parse_senate_index(payload, year=2023) == []

    def test_returns_list_of_senate_index_row(self):
        rows = parse_senate_index(_TWO_ROW_PAYLOAD, year=2023)
        assert all(isinstance(r, SenateIndexRow) for r in rows)


# ---------------------------------------------------------------------------
# parse_senate_index — HTML string path
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """\
<html><body>
<table>
<thead><tr><th>First</th><th>Last</th><th>Office</th><th>Report</th><th>Filed</th></tr></thead>
<tbody>
  <tr>
    <td>Alice</td>
    <td>Johnson</td>
    <td>Senator, OH</td>
    <td><a href="/search/view/paper/doc-uuid-001/">Annual Report for CY2022</a></td>
    <td>02/10/2023</td>
  </tr>
  <tr>
    <td>Bob</td>
    <td>Williams</td>
    <td>Senator, FL</td>
    <td><a href="/search/view/paper/doc-uuid-002/">PTR</a></td>
    <td>05/05/2023</td>
  </tr>
</tbody>
</table>
</body></html>
"""


class TestParseSenateIndexHtml:
    def test_returns_two_rows(self):
        rows = parse_senate_index(_SAMPLE_HTML, year=2022)
        assert len(rows) == 2

    def test_first_row_name(self):
        row = parse_senate_index(_SAMPLE_HTML, year=2022)[0]
        assert row.first_name == "Alice"
        assert row.last_name == "Johnson"

    def test_first_row_doc_id(self):
        row = parse_senate_index(_SAMPLE_HTML, year=2022)[0]
        assert row.doc_id == "doc-uuid-001"

    def test_second_row_doc_id(self):
        row = parse_senate_index(_SAMPLE_HTML, year=2022)[1]
        assert row.doc_id == "doc-uuid-002"

    def test_date_filed_preserved(self):
        row = parse_senate_index(_SAMPLE_HTML, year=2022)[0]
        assert row.date_filed == "02/10/2023"

    def test_filing_year_propagated(self):
        rows = parse_senate_index(_SAMPLE_HTML, year=2022)
        assert all(r.filing_year == 2022 for r in rows)

    def test_no_tbody_fallback(self):
        html = (
            "<table>"
            "<tr><td>Carol</td><td>King</td><td>Senator, NY</td>"
            '<td><a href="/search/view/paper/zzz-999/">Annual</a></td>'
            "<td>09/01/2023</td></tr>"
            "</table>"
        )
        rows = parse_senate_index(html, year=2023)
        assert len(rows) == 1
        assert rows[0].doc_id == "zzz-999"

    def test_empty_html_returns_empty(self):
        assert parse_senate_index("<html></html>", year=2023) == []

    def test_html_returns_senate_index_row_instances(self):
        rows = parse_senate_index(_SAMPLE_HTML, year=2022)
        assert all(isinstance(r, SenateIndexRow) for r in rows)


# ---------------------------------------------------------------------------
# fetch_senate_index
# ---------------------------------------------------------------------------


def _make_client(pages: list[dict]) -> MagicMock:
    """Return a mock httpx.Client that yields *pages* from the data endpoint.

    Call order: GET (session) → POST (agree) → POST (page 1) → POST (page 2) …
    """
    client = MagicMock()
    client.cookies = {"csrftoken": "testtoken"}

    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    client.get.return_value = get_resp

    agree_resp = MagicMock()
    agree_resp.raise_for_status = MagicMock()

    data_resps = []
    for page in pages:
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = page
        data_resps.append(r)

    client.post.side_effect = [agree_resp] + data_resps
    return client


class TestFetchSenateIndex:
    def test_single_page_returns_rows(self):
        client = _make_client([
            {"recordsTotal": 1, "data": [
                ["Eve", "Stone", "Senator, WA", _LINK_A, "01/01/2024"],
            ]},
        ])
        rows = fetch_senate_index(2023, client=client)
        assert len(rows) == 1
        assert rows[0].first_name == "Eve"

    def test_filing_year_on_fetched_rows(self):
        client = _make_client([
            {"recordsTotal": 1, "data": [
                ["Eve", "Stone", "Senator, WA", _LINK_A, "01/01/2024"],
            ]},
        ])
        rows = fetch_senate_index(2023, client=client)
        assert rows[0].filing_year == 2023

    def test_empty_result(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        assert fetch_senate_index(2023, client=client) == []

    def test_get_called_before_post(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        fetch_senate_index(2023, client=client)
        client.get.assert_called_once()
        get_url = client.get.call_args[0][0]
        assert "efdsearch.senate.gov" in get_url

    def test_year_embedded_in_data_post(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        fetch_senate_index(2024, client=client)
        # call_args_list[0] = agree POST, [1] = data POST
        data_kwargs = client.post.call_args_list[1].kwargs
        assert "2024" in data_kwargs["data"]["submitted_start_date"]
        assert "2024" in data_kwargs["data"]["submitted_end_date"]

    def test_provided_client_is_not_closed(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        fetch_senate_index(2023, client=client)
        client.close.assert_not_called()

    def test_pagination_stops_after_all_records(self):
        """With recordsTotal=1, only one data POST should be made."""
        client = _make_client([
            {"recordsTotal": 1, "data": [
                ["A", "B", "Senator, TX", _LINK_A, "01/01/2024"],
            ]},
        ])
        fetch_senate_index(2023, client=client)
        # agree + one data page = 2 total POSTs
        assert client.post.call_count == 2

    def test_agree_post_sent_to_search_home(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        fetch_senate_index(2023, client=client)
        agree_url = client.post.call_args_list[0][0][0]
        assert "efdsearch.senate.gov/search/" in agree_url

    def test_data_post_sent_to_data_endpoint(self):
        client = _make_client([{"recordsTotal": 0, "data": []}])
        fetch_senate_index(2023, client=client)
        data_url = client.post.call_args_list[1][0][0]
        assert "report/data" in data_url
