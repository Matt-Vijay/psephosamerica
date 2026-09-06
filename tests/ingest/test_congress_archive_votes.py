"""Tests for archive_votes local loading — no network calls."""

from __future__ import annotations

import datetime
import textwrap
from pathlib import Path

import pytest

from src.ingest.congress.archive_votes import (
    VoteArchiveResult,
    load_house_vote_records,
    load_senate_vote_records,
)
from src.ingest.congress.models import VoteCastRecord, VoteEventRecord

# ---------------------------------------------------------------------------
# Minimal XML fixtures
# ---------------------------------------------------------------------------

HOUSE_INDEX_XML = textwrap.dedent("""\
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
      <vote-total>
        <vote-number>2</vote-number>
        <vote-question>On the Resolution</vote-question>
        <vote-result>Failed</vote-result>
        <action-date>10-Jan-2023</action-date>
      </vote-total>
    </vote-summary>
""")

HOUSE_VOTE_1_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>118</congress>
        <session>1</session>
        <rollcall-num>1</rollcall-num>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date date="2023-01-09">January 9, 2023</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="A000001">Adams</legislator>
          <vote>Yea</vote>
        </recorded-vote>
        <recorded-vote>
          <legislator name-id="B000002">Baker</legislator>
          <vote>Nay</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

HOUSE_VOTE_2_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>118</congress>
        <session>1</session>
        <rollcall-num>2</rollcall-num>
        <vote-question>On the Resolution</vote-question>
        <vote-result>Failed</vote-result>
        <action-date date="2023-01-10">January 10, 2023</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="C000003">Clark</legislator>
          <vote>Yea</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

SENATE_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <vote_summary>
      <congress>118</congress>
      <session>1</session>
      <votes>
        <vote>
          <vote_number>1</vote_number>
          <vote_date>January 3, 2023</vote_date>
          <question>On the Nomination</question>
          <vote_result>Confirmed</vote_result>
        </vote>
        <vote>
          <vote_number>2</vote_number>
          <vote_date>January 5, 2023</vote_date>
          <question>On Passage of the Bill</question>
          <vote_result>Passed</vote_result>
        </vote>
      </votes>
    </vote_summary>
""")

SENATE_VOTE_1_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>118</congress>
      <session>1</session>
      <vote_number>1</vote_number>
      <vote_question_text>On the Nomination</vote_question_text>
      <vote_result_text>Confirmed</vote_result_text>
      <vote_date>2023-01-03</vote_date>
      <members>
        <member>
          <lis_member_id>S001</lis_member_id>
          <vote_cast>Yea</vote_cast>
        </member>
        <member>
          <lis_member_id>S002</lis_member_id>
          <vote_cast>Nay</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")

SENATE_VOTE_2_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>118</congress>
      <session>1</session>
      <vote_number>2</vote_number>
      <vote_question_text>On Passage of the Bill</vote_question_text>
      <vote_result_text>Passed</vote_result_text>
      <vote_date>2023-01-05</vote_date>
      <members>
        <member>
          <lis_member_id>S003</lis_member_id>
          <vote_cast>Yea</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


# ---------------------------------------------------------------------------
# Fixtures: write archive files to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def house_archive(tmp_path: Path) -> Path:
    """Populate a minimal two-vote House archive and return the root dir."""
    year_dir = tmp_path / "house" / "2023"
    year_dir.mkdir(parents=True)
    (year_dir / "index.xml").write_text(HOUSE_INDEX_XML, encoding="utf-8")
    (year_dir / "roll001.xml").write_text(HOUSE_VOTE_1_XML, encoding="utf-8")
    (year_dir / "roll002.xml").write_text(HOUSE_VOTE_2_XML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def house_archive_one_missing(tmp_path: Path) -> Path:
    """House archive where roll002.xml is absent."""
    year_dir = tmp_path / "house" / "2023"
    year_dir.mkdir(parents=True)
    (year_dir / "index.xml").write_text(HOUSE_INDEX_XML, encoding="utf-8")
    (year_dir / "roll001.xml").write_text(HOUSE_VOTE_1_XML, encoding="utf-8")
    # roll002.xml intentionally omitted
    return tmp_path


@pytest.fixture()
def senate_archive(tmp_path: Path) -> Path:
    """Populate a minimal two-vote Senate archive and return the root dir."""
    prefix = "vote1181"
    sess_dir = tmp_path / "senate" / prefix
    sess_dir.mkdir(parents=True)
    (sess_dir / "vote_summary.xml").write_text(SENATE_INDEX_XML, encoding="utf-8")
    (sess_dir / "vote_118_1_00001.xml").write_text(SENATE_VOTE_1_XML, encoding="utf-8")
    (sess_dir / "vote_118_1_00002.xml").write_text(SENATE_VOTE_2_XML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def senate_archive_one_missing(tmp_path: Path) -> Path:
    """Senate archive where vote_118_1_00002.xml is absent."""
    prefix = "vote1181"
    sess_dir = tmp_path / "senate" / prefix
    sess_dir.mkdir(parents=True)
    (sess_dir / "vote_summary.xml").write_text(SENATE_INDEX_XML, encoding="utf-8")
    (sess_dir / "vote_118_1_00001.xml").write_text(SENATE_VOTE_1_XML, encoding="utf-8")
    # vote_118_1_00002.xml intentionally omitted
    return tmp_path


# ---------------------------------------------------------------------------
# load_house_vote_records — full archive
# ---------------------------------------------------------------------------


class TestLoadHouseVoteRecordsFull:
    def setup_method(self) -> None:
        pass

    def test_returns_archive_result(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert isinstance(result, VoteArchiveResult)

    def test_event_count(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert len(result.events) == 2

    def test_cast_count(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        # roll001: 2 casts, roll002: 1 cast
        assert len(result.casts) == 3

    def test_no_missing_roll_calls(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert result.missing_roll_calls == []

    def test_events_are_vote_event_records(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert all(isinstance(e, VoteEventRecord) for e in result.events)

    def test_casts_are_vote_cast_records(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert all(isinstance(c, VoteCastRecord) for c in result.casts)

    def test_event_chamber_is_house(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert all(e.chamber == "house" for e in result.events)

    def test_cast_chamber_is_house(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert all(c.chamber == "house" for c in result.casts)

    def test_roll_call_numbers(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        numbers = sorted(e.roll_call_number for e in result.events)
        assert numbers == [1, 2]

    def test_bioguide_ids_present(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        assert all(c.bioguide_id is not None for c in result.casts)

    def test_bioguide_ids_from_roll001(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        ids_roll1 = {c.bioguide_id for c in result.casts if c.roll_call_number == 1}
        assert ids_roll1 == {"A000001", "B000002"}

    def test_vote_options_normalized(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        options = {c.vote_option for c in result.casts}
        assert options <= {"yea", "nay", "present", "not_voting"}

    def test_accepts_str_archive_dir(self, house_archive: Path) -> None:
        result = load_house_vote_records(str(house_archive), 2023)
        assert len(result.events) == 2

    def test_event_vote_date_roll001(self, house_archive: Path) -> None:
        result = load_house_vote_records(house_archive, 2023)
        ev = next(e for e in result.events if e.roll_call_number == 1)
        assert ev.vote_date == datetime.date(2023, 1, 9)


# ---------------------------------------------------------------------------
# load_house_vote_records — missing individual vote file
# ---------------------------------------------------------------------------


class TestLoadHouseVoteRecordsMissing:
    def test_missing_roll_call_reported(self, house_archive_one_missing: Path) -> None:
        result = load_house_vote_records(house_archive_one_missing, 2023)
        assert 2 in result.missing_roll_calls

    def test_present_vote_still_loaded(self, house_archive_one_missing: Path) -> None:
        result = load_house_vote_records(house_archive_one_missing, 2023)
        assert len(result.events) == 1
        assert result.events[0].roll_call_number == 1

    def test_cast_count_reflects_only_present(self, house_archive_one_missing: Path) -> None:
        result = load_house_vote_records(house_archive_one_missing, 2023)
        assert len(result.casts) == 2


# ---------------------------------------------------------------------------
# load_house_vote_records — missing index raises
# ---------------------------------------------------------------------------


class TestLoadHouseVoteRecordsNoIndex:
    def test_missing_index_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_house_vote_records(tmp_path, 2023)


# ---------------------------------------------------------------------------
# load_senate_vote_records — full archive
# ---------------------------------------------------------------------------


class TestLoadSenateVoteRecordsFull:
    def test_returns_archive_result(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert isinstance(result, VoteArchiveResult)

    def test_event_count(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert len(result.events) == 2

    def test_cast_count(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        # vote 1: 2 members, vote 2: 1 member
        assert len(result.casts) == 3

    def test_no_missing_roll_calls(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert result.missing_roll_calls == []

    def test_events_are_vote_event_records(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert all(isinstance(e, VoteEventRecord) for e in result.events)

    def test_casts_are_vote_cast_records(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert all(isinstance(c, VoteCastRecord) for c in result.casts)

    def test_event_chamber_is_senate(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert all(e.chamber == "senate" for e in result.events)

    def test_cast_chamber_is_senate(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert all(c.chamber == "senate" for c in result.casts)

    def test_roll_call_numbers(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        numbers = sorted(e.roll_call_number for e in result.events)
        assert numbers == [1, 2]

    def test_lis_member_ids_present(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        assert all(c.lis_member_id is not None for c in result.casts)

    def test_lis_member_ids_from_vote1(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        ids_v1 = {c.lis_member_id for c in result.casts if c.roll_call_number == 1}
        assert ids_v1 == {"S001", "S002"}

    def test_vote_options_normalized(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        options = {c.vote_option for c in result.casts}
        assert options <= {"yea", "nay", "present", "not_voting"}

    def test_accepts_str_archive_dir(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(str(senate_archive), 118, 1)
        assert len(result.events) == 2

    def test_event_vote_date_vote1(self, senate_archive: Path) -> None:
        result = load_senate_vote_records(senate_archive, 118, 1)
        ev = next(e for e in result.events if e.roll_call_number == 1)
        assert ev.vote_date == datetime.date(2023, 1, 3)


# ---------------------------------------------------------------------------
# load_senate_vote_records — missing individual vote file
# ---------------------------------------------------------------------------


class TestLoadSenateVoteRecordsMissing:
    def test_missing_vote_reported(self, senate_archive_one_missing: Path) -> None:
        result = load_senate_vote_records(senate_archive_one_missing, 118, 1)
        assert 2 in result.missing_roll_calls

    def test_present_vote_still_loaded(self, senate_archive_one_missing: Path) -> None:
        result = load_senate_vote_records(senate_archive_one_missing, 118, 1)
        assert len(result.events) == 1
        assert result.events[0].roll_call_number == 1

    def test_cast_count_reflects_only_present(self, senate_archive_one_missing: Path) -> None:
        result = load_senate_vote_records(senate_archive_one_missing, 118, 1)
        assert len(result.casts) == 2


# ---------------------------------------------------------------------------
# load_senate_vote_records — missing index raises
# ---------------------------------------------------------------------------


class TestLoadSenateVoteRecordsNoIndex:
    def test_missing_index_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_senate_vote_records(tmp_path, 118, 1)
