"""Tests for src/ingest/congress/live_api.py.

Two test layers
---------------
1. MagicMock layer (class Test*) — patches iter_* at the boundary; fast and
   focused on the helper wiring.

2. CongressLiveFixture layer (class TestPaginated*) — runs the real
   CongressAPIClient against an offline httpx.MockTransport so that realistic
   multi-page, empty-page, and no-cosponsor scenarios exercise the full stack
   without any network calls.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock

import httpx

from src.ingest.congress.congress_api import (
    BASE_URL,
    CongressAPIClient,
    bills_url,
    committees_url,
    cosponsors_url,
    members_url,
)
from src.ingest.congress.live_api import (
    fetch_bills,
    fetch_committees,
    fetch_cosponsors_for_bills,
    fetch_members,
)
from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
)

# ---------------------------------------------------------------------------
# Shared record fixtures
# ---------------------------------------------------------------------------

_MEMBER = MemberRecord(
    bioguide_id="A000001",
    first_name="Ada",
    last_name="Lovelace",
    full_name="Ada Lovelace",
    chamber="house",
    party="D",
    state="CA",
    is_current=True,
)

_COMMITTEE = CommitteeRecord(
    committee_code="HJUD00",
    congress=119,
    chamber="house",
    committee_type="standing",
    name="Committee on the Judiciary",
)

_BILL = BillRecord(
    congress=119,
    bill_type="hr",
    bill_number=1,
    title="A bill",
    introduced_date=datetime.date(2025, 1, 15),
)

_COSPONSOR = CosponsorRecord(
    congress=119,
    bill_type="hr",
    bill_number=1,
    bioguide_id="B000002",
    is_original=False,
    sponsor_date=datetime.date(2025, 1, 20),
)


def _make_client() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# CongressLiveFixture — offline HTTP transport for realistic pagination tests
# ---------------------------------------------------------------------------


class CongressLiveFixture:
    """Build a CongressAPIClient backed by in-memory HTTP routes.

    Register pages with add_*_page(); each page is a separate route keyed by
    the canonical URL (without api_key).  Use the ``next_url`` kwarg to chain
    paginated pages — the value must match the key of the next registered route.

    Usage::

        fixture = CongressLiveFixture()
        fixture.add_members_page([_RAW_MEMBER_A, _RAW_MEMBER_B])
        fixture.add_bills_page([_RAW_BILL_1], congress=119, next_url=PAGE2_URL)
        fixture.add_bills_page([_RAW_BILL_2], congress=119, url=PAGE2_URL)
        client = fixture.build_client()
        members = fetch_members(client, 119)
    """

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Route registration helpers
    # ------------------------------------------------------------------

    def _register(self, url: str, body: dict[str, Any]) -> None:
        self._routes[url] = body

    def _paginated_body(
        self,
        items_key: str,
        items: list[dict[str, Any]],
        next_url: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {items_key: items}
        if next_url:
            body["pagination"] = {"next": next_url}
        return body

    def add_members_page(
        self,
        members: list[dict[str, Any]],
        *,
        congress: int | None = None,
        offset: int = 0,
        limit: int = 250,
        next_url: str | None = None,
        url: str | None = None,
    ) -> "CongressLiveFixture":
        route_url = url or members_url(congress, limit=limit, offset=offset)
        self._register(route_url, self._paginated_body("members", members, next_url))
        return self

    def add_committees_page(
        self,
        committees: list[dict[str, Any]],
        *,
        congress: int | None = None,
        chamber: str | None = None,
        offset: int = 0,
        limit: int = 250,
        next_url: str | None = None,
        url: str | None = None,
    ) -> "CongressLiveFixture":
        if url is None:
            assert congress is not None, "congress required when url is not provided"
            url = committees_url(congress, chamber, limit=limit, offset=offset)
        self._register(url, self._paginated_body("committees", committees, next_url))
        return self

    def add_bills_page(
        self,
        bills: list[dict[str, Any]],
        *,
        congress: int | None = None,
        bill_type: str | None = None,
        offset: int = 0,
        limit: int = 250,
        next_url: str | None = None,
        url: str | None = None,
    ) -> "CongressLiveFixture":
        if url is None:
            assert congress is not None, "congress required when url is not provided"
            url = bills_url(congress, bill_type, limit=limit, offset=offset)
        self._register(url, self._paginated_body("bills", bills, next_url))
        return self

    def add_cosponsors_page(
        self,
        cosponsors: list[dict[str, Any]],
        *,
        congress: int | None = None,
        bill_type: str | None = None,
        bill_number: int | None = None,
        offset: int = 0,
        limit: int = 250,
        next_url: str | None = None,
        url: str | None = None,
    ) -> "CongressLiveFixture":
        if url is None:
            assert congress is not None and bill_type is not None and bill_number is not None, (
                "congress, bill_type, and bill_number required when url is not provided"
            )
            url = cosponsors_url(congress, bill_type, bill_number, limit=limit, offset=offset)
        self._register(url, self._paginated_body("cosponsors", cosponsors, next_url))
        return self

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    def build_client(self) -> CongressAPIClient:
        """Return a CongressAPIClient that serves responses from registered routes.

        The API key is header-only (verified by test_congress_api_transport.py),
        so URLs are matched directly — no query-param stripping needed.
        """
        routes = dict(self._routes)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url in routes:
                return httpx.Response(200, json=routes[url])
            return httpx.Response(404, content=b"not found")

        # Bypass __init__ to inject the mock transport.
        api_client: CongressAPIClient = object.__new__(CongressAPIClient)
        api_client._base_url = BASE_URL  # type: ignore[attr-defined]
        api_client._client = httpx.Client(  # type: ignore[attr-defined]
            transport=httpx.MockTransport(handler)
        )
        return api_client


# ---------------------------------------------------------------------------
# Inline raw-API fixtures for fixture-based tests
# ---------------------------------------------------------------------------

_RAW_MEMBER_A: dict[str, Any] = {
    "bioguideId": "A000001",
    "firstName": "Ada",
    "lastName": "Lovelace",
    "directOrderName": "Ada Lovelace",
    "partyName": "D",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023-01-03"}]},
}

_RAW_MEMBER_B: dict[str, Any] = {
    "bioguideId": "B000002",
    "firstName": "Bob",
    "lastName": "Smith",
    "directOrderName": "Bob Smith",
    "partyName": "R",
    "state": "TX",
    "currentMember": True,
    "terms": {"item": [{"chamber": "Senate", "startYear": "2021-01-03"}]},
}

_RAW_COMMITTEE: dict[str, Any] = {
    "systemCode": "hjud00",
    "chamber": "House",
    "committeeTypeCode": "Standing",
    "name": "Committee on the Judiciary",
}

_RAW_BILL_1: dict[str, Any] = {
    "congress": 119,
    "type": "HR",
    "number": 1,
    "title": "A bill",
    "introducedDate": "2025-01-15",
    "latestAction": {"actionDate": "2025-03-01", "text": "Passed"},
}

_RAW_BILL_2: dict[str, Any] = {
    "congress": 119,
    "type": "HR",
    "number": 2,
    "title": "Another bill",
    "introducedDate": "2025-01-20",
}

_RAW_COSPONSOR_X: dict[str, Any] = {
    "bioguideId": "X000099",
    "isOriginalCosponsor": False,
    "sponsorshipDate": "2025-02-01",
}

_RAW_COSPONSOR_Y: dict[str, Any] = {
    "bioguideId": "Y000088",
    "isOriginalCosponsor": True,
    "sponsorshipDate": "2025-01-20",
}


# ---------------------------------------------------------------------------
# MagicMock layer — fetch_members
# ---------------------------------------------------------------------------


class TestFetchMembers:
    def test_returns_list_of_member_records(self):
        client = _make_client()
        client.iter_members.return_value = iter([_MEMBER])
        result = fetch_members(client)
        assert result == [_MEMBER]

    def test_passes_congress_to_iter_members(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        fetch_members(client, congress=119)
        client.iter_members.assert_called_once_with(119)

    def test_passes_none_congress_when_omitted(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        fetch_members(client)
        client.iter_members.assert_called_once_with(None)

    def test_returns_empty_list_when_no_members(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        assert fetch_members(client) == []

    def test_returns_multiple_members(self):
        m2 = MemberRecord(
            bioguide_id="B000002",
            first_name="Bob",
            last_name="Smith",
            full_name="Bob Smith",
            chamber="senate",
            is_current=True,
        )
        client = _make_client()
        client.iter_members.return_value = iter([_MEMBER, m2])
        result = fetch_members(client, congress=118)
        assert len(result) == 2
        assert result[0].bioguide_id == "A000001"
        assert result[1].bioguide_id == "B000002"


# ---------------------------------------------------------------------------
# MagicMock layer — fetch_committees
# ---------------------------------------------------------------------------


class TestFetchCommittees:
    def test_returns_list_of_committee_records(self):
        client = _make_client()
        client.iter_committees.return_value = iter([_COMMITTEE])
        result = fetch_committees(client, congress=119)
        assert result == [_COMMITTEE]

    def test_passes_congress_and_chamber(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        fetch_committees(client, congress=119, chamber="house")
        client.iter_committees.assert_called_once_with(119, "house")

    def test_omits_chamber_when_not_provided(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        fetch_committees(client, congress=119)
        client.iter_committees.assert_called_once_with(119)

    def test_returns_empty_list_when_none(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        assert fetch_committees(client, congress=119) == []


# ---------------------------------------------------------------------------
# MagicMock layer — fetch_bills
# ---------------------------------------------------------------------------


class TestFetchBills:
    def test_returns_list_of_bill_records(self):
        client = _make_client()
        client.iter_bills.return_value = iter([_BILL])
        result = fetch_bills(client, congress=119)
        assert result == [_BILL]

    def test_passes_congress_and_bill_type(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        fetch_bills(client, congress=119, bill_type="hr")
        client.iter_bills.assert_called_once_with(119, "hr")

    def test_omits_bill_type_when_not_provided(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        fetch_bills(client, congress=119)
        client.iter_bills.assert_called_once_with(119)

    def test_returns_empty_list_when_none(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        assert fetch_bills(client, congress=119) == []


# ---------------------------------------------------------------------------
# MagicMock layer — fetch_cosponsors_for_bills
# ---------------------------------------------------------------------------


class TestFetchCosponsorsForBills:
    def test_returns_cosponsors_for_single_bill(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([_COSPONSOR])
        result = fetch_cosponsors_for_bills(client, [_BILL])
        assert result == [_COSPONSOR]

    def test_calls_iter_cosponsors_with_bill_fields(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([])
        fetch_cosponsors_for_bills(client, [_BILL])
        client.iter_cosponsors.assert_called_once_with(119, "hr", 1)

    def test_concatenates_cosponsors_across_bills(self):
        bill2 = BillRecord(
            congress=119,
            bill_type="s",
            bill_number=50,
            title="Another bill",
        )
        cosponsor2 = CosponsorRecord(
            congress=119,
            bill_type="s",
            bill_number=50,
            bioguide_id="C000003",
        )

        def _side_effect(congress, bill_type, bill_number):
            if bill_type == "hr":
                return iter([_COSPONSOR])
            return iter([cosponsor2])

        client = _make_client()
        client.iter_cosponsors.side_effect = _side_effect
        result = fetch_cosponsors_for_bills(client, [_BILL, bill2])
        assert result == [_COSPONSOR, cosponsor2]

    def test_empty_bills_returns_empty_list(self):
        client = _make_client()
        assert fetch_cosponsors_for_bills(client, []) == []
        client.iter_cosponsors.assert_not_called()

    def test_bill_with_no_cosponsors_contributes_nothing(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([])
        result = fetch_cosponsors_for_bills(client, [_BILL])
        assert result == []

    def test_preserves_input_order(self):
        bills = [
            BillRecord(congress=119, bill_type="hr", bill_number=i, title=f"Bill {i}")
            for i in range(1, 4)
        ]
        cosponsors_by_number = {
            1: [
                CosponsorRecord(congress=119, bill_type="hr", bill_number=1, bioguide_id="X000001")
            ],
            2: [],
            3: [
                CosponsorRecord(congress=119, bill_type="hr", bill_number=3, bioguide_id="X000003")
            ],
        }

        def _side_effect(congress, bill_type, bill_number):
            return iter(cosponsors_by_number[bill_number])

        client = _make_client()
        client.iter_cosponsors.side_effect = _side_effect
        result = fetch_cosponsors_for_bills(client, bills)
        assert [r.bill_number for r in result] == [1, 3]


# ---------------------------------------------------------------------------
# CongressLiveFixture layer — paginated members
# ---------------------------------------------------------------------------


class TestPaginatedFetchMembers:
    def test_single_page_returns_all_members(self):
        fixture = CongressLiveFixture()
        fixture.add_members_page([_RAW_MEMBER_A, _RAW_MEMBER_B], congress=119)
        client = fixture.build_client()
        result = fetch_members(client, congress=119)
        assert len(result) == 2
        assert result[0].bioguide_id == "A000001"
        assert result[1].bioguide_id == "B000002"
        assert result[0].source_url == members_url(119)
        assert result[1].source_url == members_url(119)

    def test_empty_page_returns_empty_list(self):
        fixture = CongressLiveFixture()
        fixture.add_members_page([], congress=119)
        client = fixture.build_client()
        result = fetch_members(client, congress=119)
        assert result == []

    def test_two_pages_concatenated_in_order(self):
        page2_url = members_url(119, offset=250)
        fixture = (
            CongressLiveFixture()
            .add_members_page([_RAW_MEMBER_A], congress=119, next_url=page2_url)
            .add_members_page([_RAW_MEMBER_B], url=page2_url)
        )
        client = fixture.build_client()
        result = fetch_members(client, congress=119)
        assert len(result) == 2
        assert result[0].bioguide_id == "A000001"
        assert result[1].bioguide_id == "B000002"
        assert result[0].source_url == members_url(119)
        assert result[1].source_url == page2_url

    def test_empty_page_with_next_url_terminates_pagination(self):
        # An empty page stops iteration even if pagination.next is present.
        page2_url = members_url(119, offset=250)
        fixture = (
            CongressLiveFixture()
            .add_members_page([], congress=119, next_url=page2_url)
            .add_members_page([_RAW_MEMBER_B], url=page2_url)
        )
        client = fixture.build_client()
        result = fetch_members(client, congress=119)
        # empty first page stops; page 2 is never fetched
        assert result == []

    def test_no_congress_filter_fetches_all_current_members(self):
        # members_url(None) → /member endpoint
        fixture = CongressLiveFixture()
        fixture.add_members_page([_RAW_MEMBER_A], congress=None)
        client = fixture.build_client()
        result = fetch_members(client, congress=None)
        assert result[0].bioguide_id == "A000001"


# ---------------------------------------------------------------------------
# CongressLiveFixture layer — paginated committees
# ---------------------------------------------------------------------------


class TestPaginatedFetchCommittees:
    def test_single_page_all_chambers(self):
        fixture = CongressLiveFixture()
        fixture.add_committees_page([_RAW_COMMITTEE], congress=119)
        client = fixture.build_client()
        result = fetch_committees(client, congress=119)
        assert len(result) == 1
        assert result[0].committee_code == "hjud00"

    def test_empty_page_returns_empty_list(self):
        fixture = CongressLiveFixture()
        fixture.add_committees_page([], congress=119)
        client = fixture.build_client()
        result = fetch_committees(client, congress=119)
        assert result == []

    def test_chamber_filter_uses_separate_route(self):
        fixture = CongressLiveFixture()
        fixture.add_committees_page([_RAW_COMMITTEE], congress=119, chamber="house")
        client = fixture.build_client()
        result = fetch_committees(client, congress=119, chamber="house")
        assert len(result) == 1
        assert result[0].chamber == "house"

    def test_two_pages_concatenated_in_order(self):
        raw_c2: dict[str, Any] = {
            "systemCode": "hnat00",
            "chamber": "House",
            "committeeTypeCode": "Standing",
            "name": "Committee on Natural Resources",
        }
        page2_url = committees_url(119, offset=250)
        fixture = (
            CongressLiveFixture()
            .add_committees_page([_RAW_COMMITTEE], congress=119, next_url=page2_url)
            .add_committees_page([raw_c2], url=page2_url)
        )
        client = fixture.build_client()
        result = fetch_committees(client, congress=119)
        assert len(result) == 2
        assert result[0].committee_code == "hjud00"
        assert result[1].committee_code == "hnat00"
        assert result[0].source_url == committees_url(119)
        assert result[1].source_url == page2_url


# ---------------------------------------------------------------------------
# CongressLiveFixture layer — paginated bills
# ---------------------------------------------------------------------------


class TestPaginatedFetchBills:
    def test_single_page_returns_bills(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1], congress=119)
        client = fixture.build_client()
        result = fetch_bills(client, congress=119)
        assert len(result) == 1
        assert result[0].bill_number == 1

    def test_empty_page_returns_empty_list(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([], congress=119)
        client = fixture.build_client()
        result = fetch_bills(client, congress=119)
        assert result == []

    def test_two_pages_concatenated_in_order(self):
        page2_url = bills_url(119, offset=250)
        fixture = (
            CongressLiveFixture()
            .add_bills_page([_RAW_BILL_1], congress=119, next_url=page2_url)
            .add_bills_page([_RAW_BILL_2], url=page2_url)
        )
        client = fixture.build_client()
        result = fetch_bills(client, congress=119)
        assert len(result) == 2
        assert result[0].bill_number == 1
        assert result[1].bill_number == 2
        assert result[0].source_url == bills_url(119)
        assert result[1].source_url == page2_url

    def test_bill_type_filter_uses_separate_route(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1], congress=119, bill_type="hr")
        client = fixture.build_client()
        result = fetch_bills(client, congress=119, bill_type="hr")
        assert len(result) == 1
        assert result[0].bill_type == "hr"


# ---------------------------------------------------------------------------
# CongressLiveFixture layer — cosponsors with edge cases
# ---------------------------------------------------------------------------


class TestPaginatedFetchCosponsors:
    def test_single_bill_with_cosponsors(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1], congress=119)
        fixture.add_cosponsors_page([_RAW_COSPONSOR_X], congress=119, bill_type="hr", bill_number=1)
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert len(result) == 1
        assert result[0].bioguide_id == "X000099"

    def test_bill_with_no_cosponsors_contributes_nothing(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1], congress=119)
        fixture.add_cosponsors_page([], congress=119, bill_type="hr", bill_number=1)
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert result == []

    def test_two_bills_one_with_no_cosponsors_preserves_order(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1, _RAW_BILL_2], congress=119)
        fixture.add_cosponsors_page([_RAW_COSPONSOR_X], congress=119, bill_type="hr", bill_number=1)
        fixture.add_cosponsors_page([], congress=119, bill_type="hr", bill_number=2)
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert len(result) == 1
        assert result[0].bioguide_id == "X000099"

    def test_two_cosponsors_across_two_bills_in_bill_order(self):
        fixture = CongressLiveFixture()
        fixture.add_bills_page([_RAW_BILL_1, _RAW_BILL_2], congress=119)
        fixture.add_cosponsors_page([_RAW_COSPONSOR_X], congress=119, bill_type="hr", bill_number=1)
        fixture.add_cosponsors_page([_RAW_COSPONSOR_Y], congress=119, bill_type="hr", bill_number=2)
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert len(result) == 2
        assert result[0].bioguide_id == "X000099"
        assert result[1].bioguide_id == "Y000088"
        assert result[0].source_url == cosponsors_url(119, "hr", 1)
        assert result[1].source_url == cosponsors_url(119, "hr", 2)

    def test_cosponsor_two_pages_for_single_bill(self):
        page2_url = cosponsors_url(119, "hr", 1, offset=250)
        fixture = (
            CongressLiveFixture()
            .add_bills_page([_RAW_BILL_1], congress=119)
            .add_cosponsors_page(
                [_RAW_COSPONSOR_X],
                congress=119,
                bill_type="hr",
                bill_number=1,
                next_url=page2_url,
            )
            .add_cosponsors_page(
                [_RAW_COSPONSOR_Y],
                url=page2_url,
            )
        )
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert len(result) == 2
        assert result[0].bioguide_id == "X000099"
        assert result[1].bioguide_id == "Y000088"
        assert result[0].source_url == cosponsors_url(119, "hr", 1)
        assert result[1].source_url == page2_url

    def test_empty_bills_list_makes_no_cosponsor_requests(self):
        # No cosponsor routes registered; iter on empty bills must not hit the network.
        fixture = CongressLiveFixture()
        fixture.add_bills_page([], congress=119)
        client = fixture.build_client()
        bills = fetch_bills(client, congress=119)
        result = fetch_cosponsors_for_bills(client, bills)
        assert result == []
