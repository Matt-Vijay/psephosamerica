"""In-memory fixture payloads for Congress.gov live API responses.

Provides typed helpers that return realistic JSON-serialisable dicts and
XML strings matching the Congress.gov v3 API shape.  No network calls,
no disk writes, no binary blobs.

Public API
----------
Item constructors (return one API item dict):
    member_item(**overrides)       -> dict
    committee_item(**overrides)    -> dict
    bill_item(**overrides)         -> dict
    cosponsor_item(**overrides)    -> dict

Single-page response builders:
    members_page(items, *, next_url)       -> dict
    committees_page(items, *, next_url)    -> dict
    bills_page(items, *, next_url)         -> dict
    cosponsors_page(items, *, next_url)    -> dict

Detail response builders:
    member_detail_response(payload)  -> dict  # wraps {"member": payload}
    bill_detail_response(payload)    -> dict  # wraps {"bill": payload}

Multi-page helpers (auto-wire next_url):
    paginated_members(pages)      -> list[dict]
    paginated_committees(pages)   -> list[dict]
    paginated_bills(pages)        -> list[dict]
    paginated_cosponsors(pages)   -> list[dict]

Vote XML generators (return str):
    house_vote_xml(congress, session, roll_call_number, year, **overrides) -> str
    senate_vote_xml(congress, session, roll_call_number, **overrides) -> str
"""

from __future__ import annotations

from copy import deepcopy
import textwrap
from typing import Any

# ---------------------------------------------------------------------------
# Pagination sentinel URL base used by multi-page helpers
# ---------------------------------------------------------------------------

_NEXT_BASE = "https://api.congress.gov/v3/__fixture_page__"


# ---------------------------------------------------------------------------
# Default item shapes — representative but minimal
# ---------------------------------------------------------------------------

_DEFAULT_MEMBER: dict[str, Any] = {
    "bioguideId": "P000197",
    "firstName": "Nancy",
    "lastName": "Pelosi",
    "directOrderName": "Nancy Pelosi",
    "partyName": "Democratic",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023-01-03"}]},
}

_DEFAULT_COMMITTEE: dict[str, Any] = {
    "systemCode": "hswm00",
    "chamber": "House",
    "committeeTypeCode": "Standing",
    "name": "Committee on Ways and Means",
}

_DEFAULT_BILL: dict[str, Any] = {
    "congress": 119,
    "type": "HR",
    "number": 1,
    "title": "Test Bill One",
    "introducedDate": "2025-01-09",
    "latestAction": {"actionDate": "2025-03-01", "text": "Passed"},
}

_DEFAULT_COSPONSOR: dict[str, Any] = {
    "bioguideId": "S000148",
    "isOriginalCosponsor": False,
    "sponsorshipDate": "2025-01-15",
}

_DEFAULT_MEMBER_DETAIL: dict[str, Any] = {
    "bioguideId": "P000197",
    "firstName": "Nancy",
    "lastName": "Pelosi",
    "directOrderName": "Nancy Pelosi",
    "partyName": "Democratic",
    "state": "CA",
    "currentMember": True,
    "terms": {
        "item": [
            {
                "congress": 119,
                "chamber": "House of Representatives",
                "startYear": "2025-01-03",
                "stateCode": "CA",
                "district": 11,
            }
        ]
    },
    "committees": {
        "item": [
            {
                "committee": {
                    "systemCode": "hswm00",
                    "url": "https://api.congress.gov/v3/committee/hswm00",
                },
                "congress": 119,
                "role": "Member",
                "startDate": "2025-01-03",
                "isCurrent": True,
            }
        ]
    },
}

_DEFAULT_BILL_DETAIL: dict[str, Any] = {
    "congress": 119,
    "type": "HR",
    "number": 1,
    "title": "Test Bill One",
    "introducedDate": "2025-01-09",
    "latestAction": {"actionDate": "2025-03-01", "text": "Passed"},
    "sponsors": [
        {
            "bioguideId": "P000197",
            "sponsorshipDate": "2025-01-09",
            "url": "https://api.congress.gov/v3/member/P000197",
        }
    ],
}

# ---------------------------------------------------------------------------
# Item constructors
# ---------------------------------------------------------------------------


def member_item(**overrides: Any) -> dict[str, Any]:
    """Return a member list-item dict with optional field overrides."""
    result = deepcopy(_DEFAULT_MEMBER)
    result.update(overrides)
    return result


def committee_item(**overrides: Any) -> dict[str, Any]:
    """Return a committee list-item dict with optional field overrides."""
    result = deepcopy(_DEFAULT_COMMITTEE)
    result.update(overrides)
    return result


def bill_item(**overrides: Any) -> dict[str, Any]:
    """Return a bill list-item dict with optional field overrides."""
    result = deepcopy(_DEFAULT_BILL)
    result.update(overrides)
    return result


def cosponsor_item(**overrides: Any) -> dict[str, Any]:
    """Return a cosponsor list-item dict with optional field overrides."""
    result = deepcopy(_DEFAULT_COSPONSOR)
    result.update(overrides)
    return result


def member_detail_item(**overrides: Any) -> dict[str, Any]:
    """Return a realistic member-detail payload with optional field overrides."""
    result = deepcopy(_DEFAULT_MEMBER_DETAIL)
    result.update(overrides)
    return result


def bill_detail_item(**overrides: Any) -> dict[str, Any]:
    """Return a realistic bill-detail payload with optional field overrides."""
    result = deepcopy(_DEFAULT_BILL_DETAIL)
    result.update(overrides)
    return result


# ---------------------------------------------------------------------------
# Single-page response builders
# ---------------------------------------------------------------------------


def _page(items_key: str, items: list[dict[str, Any]], next_url: str | None) -> dict[str, Any]:
    pagination: dict[str, Any] = {"count": len(items)}
    if next_url:
        pagination["next"] = next_url
    return {items_key: items, "pagination": pagination}


def members_page(
    items: list[dict[str, Any]] | None = None,
    *,
    next_url: str | None = None,
) -> dict[str, Any]:
    """Return a single paginated members response body."""
    return _page("members", items if items is not None else [member_item()], next_url)


def committees_page(
    items: list[dict[str, Any]] | None = None,
    *,
    next_url: str | None = None,
) -> dict[str, Any]:
    """Return a single paginated committees response body."""
    return _page("committees", items if items is not None else [committee_item()], next_url)


def bills_page(
    items: list[dict[str, Any]] | None = None,
    *,
    next_url: str | None = None,
) -> dict[str, Any]:
    """Return a single paginated bills response body."""
    return _page("bills", items if items is not None else [bill_item()], next_url)


def cosponsors_page(
    items: list[dict[str, Any]] | None = None,
    *,
    next_url: str | None = None,
) -> dict[str, Any]:
    """Return a single paginated cosponsors response body."""
    return _page("cosponsors", items if items is not None else [cosponsor_item()], next_url)


# ---------------------------------------------------------------------------
# Detail response builders
# ---------------------------------------------------------------------------


def member_detail_response(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a member detail response body wrapping *payload* under 'member'."""
    if payload is None:
        payload = member_detail_item()
    return {"member": payload}


def bill_detail_response(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a bill detail response body wrapping *payload* under 'bill'."""
    if payload is None:
        payload = bill_detail_item()
    return {"bill": payload}


# ---------------------------------------------------------------------------
# Multi-page helpers — auto-wire next_url between pages
# ---------------------------------------------------------------------------


def _paginated(
    builder: Any,  # callable(items, *, next_url) -> dict
    pages: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Wire *pages* into a chain of paginated response dicts.

    Each page's ``pagination.next`` points to a deterministic sentinel URL for
    the following page; the last page has no ``next``.
    """
    result = []
    n = len(pages)
    for i, page_items in enumerate(pages):
        next_url = f"{_NEXT_BASE}/{i + 1}" if i < n - 1 else None
        result.append(builder(page_items, next_url=next_url))
    return result


def paginated_members(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a list of member page response dicts linked by next_url."""
    return _paginated(members_page, pages)


def paginated_committees(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a list of committee page response dicts linked by next_url."""
    return _paginated(committees_page, pages)


def paginated_bills(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a list of bill page response dicts linked by next_url."""
    return _paginated(bills_page, pages)


def paginated_cosponsors(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a list of cosponsor page response dicts linked by next_url."""
    return _paginated(cosponsors_page, pages)


# ---------------------------------------------------------------------------
# Vote XML generators
# ---------------------------------------------------------------------------

_HOUSE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>{congress}</congress>
        <session>{session}</session>
        <rollcall-num>{roll_call_number}</rollcall-num>
        <vote-question>{question}</vote-question>
        <vote-result>{result}</vote-result>
        <action-date date="{iso_date}">{display_date}</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="{bioguide_id}" party="{party}" state="{state}">{legislator_name}</legislator>
          <vote>{vote_option}</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

_SENATE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>{congress}</congress>
      <session>{session}</session>
      <vote_number>{roll_call_number}</vote_number>
      <vote_question_text>{question}</vote_question_text>
      <vote_result_text>{result}</vote_result_text>
      <vote_date>{display_date}</vote_date>
      <members>
        <member>
          <lis_member_id>{lis_member_id}</lis_member_id>
          <member_full>{member_full}</member_full>
          <vote_cast>{vote_cast}</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


def house_vote_xml(
    congress: int = 119,
    session: int = 1,
    roll_call_number: int = 1,
    year: int = 2025,
    *,
    question: str = "On Passage",
    result: str = "Passed",
    bioguide_id: str = "P000197",
    legislator_name: str = "Pelosi",
    party: str = "D",
    state: str = "CA",
    vote_option: str = "Yea",
) -> str:
    """Return a House roll-call vote XML string."""
    iso_date = f"{year}-01-15"
    display_date = f"15-Jan-{year}"
    return _HOUSE_VOTE_XML.format(
        congress=congress,
        session=session,
        roll_call_number=roll_call_number,
        question=question,
        result=result,
        iso_date=iso_date,
        display_date=display_date,
        bioguide_id=bioguide_id,
        legislator_name=legislator_name,
        party=party,
        state=state,
        vote_option=vote_option,
    )


def senate_vote_xml(
    congress: int = 119,
    session: int = 1,
    roll_call_number: int = 1,
    *,
    question: str = "On the Motion",
    result: str = "Agreed To",
    display_date: str = "January 20, 2025",
    lis_member_id: str = "S270",
    member_full: str = "Schumer (D-NY)",
    vote_cast: str = "Yea",
) -> str:
    """Return a Senate roll-call vote XML string."""
    return _SENATE_VOTE_XML.format(
        congress=congress,
        session=session,
        roll_call_number=roll_call_number,
        question=question,
        result=result,
        display_date=display_date,
        lis_member_id=lis_member_id,
        member_full=member_full,
        vote_cast=vote_cast,
    )
