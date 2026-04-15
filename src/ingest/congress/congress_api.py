"""Congress.gov API v3 client — URL builders, response normalization, paginating iterators.

No DB writes; all public helpers return typed ingest records from models.py.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

import httpx

from .models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
)

BASE_URL = "https://api.congress.gov/v3/"
DEFAULT_LIMIT = 250


def members_url(
    congress: int | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    path = f"member/congress/{congress}" if congress else "member"
    params = {"limit": limit, "offset": offset, "format": "json"}
    return urljoin(BASE_URL, path) + "?" + urlencode(params)


def member_detail_url(bioguide_id: str) -> str:
    return urljoin(BASE_URL, f"member/{bioguide_id}") + "?format=json"


def committees_url(
    congress: int,
    chamber: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    path = f"committee/congress/{congress}"
    if chamber:
        path += f"/{chamber}"
    params = {"limit": limit, "offset": offset, "format": "json"}
    return urljoin(BASE_URL, path) + "?" + urlencode(params)


def bills_url(
    congress: int,
    bill_type: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    path = f"bill/{congress}"
    if bill_type:
        path += f"/{bill_type}"
    params = {"limit": limit, "offset": offset, "format": "json"}
    return urljoin(BASE_URL, path) + "?" + urlencode(params)


def bill_detail_url(congress: int, bill_type: str, bill_number: int) -> str:
    path = f"bill/{congress}/{bill_type}/{bill_number}"
    return urljoin(BASE_URL, path) + "?format=json"


def cosponsors_url(
    congress: int,
    bill_type: str,
    bill_number: int,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    path = f"bill/{congress}/{bill_type}/{bill_number}/cosponsors"
    params = {"limit": limit, "offset": offset, "format": "json"}
    return urljoin(BASE_URL, path) + "?" + urlencode(params)


def _parse_date(raw: str | None) -> datetime.date | None:
    if not raw:
        return None
    return datetime.date.fromisoformat(raw[:10])


def _normalize_chamber(raw: str | None) -> str:
    if not raw:
        return "house"
    lower = raw.lower().strip()
    if lower in ("senate", "s"):
        return "senate"
    return "house"


def _normalize_bill_type(raw: str) -> str:
    mapping = {
        "hr": "hr", "s": "s",
        "hjres": "hjres", "sjres": "sjres",
        "hconres": "hconres", "sconres": "sconres",
        "hres": "hres", "sres": "sres",
    }
    return mapping.get(raw.lower().replace(".", ""), raw.lower())


def normalize_member(data: dict[str, Any], *, source_url: str | None = None) -> MemberRecord:
    terms = data.get("terms", {}).get("item", [])
    latest_term = terms[-1] if terms else {}
    return MemberRecord(
        bioguide_id=data["bioguideId"],
        first_name=data.get("firstName", data.get("directOrderName", "").split(",")[0].strip()),
        last_name=data.get("lastName", ""),
        full_name=data.get("directOrderName", f'{data.get("firstName", "")} {data.get("lastName", "")}'),
        chamber=_normalize_chamber(latest_term.get("chamber", data.get("terms", {}).get("item", [{}])[0].get("chamber"))),
        party=data.get("partyName"),
        state=data.get("state"),
        middle_name=data.get("middleName"),
        lis_member_id=data.get("lisId"),
        current_term_start=_parse_date(latest_term.get("startYear")),
        current_term_end=_parse_date(latest_term.get("endYear")),
        is_current=data.get("currentMember", False),
        source_url=source_url,
    )


def normalize_committee(data: dict[str, Any], *, congress: int, source_url: str | None = None) -> CommitteeRecord:
    chamber_raw = data.get("chamber", {})
    chamber_name = chamber_raw if isinstance(chamber_raw, str) else chamber_raw.get("name", "")
    ctype = data.get("committeeTypeCode", "other").lower()
    type_map = {
        "standing": "standing",
        "select": "select",
        "joint": "joint",
        "subcommittee": "subcommittee",
    }
    return CommitteeRecord(
        committee_code=data["systemCode"],
        congress=congress,
        chamber=_normalize_chamber(chamber_name),
        committee_type=type_map.get(ctype, "other"),
        name=data.get("name", ""),
        parent_committee_code=data.get("parentCommittee", {}).get("systemCode") if data.get("parentCommittee") else None,
        source_url=source_url,
    )


def normalize_bill(data: dict[str, Any], *, source_url: str | None = None) -> BillRecord:
    return BillRecord(
        congress=data["congress"],
        bill_type=_normalize_bill_type(data["type"]),
        bill_number=data["number"],
        title=data.get("title", ""),
        short_title=data.get("shortTitle"),
        introduced_date=_parse_date(data.get("introducedDate")),
        latest_action_date=_parse_date(data.get("latestAction", {}).get("actionDate")),
        current_status=data.get("latestAction", {}).get("text"),
        source_url=source_url,
    )


def normalize_cosponsor(
    data: dict[str, Any],
    *,
    congress: int,
    bill_type: str,
    bill_number: int,
    source_url: str | None = None,
) -> CosponsorRecord:
    return CosponsorRecord(
        congress=congress,
        bill_type=_normalize_bill_type(bill_type),
        bill_number=bill_number,
        bioguide_id=data["bioguideId"],
        is_original=data.get("isOriginalCosponsor", False),
        sponsor_date=_parse_date(data.get("sponsorshipDate")),
        source_url=source_url,
    )


class CongressAPIClient:
    """Wraps the Congress.gov API. Requires an API key; paginates transparently."""

    def __init__(self, api_key: str, *, base_url: str = BASE_URL, timeout: float = 30.0) -> None:
        self._base_url = base_url
        self._client = httpx.Client(
            timeout=timeout,
            headers={"X-Api-Key": api_key},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CongressAPIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, url: str) -> dict[str, Any]:
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, url: str, items_key: str) -> Iterator[dict[str, Any]]:
        current_url: str | None = url
        while current_url:
            body = self._get(current_url)
            items = body.get(items_key, [])
            yield from items
            next_info = body.get("pagination", {}).get("next")
            current_url = next_info if next_info and items else None

    def iter_members(self, congress: int | None = None) -> Iterator[MemberRecord]:
        url = members_url(congress)
        for raw in self._paginate(url, "members"):
            yield normalize_member(raw, source_url=url)

    def iter_committees(self, congress: int, chamber: str | None = None) -> Iterator[CommitteeRecord]:
        url = committees_url(congress, chamber)
        for raw in self._paginate(url, "committees"):
            yield normalize_committee(raw, congress=congress, source_url=url)

    def iter_bills(self, congress: int, bill_type: str | None = None) -> Iterator[BillRecord]:
        url = bills_url(congress, bill_type)
        for raw in self._paginate(url, "bills"):
            yield normalize_bill(raw, source_url=url)

    def get_member_detail(self, bioguide_id: str) -> MemberRecord:
        detail = self.get_member_detail_payload(bioguide_id)
        return normalize_member(detail, source_url=member_detail_url(bioguide_id))

    def get_bill_detail(self, congress: int, bill_type: str, bill_number: int) -> BillRecord:
        detail = self.get_bill_detail_payload(congress, bill_type, bill_number)
        return normalize_bill(
            detail,
            source_url=bill_detail_url(congress, bill_type, bill_number),
        )

    def get_member_detail_payload(self, bioguide_id: str) -> dict[str, Any]:
        url = member_detail_url(bioguide_id)
        body = self._get(url)
        return body["member"]

    def get_bill_detail_payload(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
    ) -> dict[str, Any]:
        url = bill_detail_url(congress, bill_type, bill_number)
        body = self._get(url)
        return body["bill"]

    def iter_cosponsors(self, congress: int, bill_type: str, bill_number: int) -> Iterator[CosponsorRecord]:
        url = cosponsors_url(congress, bill_type, bill_number)
        for raw in self._paginate(url, "cosponsors"):
            yield normalize_cosponsor(
                raw,
                congress=congress,
                bill_type=bill_type,
                bill_number=bill_number,
                source_url=url,
            )
