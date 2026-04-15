"""Tests for src/runtime/congress_live.py.

No live network calls.  The CongressAPIClient and run_congress_load_runtime
boundaries are both mocked.

Covers:
  - client is constructed with settings.congress_api_key
  - members, committees, bills are fetched for the given congress
  - cosponsors are fetched for every bill returned
  - CongressIngestInputs is built with the fetched data and empty vote lists
  - result is the return value of run_congress_load_runtime
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.ingest.congress.models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord
from src.runtime.congress_live import run_live_congress_load


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _member(bioguide_id: str = "A000001") -> MemberRecord:
    return MemberRecord(
        bioguide_id=bioguide_id,
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        chamber="house",
    )


def _committee(code: str = "hsag00") -> CommitteeRecord:
    return CommitteeRecord(
        committee_code=code,
        congress=119,
        chamber="house",
        committee_type="standing",
        name="Agriculture",
    )


def _bill(bill_type: str = "hr", bill_number: int = 1) -> BillRecord:
    return BillRecord(
        congress=119,
        bill_type=bill_type,
        bill_number=bill_number,
        title="A bill",
    )


def _cosponsor(bill_type: str = "hr", bill_number: int = 1) -> CosponsorRecord:
    return CosponsorRecord(
        congress=119,
        bill_type=bill_type,
        bill_number=bill_number,
        bioguide_id="B000001",
    )


def _settings(api_key: str = "test-key") -> Any:
    s = MagicMock()
    s.congress_api_key = api_key
    return s


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_client_built_with_api_key(self):
        conn = MagicMock()
        settings = _settings(api_key="mykey")

        with (
            patch(
                "src.runtime.congress_live.CongressAPIClient",
                autospec=True,
            ) as MockClient,
            patch("src.runtime.congress_live.run_congress_load_runtime"),
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter([])
            instance.iter_committees.return_value = iter([])
            instance.iter_bills.return_value = iter([])

            run_live_congress_load(conn, settings, congress=119)

        MockClient.assert_called_once_with("mykey")


# ---------------------------------------------------------------------------
# Fetch calls
# ---------------------------------------------------------------------------


class TestFetchCalls:
    def _run(self, members=None, committees=None, bills=None, cosponsors=None):
        conn = MagicMock()
        settings = _settings()
        members = members or []
        committees = committees or []
        bills = bills or []
        cosponsors = cosponsors or []

        with (
            patch(
                "src.runtime.congress_live.CongressAPIClient",
                autospec=True,
            ) as MockClient,
            patch(
                "src.runtime.congress_live.run_congress_load_runtime",
                return_value=MagicMock(),
            ),
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter(members)
            instance.iter_committees.return_value = iter(committees)
            instance.iter_bills.return_value = iter(bills)
            instance.iter_cosponsors.return_value = iter(cosponsors)

            run_live_congress_load(conn, settings, congress=119)

        return instance

    def test_members_fetched_for_congress(self):
        client = self._run()
        client.iter_members.assert_called_once_with(119)

    def test_committees_fetched_for_congress(self):
        client = self._run()
        client.iter_committees.assert_called_once_with(119)

    def test_bills_fetched_for_congress(self):
        client = self._run()
        client.iter_bills.assert_called_once_with(119)

    def test_cosponsors_fetched_per_bill(self):
        bill_a = _bill("hr", 1)
        bill_b = _bill("s", 2)
        client = self._run(bills=[bill_a, bill_b])

        assert client.iter_cosponsors.call_count == 2
        client.iter_cosponsors.assert_any_call(119, "hr", 1)
        client.iter_cosponsors.assert_any_call(119, "s", 2)

    def test_no_cosponsor_calls_when_no_bills(self):
        client = self._run(bills=[])
        client.iter_cosponsors.assert_not_called()


# ---------------------------------------------------------------------------
# CongressIngestInputs wiring
# ---------------------------------------------------------------------------


class TestIngestInputsWiring:
    def _capture_inputs(self, members=None, committees=None, bills=None, cosponsors=None):
        conn = MagicMock()
        settings = _settings()

        captured: list = []

        def _capture(c, inputs):
            captured.append(inputs)
            return MagicMock()

        with (
            patch("src.runtime.congress_live.CongressAPIClient", autospec=True) as MockClient,
            patch("src.runtime.congress_live.run_congress_load_runtime", side_effect=_capture),
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter(members or [])
            instance.iter_committees.return_value = iter(committees or [])
            instance.iter_bills.return_value = iter(bills or [])
            instance.iter_cosponsors.return_value = iter(cosponsors or [])

            run_live_congress_load(conn, settings, congress=119)

        return captured[0]

    def test_members_in_inputs(self):
        m = _member()
        inputs = self._capture_inputs(members=[m])
        assert inputs.members == [m]

    def test_committees_in_inputs(self):
        c = _committee()
        inputs = self._capture_inputs(committees=[c])
        assert inputs.committees == [c]

    def test_bills_in_inputs(self):
        b = _bill()
        inputs = self._capture_inputs(bills=[b])
        assert inputs.bills == [b]

    def test_cosponsors_in_inputs(self):
        b = _bill()
        cs = _cosponsor()
        inputs = self._capture_inputs(bills=[b], cosponsors=[cs])
        assert inputs.cosponsors == [cs]

    def test_vote_events_empty(self):
        inputs = self._capture_inputs()
        assert inputs.vote_events == []

    def test_vote_casts_empty(self):
        inputs = self._capture_inputs()
        assert inputs.vote_casts == []

    def test_member_terms_empty(self):
        inputs = self._capture_inputs()
        assert inputs.member_terms == []

    def test_memberships_empty(self):
        inputs = self._capture_inputs()
        assert inputs.memberships == []

    def test_primary_sponsors_empty(self):
        inputs = self._capture_inputs()
        assert inputs.primary_sponsors == []


# ---------------------------------------------------------------------------
# Delegation and return value
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_returns_result_from_runtime(self):
        conn = MagicMock()
        settings = _settings()
        expected = MagicMock()

        with (
            patch("src.runtime.congress_live.CongressAPIClient", autospec=True) as MockClient,
            patch(
                "src.runtime.congress_live.run_congress_load_runtime",
                return_value=expected,
            ),
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter([])
            instance.iter_committees.return_value = iter([])
            instance.iter_bills.return_value = iter([])

            result = run_live_congress_load(conn, settings, congress=119)

        assert result is expected

    def test_conn_passed_to_runtime(self):
        conn = MagicMock()
        settings = _settings()

        with (
            patch("src.runtime.congress_live.CongressAPIClient", autospec=True) as MockClient,
            patch(
                "src.runtime.congress_live.run_congress_load_runtime",
                return_value=MagicMock(),
            ) as mock_rt,
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter([])
            instance.iter_committees.return_value = iter([])
            instance.iter_bills.return_value = iter([])

            run_live_congress_load(conn, settings, congress=119)

        actual_conn = mock_rt.call_args.args[0]
        assert actual_conn is conn
