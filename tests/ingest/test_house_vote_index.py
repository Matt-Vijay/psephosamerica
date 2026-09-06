"""Tests for the House vote index layer — no network."""

from __future__ import annotations

import datetime
import textwrap
from unittest.mock import MagicMock

import pytest

from src.ingest.congress.house_vote_index import (
    HouseVoteIndexRow,
    fetch_house_vote_index,
    house_vote_index_url,
    parse_house_vote_index,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote-summary>
      <congress>118</congress>
      <session>1</session>
      <vote-total>
        <vote-number>1</vote-number>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date>09-Jan-2023</action-date>
      </vote-total>
    </vote-summary>
""")

MULTI_ENTRY_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote-summary>
      <congress>118</congress>
      <session>2</session>
      <vote-total>
        <vote-number>42</vote-number>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date>2024-03-15</action-date>
      </vote-total>
      <vote-total>
        <vote-number>43</vote-number>
        <vote-question>On Motion to Table</vote-question>
        <vote-result>Failed</vote-result>
        <action-date>2024-03-16</action-date>
      </vote-total>
      <vote-total>
        <vote-number>44</vote-number>
        <vote-question>On the Resolution</vote-question>
        <action-date>2024-03-17</action-date>
      </vote-total>
    </vote-summary>
""")

EMPTY_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote-summary>
      <congress>118</congress>
      <session>1</session>
    </vote-summary>
""")


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = resp
    return resp


# ---------------------------------------------------------------------------
# house_vote_index_url
# ---------------------------------------------------------------------------


class TestHouseVoteIndexUrl:
    def test_contains_year(self) -> None:
        url = house_vote_index_url(2023)
        assert "2023" in url

    def test_points_to_clerk_house_gov(self) -> None:
        url = house_vote_index_url(2024)
        assert "clerk.house.gov" in url

    def test_is_xml(self) -> None:
        url = house_vote_index_url(2025)
        assert url.endswith(".xml")

    def test_exact_url(self) -> None:
        assert house_vote_index_url(2023) == "https://clerk.house.gov/evs/2023/index.xml"


# ---------------------------------------------------------------------------
# parse_house_vote_index
# ---------------------------------------------------------------------------


class TestParseHouseVoteIndexSingleEntry:
    def setup_method(self) -> None:
        self.rows = parse_house_vote_index(MINIMAL_INDEX_XML)

    def test_returns_one_row(self) -> None:
        assert len(self.rows) == 1

    def test_row_type(self) -> None:
        assert isinstance(self.rows[0], HouseVoteIndexRow)

    def test_congress(self) -> None:
        assert self.rows[0].congress == 118

    def test_session(self) -> None:
        assert self.rows[0].session == 1

    def test_roll_call_number(self) -> None:
        assert self.rows[0].roll_call_number == 1

    def test_vote_date(self) -> None:
        assert self.rows[0].vote_date == datetime.date(2023, 1, 9)

    def test_question(self) -> None:
        assert self.rows[0].question == "On Passage"

    def test_result(self) -> None:
        assert self.rows[0].result == "Passed"

    def test_source_url_points_to_roll_call_xml(self) -> None:
        url = self.rows[0].source_url
        assert "clerk.house.gov" in url
        assert "roll001.xml" in url


class TestParseHouseVoteIndexMultiEntry:
    def setup_method(self) -> None:
        self.rows = parse_house_vote_index(MULTI_ENTRY_INDEX_XML)

    def test_returns_all_entries(self) -> None:
        assert len(self.rows) == 3

    def test_session_from_root(self) -> None:
        assert all(r.session == 2 for r in self.rows)

    def test_roll_call_numbers(self) -> None:
        numbers = [r.roll_call_number for r in self.rows]
        assert numbers == [42, 43, 44]

    def test_iso_date_parsing(self) -> None:
        assert self.rows[0].vote_date == datetime.date(2024, 3, 15)

    def test_consecutive_dates(self) -> None:
        dates = [r.vote_date for r in self.rows]
        assert dates == [
            datetime.date(2024, 3, 15),
            datetime.date(2024, 3, 16),
            datetime.date(2024, 3, 17),
        ]

    def test_missing_result_is_none(self) -> None:
        # Entry 44 has no <vote-result>
        row_44 = next(r for r in self.rows if r.roll_call_number == 44)
        assert row_44.result is None

    def test_source_urls_are_distinct(self) -> None:
        urls = [r.source_url for r in self.rows]
        assert len(set(urls)) == 3

    def test_source_url_encodes_roll_call_number(self) -> None:
        row_42 = next(r for r in self.rows if r.roll_call_number == 42)
        assert "roll042.xml" in row_42.source_url


class TestParseHouseVoteIndexEdgeCases:
    def test_empty_index_returns_empty_list(self) -> None:
        rows = parse_house_vote_index(EMPTY_INDEX_XML)
        assert rows == []

    def test_missing_congress_raises(self) -> None:
        xml = "<vote-summary><session>1</session></vote-summary>"
        with pytest.raises(ValueError, match="congress"):
            parse_house_vote_index(xml)

    def test_missing_session_raises(self) -> None:
        xml = "<vote-summary><congress>118</congress></vote-summary>"
        with pytest.raises(ValueError, match="session"):
            parse_house_vote_index(xml)

    def test_missing_vote_number_raises(self) -> None:
        xml = textwrap.dedent("""\
            <vote-summary>
              <congress>118</congress>
              <session>1</session>
              <vote-total>
                <vote-question>On Passage</vote-question>
                <action-date>2023-01-09</action-date>
              </vote-total>
            </vote-summary>
        """)
        with pytest.raises(ValueError):
            parse_house_vote_index(xml)

    def test_missing_action_date_raises(self) -> None:
        xml = textwrap.dedent("""\
            <vote-summary>
              <congress>118</congress>
              <session>1</session>
              <vote-total>
                <vote-number>1</vote-number>
                <vote-question>On Passage</vote-question>
              </vote-total>
            </vote-summary>
        """)
        with pytest.raises(ValueError):
            parse_house_vote_index(xml)

    def test_bad_date_format_raises(self) -> None:
        xml = textwrap.dedent("""\
            <vote-summary>
              <congress>118</congress>
              <session>1</session>
              <vote-total>
                <vote-number>1</vote-number>
                <vote-question>On Passage</vote-question>
                <action-date>not-a-date</action-date>
              </vote-total>
            </vote-summary>
        """)
        with pytest.raises(ValueError, match="Unparseable"):
            parse_house_vote_index(xml)

    def test_clerk_date_format_dd_mon_yyyy(self) -> None:
        xml = textwrap.dedent("""\
            <vote-summary>
              <congress>119</congress>
              <session>1</session>
              <vote-total>
                <vote-number>5</vote-number>
                <vote-question>On Motion</vote-question>
                <action-date>15-Mar-2025</action-date>
              </vote-total>
            </vote-summary>
        """)
        rows = parse_house_vote_index(xml)
        assert rows[0].vote_date == datetime.date(2025, 3, 15)


# ---------------------------------------------------------------------------
# fetch_house_vote_index
# ---------------------------------------------------------------------------


class TestFetchHouseVoteIndex:
    def test_requests_correct_url(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(MINIMAL_INDEX_XML)

        fetch_house_vote_index(2023, client=client)

        url = client.get.call_args[0][0]
        assert url == "https://clerk.house.gov/evs/2023/index.xml"

    def test_returns_parsed_rows(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(MINIMAL_INDEX_XML)

        rows = fetch_house_vote_index(2023, client=client)

        assert len(rows) == 1
        assert isinstance(rows[0], HouseVoteIndexRow)
        assert rows[0].congress == 118
        assert rows[0].roll_call_number == 1

    def test_raises_on_http_error(self) -> None:
        import httpx as _httpx

        client = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        client.get.return_value = resp

        with pytest.raises(_httpx.HTTPStatusError):
            fetch_house_vote_index(2023, client=client)

    def test_multi_entry_index(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(MULTI_ENTRY_INDEX_XML)

        rows = fetch_house_vote_index(2024, client=client)

        assert len(rows) == 3
        assert all(r.congress == 118 for r in rows)
