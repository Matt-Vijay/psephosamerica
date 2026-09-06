"""Tests for Congress live vote fetch layer — no network; httpx mocked."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.live_votes import (
    fetch_house_vote,
    fetch_house_votes,
    fetch_senate_vote,
    fetch_senate_votes,
)
from src.ingest.congress.models import VoteCastRecord, VoteEventRecord

HOUSE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>118</congress>
        <session>1</session>
        <rollcall-num>42</rollcall-num>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date date="2023-02-01">1-Feb-2023</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="P000197" party="D" state="CA">Pelosi</legislator>
          <vote>Yea</vote>
        </recorded-vote>
        <recorded-vote>
          <legislator name-id="J000289" party="R" state="OH">Jordan</legislator>
          <vote>Nay</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

SENATE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>118</congress>
      <session>1</session>
      <vote_number>10</vote_number>
      <vote_question_text>On the Nomination</vote_question_text>
      <vote_result_text>Confirmed</vote_result_text>
      <vote_date>February 15, 2023</vote_date>
      <members>
        <member>
          <lis_member_id>S270</lis_member_id>
          <member_full>Schumer (D-NY)</member_full>
          <vote_cast>Yea</vote_cast>
        </member>
        <member>
          <lis_member_id>S174</lis_member_id>
          <member_full>McConnell (R-KY)</member_full>
          <vote_cast>Nay</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = resp
    return resp


# ===================================================================
# fetch_house_vote
# ===================================================================


class TestFetchHouseVote:
    def test_returns_event_and_casts(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(HOUSE_VOTE_XML)

        event, casts = fetch_house_vote(2023, 42, client=client)

        assert isinstance(event, VoteEventRecord)
        assert event.chamber == "house"
        assert event.congress == 118
        assert event.roll_call_number == 42
        assert event.result == "Passed"
        assert len(casts) == 2
        assert all(isinstance(c, VoteCastRecord) for c in casts)

    def test_requests_correct_url(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(HOUSE_VOTE_XML)

        fetch_house_vote(2023, 42, client=client)

        url = client.get.call_args[0][0]
        assert "clerk.house.gov/evs/2023/roll042.xml" in url

    def test_casts_have_bioguide_ids(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(HOUSE_VOTE_XML)

        _, casts = fetch_house_vote(2023, 42, client=client)

        assert all(c.bioguide_id is not None for c in casts)

    def test_http_error_propagates(self) -> None:
        import httpx

        client = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        client.get.return_value = resp

        with pytest.raises(httpx.HTTPStatusError):
            fetch_house_vote(2023, 42, client=client)


# ===================================================================
# fetch_senate_vote
# ===================================================================


class TestFetchSenateVote:
    def test_returns_event_and_casts(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(SENATE_VOTE_XML)

        event, casts = fetch_senate_vote(118, 1, 10, client=client)

        assert isinstance(event, VoteEventRecord)
        assert event.chamber == "senate"
        assert event.congress == 118
        assert event.roll_call_number == 10
        assert event.result == "Confirmed"
        assert len(casts) == 2

    def test_requests_correct_url(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(SENATE_VOTE_XML)

        fetch_senate_vote(118, 1, 10, client=client)

        url = client.get.call_args[0][0]
        assert "senate.gov" in url
        assert "vote1181" in url
        assert "vote_118_1_00010.xml" in url

    def test_casts_carry_lis_member_id(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(SENATE_VOTE_XML)

        _, casts = fetch_senate_vote(118, 1, 10, client=client)

        assert all(c.lis_member_id is not None for c in casts)
        assert all(c.bioguide_id is None for c in casts)


# ===================================================================
# fetch_house_vote / fetch_senate_vote — no-client (context manager) path
# ===================================================================


class TestFetchHouseVoteNoClient:
    """Single-vote fetch with no caller-supplied client creates and closes its own."""

    def test_result_returned_via_own_client(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(HOUSE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.ingest.congress.live_votes.httpx.Client", return_value=mock_client):
            event, casts = fetch_house_vote(2023, 42)

        assert isinstance(event, VoteEventRecord)
        assert len(casts) == 2

    def test_context_manager_exits_on_success(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(HOUSE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.ingest.congress.live_votes.httpx.Client", return_value=mock_client):
            fetch_house_vote(2023, 42)

        mock_client.__exit__.assert_called_once()

    def test_client_built_with_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(HOUSE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch(
            "src.ingest.congress.live_votes.httpx.Client", return_value=mock_client
        ) as cls_mock:
            fetch_house_vote(2023, 42)

        cls_mock.assert_called_once_with(timeout=30.0)


class TestFetchSenateVoteNoClient:
    """Single Senate vote fetch with no caller-supplied client creates and closes its own."""

    def test_result_returned_via_own_client(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(SENATE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.ingest.congress.live_votes.httpx.Client", return_value=mock_client):
            event, casts = fetch_senate_vote(118, 1, 10)

        assert isinstance(event, VoteEventRecord)
        assert len(casts) == 2

    def test_context_manager_exits_on_success(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(SENATE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.ingest.congress.live_votes.httpx.Client", return_value=mock_client):
            fetch_senate_vote(118, 1, 10)

        mock_client.__exit__.assert_called_once()

    def test_client_built_with_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(SENATE_VOTE_XML)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch(
            "src.ingest.congress.live_votes.httpx.Client", return_value=mock_client
        ) as cls_mock:
            fetch_senate_vote(118, 1, 10)

        cls_mock.assert_called_once_with(timeout=30.0)


# ===================================================================
# fetch_house_votes (batch)
# ===================================================================


class TestFetchHouseVotes:
    def test_returns_one_result_per_number(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(HOUSE_VOTE_XML)

        results = fetch_house_votes(2023, [42, 43], client=client)

        assert len(results) == 2
        assert client.get.call_count == 2
        for event, _casts in results:
            assert isinstance(event, VoteEventRecord)

    def test_empty_list(self) -> None:
        client = MagicMock()
        results = fetch_house_votes(2023, [], client=client)
        assert results == []
        client.get.assert_not_called()

    def test_reuses_provided_client(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(HOUSE_VOTE_XML)

        fetch_house_votes(2023, [1, 2, 3], client=client)

        assert client.get.call_count == 3
        # client.close must NOT be called — caller owns the client
        client.close.assert_not_called()

    def test_closes_own_client_after_batch(self) -> None:
        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = _mock_response(HOUSE_VOTE_XML)
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        with patch(
            "src.ingest.congress.live_votes.httpx.Client",
            return_value=mock_client_instance,
        ):
            fetch_house_votes(2023, [1])
            mock_client_instance.close.assert_called_once()


# ===================================================================
# fetch_senate_votes (batch)
# ===================================================================


class TestFetchSenateVotes:
    def test_returns_one_result_per_number(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(SENATE_VOTE_XML)

        results = fetch_senate_votes(118, 1, [10, 11], client=client)

        assert len(results) == 2
        assert client.get.call_count == 2

    def test_empty_list(self) -> None:
        client = MagicMock()
        results = fetch_senate_votes(118, 1, [], client=client)
        assert results == []
        client.get.assert_not_called()

    def test_reuses_provided_client(self) -> None:
        client = MagicMock()
        client.get.return_value = _mock_response(SENATE_VOTE_XML)

        fetch_senate_votes(118, 1, [10, 11, 12], client=client)

        assert client.get.call_count == 3
        client.close.assert_not_called()

    def test_closes_own_client_after_batch(self) -> None:
        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = _mock_response(SENATE_VOTE_XML)
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        with patch(
            "src.ingest.congress.live_votes.httpx.Client",
            return_value=mock_client_instance,
        ):
            fetch_senate_votes(118, 1, [10])
            mock_client_instance.close.assert_called_once()
