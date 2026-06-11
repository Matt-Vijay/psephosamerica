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


# Mirrors the live vote_menu_<congress>_<session>.xml shape: congress_year at the
# root, day-month vote dates, results under <result>. Row 2 keeps a full date and
# the legacy <vote_result> name to pin the compatibility fallbacks.
VOTE_SUMMARY_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote_summary>
      <congress>118</congress>
      <session>1</session>
      <congress_year>2023</congress_year>
      <votes>
        <vote>
          <vote_number>00001</vote_number>
          <vote_date>3-Jan</vote_date>
          <question>On the Nomination</question>
          <result>Confirmed</result>
        </vote>
        <vote>
          <vote_number>2</vote_number>
          <vote_date>January 5, 2023</vote_date>
          <question>On Passage of the Bill</question>
          <vote_result>Passed</vote_result>
        </vote>
        <vote>
          <vote_number>3</vote_number>
          <vote_date>7-Jan</vote_date>
          <question>On the Amendment</question>
          <result></result>
        </vote>
      </votes>
    </vote_summary>
""")


class TestSenateVoteIndexUrl:
    def test_url_contains_senate_gov(self) -> None:
        url = senate_vote_index_url(118, 1)
        assert "senate.gov" in url

    def test_url_is_the_roll_call_lists_menu(self) -> None:
        # The old roll_call_votes/.../vote_summary.xml path answers 200 with HTML.
        assert senate_vote_index_url(118, 1) == (
            "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_118_1.xml"
        )

    def test_url_session_2(self) -> None:
        url = senate_vote_index_url(118, 2)
        assert url.endswith("vote_menu_118_2.xml")

    def test_url_different_congress(self) -> None:
        url = senate_vote_index_url(119, 1)
        assert url.endswith("vote_menu_119_1.xml")


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

    def test_day_month_date_uses_congress_year(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[0].vote_date == datetime.date(2023, 1, 3)

    def test_full_date_still_parses(self) -> None:
        rows = parse_senate_vote_index(VOTE_SUMMARY_XML, congress=118, session=1)
        assert rows[1].vote_date == datetime.date(2023, 1, 5)

    def test_missing_congress_year_derives_session_year(self) -> None:
        # No <congress_year>: 119/1 -> 2025, 118/2 -> 2024.
        xml = textwrap.dedent("""\
            <vote_summary>
              <votes>
                <vote>
                  <vote_number>4</vote_number>
                  <vote_date>18-Dec</vote_date>
                  <question>On the Cloture Motion</question>
                  <result>Agreed to</result>
                </vote>
              </votes>
            </vote_summary>
        """)
        rows = parse_senate_vote_index(xml, congress=119, session=1)
        assert rows[0].vote_date == datetime.date(2025, 12, 18)
        rows = parse_senate_vote_index(xml, congress=118, session=2)
        assert rows[0].vote_date == datetime.date(2024, 12, 18)

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
        assert called_url.endswith("roll_call_lists/vote_menu_118_1.xml")

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
