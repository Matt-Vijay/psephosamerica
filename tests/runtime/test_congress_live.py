"""Tests for src/runtime/congress_live.py.

No live network calls.  The CongressAPIClient and run_congress_load_runtime
boundaries are both mocked.

Covers:
  - client is constructed with settings.congress_api_key
  - members, committees, bills are fetched for the given congress
  - cosponsors are fetched for every bill returned
  - CongressIngestInputs is built with the fetched data and empty vote lists
  - result is the return value of run_congress_load_runtime
  - paginated / empty-page / no-cosponsor edge cases do not change the wiring
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


def _cosponsor(
    bill_type: str = "hr", bill_number: int = 1, bioguide_id: str = "B000001"
) -> CosponsorRecord:
    return CosponsorRecord(
        congress=119,
        bill_type=bill_type,
        bill_number=bill_number,
        bioguide_id=bioguide_id,
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
# Fetch ordering — members → committees → bills → cosponsors per bill
# ---------------------------------------------------------------------------


class TestFetchOrdering:
    """Verify that the four fetch steps happen in the documented sequence."""

    def _capture_call_order(self, bills=None):
        conn = MagicMock()
        settings = _settings()
        bills = bills or []
        call_log: list[str] = []

        with (
            patch("src.runtime.congress_live.CongressAPIClient", autospec=True) as MockClient,
            patch("src.runtime.congress_live.run_congress_load_runtime", return_value=MagicMock()),
        ):
            instance = MockClient.return_value.__enter__.return_value

            def _log_members(congress):
                call_log.append("members")
                return iter([])

            def _log_committees(congress):
                call_log.append("committees")
                return iter([])

            def _log_bills(congress):
                call_log.append("bills")
                return iter(bills)

            def _log_cosponsors(congress, bill_type, bill_number):
                call_log.append(f"cosponsors:{bill_type}:{bill_number}")
                return iter([])

            instance.iter_members.side_effect = _log_members
            instance.iter_committees.side_effect = _log_committees
            instance.iter_bills.side_effect = _log_bills
            instance.iter_cosponsors.side_effect = _log_cosponsors

            run_live_congress_load(conn, settings, congress=119)

        return call_log

    def test_members_before_committees(self):
        log = self._capture_call_order()
        assert log.index("members") < log.index("committees")

    def test_committees_before_bills(self):
        log = self._capture_call_order()
        assert log.index("committees") < log.index("bills")

    def test_bills_before_cosponsors(self):
        log = self._capture_call_order(bills=[_bill("hr", 1)])
        assert log.index("bills") < log.index("cosponsors:hr:1")

    def test_cosponsor_order_matches_bill_order(self):
        bills = [_bill("hr", 1), _bill("s", 2), _bill("hr", 3)]
        log = self._capture_call_order(bills=bills)
        cosponsor_entries = [e for e in log if e.startswith("cosponsors:")]
        assert cosponsor_entries == ["cosponsors:hr:1", "cosponsors:s:2", "cosponsors:hr:3"]


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
# Paginated / edge-case wiring
# ---------------------------------------------------------------------------


class TestPaginatedEdgeCases:
    """Ensure wiring holds for realistic paginated inputs and edge cases."""

    def _run_and_capture(self, members=None, committees=None, bills=None, cosponsors_map=None):
        """
        cosponsors_map: dict[tuple[str, int], list[CosponsorRecord]] keyed by
                        (bill_type, bill_number).
        """
        conn = MagicMock()
        settings = _settings()
        members = members or []
        committees = committees or []
        bills = bills or []
        cosponsors_map = cosponsors_map or {}

        captured: list = []

        def _capture(c, inputs):
            captured.append(inputs)
            return MagicMock()

        def _cosponsors_side_effect(congress, bill_type, bill_number):
            return iter(cosponsors_map.get((bill_type, bill_number), []))

        with (
            patch("src.runtime.congress_live.CongressAPIClient", autospec=True) as MockClient,
            patch("src.runtime.congress_live.run_congress_load_runtime", side_effect=_capture),
        ):
            instance = MockClient.return_value.__enter__.return_value
            instance.iter_members.return_value = iter(members)
            instance.iter_committees.return_value = iter(committees)
            instance.iter_bills.return_value = iter(bills)
            instance.iter_cosponsors.side_effect = _cosponsors_side_effect

            run_live_congress_load(conn, settings, congress=119)

        return captured[0]

    def test_multiple_members_all_reach_inputs(self):
        members = [_member("A000001"), _member("B000002"), _member("C000003")]
        inputs = self._run_and_capture(members=members)
        assert len(inputs.members) == 3
        assert [m.bioguide_id for m in inputs.members] == ["A000001", "B000002", "C000003"]

    def test_multiple_committees_all_reach_inputs(self):
        committees = [_committee("hsag00"), _committee("hjud00"), _committee("hfin00")]
        inputs = self._run_and_capture(committees=committees)
        assert len(inputs.committees) == 3
        codes = [c.committee_code for c in inputs.committees]
        assert codes == ["hsag00", "hjud00", "hfin00"]

    def test_multiple_bills_all_reach_inputs(self):
        bills = [_bill("hr", 1), _bill("hr", 2), _bill("s", 3)]
        inputs = self._run_and_capture(bills=bills)
        assert len(inputs.bills) == 3

    def test_bill_with_no_cosponsors_not_in_cosponsor_list(self):
        bills = [_bill("hr", 1)]
        # No cosponsors for (hr, 1) in the map — should yield empty.
        inputs = self._run_and_capture(bills=bills, cosponsors_map={})
        assert inputs.cosponsors == []

    def test_mixed_cosponsor_coverage_across_bills(self):
        bills = [_bill("hr", 1), _bill("s", 2), _bill("hr", 3)]
        cs1 = _cosponsor("hr", 1, "X000001")
        cs3a = _cosponsor("hr", 3, "X000003a")
        cs3b = _cosponsor("hr", 3, "X000003b")
        cmap = {
            ("hr", 1): [cs1],
            ("s", 2): [],
            ("hr", 3): [cs3a, cs3b],
        }
        inputs = self._run_and_capture(bills=bills, cosponsors_map=cmap)
        assert len(inputs.cosponsors) == 3
        assert inputs.cosponsors[0].bioguide_id == "X000001"
        assert inputs.cosponsors[1].bioguide_id == "X000003a"
        assert inputs.cosponsors[2].bioguide_id == "X000003b"

    def test_no_members_no_bills_empty_inputs(self):
        inputs = self._run_and_capture()
        assert inputs.members == []
        assert inputs.committees == []
        assert inputs.bills == []
        assert inputs.cosponsors == []
        assert inputs.vote_events == []
        assert inputs.vote_casts == []

    def test_all_deferred_fields_are_always_empty(self):
        bills = [_bill("hr", 1)]
        inputs = self._run_and_capture(
            members=[_member()],
            committees=[_committee()],
            bills=bills,
            cosponsors_map={("hr", 1): [_cosponsor()]},
        )
        assert inputs.vote_events == []
        assert inputs.vote_casts == []
        assert inputs.member_terms == []
        assert inputs.memberships == []
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
