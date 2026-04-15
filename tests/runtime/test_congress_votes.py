"""Tests for the runtime vote-fetch layer.

No network calls: index fetch functions and live vote batch functions are mocked.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.models import VoteCastRecord, VoteEventRecord
from src.runtime.congress_votes import (
    VoteFetchResult,
    fetch_congress_vote_records,
    fetch_house_vote_records,
    fetch_senate_vote_records,
)


# ---------------------------------------------------------------------------
# Shared record builders
# ---------------------------------------------------------------------------


def _house_event(roll_call: int = 1) -> VoteEventRecord:
    return VoteEventRecord(
        chamber="house",
        congress=118,
        session_number=1,
        roll_call_number=roll_call,
        vote_date=datetime.date(2023, 2, 1),
        question="On Passage",
        result="Passed",
    )


def _house_cast(roll_call: int = 1, bioguide: str = "P000197") -> VoteCastRecord:
    return VoteCastRecord(
        chamber="house",
        congress=118,
        session_number=1,
        roll_call_number=roll_call,
        vote_option="yea",
        bioguide_id=bioguide,
    )


def _senate_event(vote_number: int = 10) -> VoteEventRecord:
    return VoteEventRecord(
        chamber="senate",
        congress=118,
        session_number=1,
        roll_call_number=vote_number,
        vote_date=datetime.date(2023, 2, 15),
        question="On the Nomination",
        result="Confirmed",
    )


def _senate_cast(vote_number: int = 10, lis_id: str = "S270") -> VoteCastRecord:
    return VoteCastRecord(
        chamber="senate",
        congress=118,
        session_number=1,
        roll_call_number=vote_number,
        vote_option="yea",
        lis_member_id=lis_id,
    )


def _house_index_row(roll_call_number: int) -> MagicMock:
    row = MagicMock()
    row.roll_call_number = roll_call_number
    return row


def _senate_index_row(vote_number: int) -> MagicMock:
    row = MagicMock()
    row.vote_number = vote_number
    return row


# ---------------------------------------------------------------------------
# VoteFetchResult
# ---------------------------------------------------------------------------


class TestVoteFetchResult:
    def test_exposes_vote_events(self) -> None:
        events = [_house_event()]
        result = VoteFetchResult(vote_events=events, vote_casts=[])
        assert result.vote_events is events

    def test_exposes_vote_casts(self) -> None:
        casts = [_house_cast()]
        result = VoteFetchResult(vote_events=[], vote_casts=casts)
        assert result.vote_casts is casts

    def test_is_frozen(self) -> None:
        result = VoteFetchResult(vote_events=[], vote_casts=[])
        with pytest.raises(Exception):
            result.vote_events = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# fetch_house_vote_records — explicit roll_call_numbers
# ---------------------------------------------------------------------------


class TestFetchHouseVoteRecordsExplicit:
    def test_returns_vote_fetch_result(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[(_house_event(1), [_house_cast(1)])],
        ):
            result = fetch_house_vote_records(2023, roll_call_numbers=[1])

        assert isinstance(result, VoteFetchResult)

    def test_events_accumulated_in_order(self) -> None:
        e1, e2 = _house_event(1), _house_event(2)

        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[(e1, []), (e2, [])],
        ):
            result = fetch_house_vote_records(2023, roll_call_numbers=[1, 2])

        assert result.vote_events == [e1, e2]

    def test_casts_flattened_across_votes(self) -> None:
        c1 = _house_cast(1, "A000001")
        c2 = _house_cast(1, "B000002")
        c3 = _house_cast(2, "C000003")

        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[(_house_event(1), [c1, c2]), (_house_event(2), [c3])],
        ):
            result = fetch_house_vote_records(2023, roll_call_numbers=[1, 2])

        assert result.vote_casts == [c1, c2, c3]

    def test_index_not_called_when_numbers_given(self) -> None:
        with (
            patch("src.runtime.congress_votes.fetch_house_vote_index") as mock_index,
            patch("src.runtime.congress_votes.fetch_house_votes", return_value=[]),
        ):
            fetch_house_vote_records(2023, roll_call_numbers=[42])
            mock_index.assert_not_called()

    def test_passes_year_and_numbers_to_fetch(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[],
        ) as mock_fetch:
            fetch_house_vote_records(2023, roll_call_numbers=[10, 20, 30])
            mock_fetch.assert_called_once_with(2023, [10, 20, 30], client=None)

    def test_passes_client_to_fetch(self) -> None:
        client = MagicMock()

        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[],
        ) as mock_fetch:
            fetch_house_vote_records(2023, roll_call_numbers=[1], client=client)
            mock_fetch.assert_called_once_with(2023, [1], client=client)

    def test_empty_numbers_returns_empty_result(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_house_votes",
            return_value=[],
        ):
            result = fetch_house_vote_records(2023, roll_call_numbers=[])

        assert result.vote_events == []
        assert result.vote_casts == []


# ---------------------------------------------------------------------------
# fetch_house_vote_records — index-driven (roll_call_numbers=None)
# ---------------------------------------------------------------------------


class TestFetchHouseVoteRecordsFromIndex:
    def test_calls_index_for_year(self) -> None:
        with (
            patch(
                "src.runtime.congress_votes.fetch_house_vote_index",
                return_value=[],
            ) as mock_index,
            patch("src.runtime.congress_votes.fetch_house_votes", return_value=[]),
        ):
            fetch_house_vote_records(2023)
            mock_index.assert_called_once_with(2023, client=None)

    def test_index_roll_call_numbers_drive_fetch(self) -> None:
        rows = [_house_index_row(5), _house_index_row(7)]

        with (
            patch(
                "src.runtime.congress_votes.fetch_house_vote_index",
                return_value=rows,
            ),
            patch(
                "src.runtime.congress_votes.fetch_house_votes",
                return_value=[],
            ) as mock_fetch,
        ):
            fetch_house_vote_records(2023)
            mock_fetch.assert_called_once_with(2023, [5, 7], client=None)

    def test_empty_index_returns_empty_result(self) -> None:
        with (
            patch("src.runtime.congress_votes.fetch_house_vote_index", return_value=[]),
            patch("src.runtime.congress_votes.fetch_house_votes", return_value=[]),
        ):
            result = fetch_house_vote_records(2023)

        assert result.vote_events == []
        assert result.vote_casts == []


class TestFetchCongressVoteRecords:
    def test_returns_empty_when_both_periods_omitted(self) -> None:
        result = fetch_congress_vote_records(119)
        assert result.vote_events == []
        assert result.vote_casts == []

    def test_combines_house_and_senate_results(self) -> None:
        house_result = VoteFetchResult(vote_events=[_house_event(1)], vote_casts=[_house_cast(1)])
        senate_result = VoteFetchResult(vote_events=[_senate_event(2)], vote_casts=[_senate_cast(2)])

        with (
            patch("src.runtime.congress_votes.fetch_house_vote_records", return_value=house_result),
            patch("src.runtime.congress_votes.fetch_senate_vote_records", return_value=senate_result),
        ):
            result = fetch_congress_vote_records(119, house_vote_year=2025, senate_session=1)

        assert result.vote_events == [_house_event(1), _senate_event(2)]
        assert result.vote_casts == [_house_cast(1), _senate_cast(2)]

    def test_passes_client_to_index(self) -> None:
        client = MagicMock()

        with (
            patch(
                "src.runtime.congress_votes.fetch_house_vote_index",
                return_value=[],
            ) as mock_index,
            patch("src.runtime.congress_votes.fetch_house_votes", return_value=[]),
        ):
            fetch_house_vote_records(2023, client=client)
            mock_index.assert_called_once_with(2023, client=client)

    def test_passes_client_to_fetch_from_index(self) -> None:
        client = MagicMock()
        rows = [_house_index_row(1)]

        with (
            patch(
                "src.runtime.congress_votes.fetch_house_vote_index",
                return_value=rows,
            ),
            patch(
                "src.runtime.congress_votes.fetch_house_votes",
                return_value=[],
            ) as mock_fetch,
        ):
            fetch_house_vote_records(2023, client=client)
            mock_fetch.assert_called_once_with(2023, [1], client=client)


# ---------------------------------------------------------------------------
# fetch_senate_vote_records — explicit vote_numbers
# ---------------------------------------------------------------------------


class TestFetchSenateVoteRecordsExplicit:
    def test_returns_vote_fetch_result(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[(_senate_event(10), [_senate_cast(10)])],
        ):
            result = fetch_senate_vote_records(118, 1, vote_numbers=[10])

        assert isinstance(result, VoteFetchResult)

    def test_events_accumulated_in_order(self) -> None:
        e1, e2 = _senate_event(10), _senate_event(11)

        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[(e1, []), (e2, [])],
        ):
            result = fetch_senate_vote_records(118, 1, vote_numbers=[10, 11])

        assert result.vote_events == [e1, e2]

    def test_casts_flattened_across_votes(self) -> None:
        c1 = _senate_cast(10, "S270")
        c2 = _senate_cast(10, "S174")
        c3 = _senate_cast(11, "S001")

        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[(_senate_event(10), [c1, c2]), (_senate_event(11), [c3])],
        ):
            result = fetch_senate_vote_records(118, 1, vote_numbers=[10, 11])

        assert result.vote_casts == [c1, c2, c3]

    def test_index_not_called_when_numbers_given(self) -> None:
        with (
            patch("src.runtime.congress_votes.fetch_senate_vote_index") as mock_index,
            patch("src.runtime.congress_votes.fetch_senate_votes", return_value=[]),
        ):
            fetch_senate_vote_records(118, 1, vote_numbers=[10])
            mock_index.assert_not_called()

    def test_passes_congress_session_and_numbers_to_fetch(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[],
        ) as mock_fetch:
            fetch_senate_vote_records(118, 1, vote_numbers=[10, 20])
            mock_fetch.assert_called_once_with(118, 1, [10, 20], client=None)

    def test_passes_client_to_fetch(self) -> None:
        client = MagicMock()

        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[],
        ) as mock_fetch:
            fetch_senate_vote_records(118, 1, vote_numbers=[10], client=client)
            mock_fetch.assert_called_once_with(118, 1, [10], client=client)

    def test_empty_numbers_returns_empty_result(self) -> None:
        with patch(
            "src.runtime.congress_votes.fetch_senate_votes",
            return_value=[],
        ):
            result = fetch_senate_vote_records(118, 1, vote_numbers=[])

        assert result.vote_events == []
        assert result.vote_casts == []


# ---------------------------------------------------------------------------
# fetch_senate_vote_records — index-driven (vote_numbers=None)
# ---------------------------------------------------------------------------


class TestFetchSenateVoteRecordsFromIndex:
    def test_calls_index_for_congress_and_session(self) -> None:
        with (
            patch(
                "src.runtime.congress_votes.fetch_senate_vote_index",
                return_value=[],
            ) as mock_index,
            patch("src.runtime.congress_votes.fetch_senate_votes", return_value=[]),
        ):
            fetch_senate_vote_records(118, 1)
            mock_index.assert_called_once_with(118, 1, client=None)

    def test_index_vote_numbers_drive_fetch(self) -> None:
        rows = [_senate_index_row(10), _senate_index_row(11)]

        with (
            patch(
                "src.runtime.congress_votes.fetch_senate_vote_index",
                return_value=rows,
            ),
            patch(
                "src.runtime.congress_votes.fetch_senate_votes",
                return_value=[],
            ) as mock_fetch,
        ):
            fetch_senate_vote_records(118, 1)
            mock_fetch.assert_called_once_with(118, 1, [10, 11], client=None)

    def test_empty_index_returns_empty_result(self) -> None:
        with (
            patch("src.runtime.congress_votes.fetch_senate_vote_index", return_value=[]),
            patch("src.runtime.congress_votes.fetch_senate_votes", return_value=[]),
        ):
            result = fetch_senate_vote_records(118, 1)

        assert result.vote_events == []
        assert result.vote_casts == []

    def test_passes_client_to_index(self) -> None:
        client = MagicMock()

        with (
            patch(
                "src.runtime.congress_votes.fetch_senate_vote_index",
                return_value=[],
            ) as mock_index,
            patch("src.runtime.congress_votes.fetch_senate_votes", return_value=[]),
        ):
            fetch_senate_vote_records(118, 1, client=client)
            mock_index.assert_called_once_with(118, 1, client=client)

    def test_passes_client_to_fetch_from_index(self) -> None:
        client = MagicMock()
        rows = [_senate_index_row(10)]

        with (
            patch(
                "src.runtime.congress_votes.fetch_senate_vote_index",
                return_value=rows,
            ),
            patch(
                "src.runtime.congress_votes.fetch_senate_votes",
                return_value=[],
            ) as mock_fetch,
        ):
            fetch_senate_vote_records(118, 1, client=client)
            mock_fetch.assert_called_once_with(118, 1, [10], client=client)
