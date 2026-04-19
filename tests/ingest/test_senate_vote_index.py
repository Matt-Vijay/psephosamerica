"""Tests for the Senate vote index layer.

No network calls.  All XML is inline.
"""

from __future__ import annotations

import datetime
import textwrap
from unittest.mock import MagicMock

import pytest

from src.ingest.congress.senate_vote_index import (
    SenateVoteIndexRow,
    fetch_senate_vote_index,
    parse_senate_vote_index,
    senate_vote_index_url,
)


VOTE_SUMMARY_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote_summary>
      <congress>118</congress>
      <session>1</session>
      <votes>
        <vote>
          <vote_number>1</vote_number>
          <vote_date>January 3, 2023, 12:15 PM</vote_date>
          <question>On the Nomination</question>
          <vote_result>Confirmed</vote_result>
        </vote>
        <vote>
          <vote_number>2</vote_number>
          <vote_date>January 5, 2023</vote_date>
          <question>On Passage of the Bill</question>
          <vote_result>Passed</vote_result>
        </vote>
        <vote>
          <vote_number>3</vote_number>
          <vote_date>January 7, 2023</vote_date>
          <question>On the Amendment</question>
          <vote_result></vote_result>
        </vote>
      </votes>
    </vote_summary>
""")


class TestSenateVoteIndexUrl:
    def test_url_contains_senate_gov(self) -> None:
        url = senate_vote_index_url(118, 1)
        assert "senate.gov" in url

    def test_url_encodes_congress_and_session(self) -> None:
        url = senate_vote_index_url(118, 1)
        assert "vote1181" in url

    def test_url_points_to_vote_summary_xml(self) -> None:
        url = senate_vote_index_url(118, 1)
        assert url.endswith("vote_summary.xml")

    def test_url_session_2(self) -> None:
        url = senate_vote_index_url(118, 2)
        assert "vote1182" in url

    def test_url_different_congress(self) -> None:
        url = senate_vote_index_url(119, 1)
        assert "vote1191" in url


class TestParseSenateVoteIndex:
    def test_row_count(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert len(rows) == 3

    def test_all_rows_are_index_rows(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        for r in rows:
            assert isinstance(r, SenateVoteIndexRow)

    def test_congress_and_session_come_from_args(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert all(r.congress == 118 for r in rows)
        assert all(r.session == 1 for r in rows)

    def test_vote_numbers_in_order(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert [r.vote_number for r in rows] == [1, 2, 3]

    def test_date_with_time_suffix_strips_time(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[0].vote_date == datetime.date(2023, 1, 3)

    def test_date_without_time_suffix(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[1].vote_date == datetime.date(2023, 1, 5)

    def test_question_text(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[0].question == "On the Nomination"
        assert rows[1].question == "On Passage of the Bill"

    def test_result_present(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[0].result == "Confirmed"
        assert rows[1].result == "Passed"

    def test_empty_result_is_none(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[2].result is None

    def test_source_url_is_individual_roll_call_xml(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert "senate.gov" in rows[0].source_url
        assert "vote_118_1_00001.xml" in rows[0].source_url

    def test_source_urls_differ_by_vote_number(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[0].source_url != rows[1].source_url
        assert "vote_118_1_00002.xml" in rows[1].source_url

    def test_missing_votes_element_returns_empty(self) -> None:
        xml = "<vote_summary></vote_summary>"
        rows = parse_senate_vote_index(xml, congress=118, session=1)
        assert rows == []

    def test_empty_votes_element_returns_empty(self) -> None:
        xml = "<vote_summary><votes></votes></vote_summary>"
        rows = parse_senate_vote_index(xml, congress=118, session=1)
        assert rows == []

    def test_vote_without_number_is_skipped(self) -> None:
        xml = textwrap.dedent("""\
            <vote_summary>
              <votes>
                <vote>
                  <vote_date>January 3, 2023</vote_date>
                  <question>Missing number</question>
                  <vote_result>Passed</vote_result>
                </vote>
                <vote>
                  <vote_number>5</vote_number>
                  <vote_date>January 4, 2023</vote_date>
                  <question>Has number</question>
                  <vote_result>Passed</vote_result>
                </vote>
              </votes>
            </vote_summary>
        """)
        rows = parse_senate_vote_index(xml, congress=118, session=1)
        assert len(rows) == 1
        assert rows[0].vote_number == 5

    def test_missing_vote_date_raises_value_error(self) -> None:
        xml = textwrap.dedent("""\
            <vote_summary>
              <votes>
                <vote>
                  <vote_number>5</vote_number>
                  <vote_date></vote_date>
                  <question>Missing date</question>
                  <vote_result>Passed</vote_result>
                </vote>
              </votes>
            </vote_summary>
        """)
        with pytest.raises(ValueError, match="vote_date"):
            parse_senate_vote_index(xml, congress=118, session=1)

    def test_unparseable_vote_date_raises_value_error(self) -> None:
        xml = textwrap.dedent("""\
            <vote_summary>
              <votes>
                <vote>
                  <vote_number>5</vote_number>
                  <vote_date>not-a-date</vote_date>
                  <question>Bad date</question>
                  <vote_result>Passed</vote_result>
                </vote>
              </votes>
            </vote_summary>
        """)
        with pytest.raises(ValueError, match="vote_date"):
            parse_senate_vote_index(xml, congress=118, session=1)


class TestFetchSenateVoteIndex:
    def _mock_client(self, xml: str) -> MagicMock:
        response = MagicMock()
        response.text = xml
        response.raise_for_status.return_value = response
        client = MagicMock()
        client.get.return_value = response
        return client

    def test_fetches_correct_url(self) -> None:
        client = self._mock_client(VOTE_SUMMARY_XML)
        fetch_senate_vote_index(118, 1, client=client)
        called_url = client.get.call_args[0][0]
        assert "vote1181" in called_url
        assert called_url.endswith("vote_summary.xml")

    def test_returns_parsed_rows(self) -> None:
        client = self._mock_client(VOTE_SUMMARY_XML)
        rows = fetch_senate_vote_index(118, 1, client=client)
        assert len(rows) == 3
        assert all(isinstance(r, SenateVoteIndexRow) for r in rows)

    def test_congress_session_propagated(self) -> None:
        client = self._mock_client(VOTE_SUMMARY_XML)
        rows = fetch_senate_vote_index(118, 2, client=client)
        assert all(r.congress == 118 for r in rows)
        assert all(r.session == 2 for r in rows)
