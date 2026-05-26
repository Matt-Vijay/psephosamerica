"""Congress.gov API v3 client — URL builders, response normalization, paginating iterators.

No DB writes; all public helpers return typed ingest records from models.py.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Iterator, Literal
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from .models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
)

BASE_URL = "https://api.congress.gov/v3/"
DEFAULT_LIMIT = 250
MAX_API_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_PAGINATION_PAGES = 1_000
_HTTPX_CLIENT_CLS = httpx.Client
MemberChamber = Literal["house", "senate"]
CommitteeChamber = Literal["house", "senate", "joint"]
CommitteeType = Literal["standing", "select", "joint", "subcommittee", "other"]
BillType = Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]


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


def safe_pagination_next(
    current_url: str,
    next_url: str,
    *,
    base_url: str = BASE_URL,
) -> str:
    """Return a normalized same-origin Congress.gov pagination URL."""
    resolved = urljoin(current_url, next_url)
    parsed = urlparse(resolved)
    base = urlparse(base_url)
    if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
        raise ValueError(f"pagination.next points off origin: {next_url!r}")
    base_path = base.path if base.path.endswith("/") else f"{base.path}/"
    if not parsed.path.startswith(base_path):
        raise ValueError(f"pagination.next escapes API base path: {next_url!r}")
    return resolved


def next_pagination_url(
    current_url: str,
    next_url: object,
    *,
    emitted: bool,
    seen_urls: set[str],
    page_count: int,
    base_url: str = BASE_URL,
    max_pages: int | None = None,
) -> str | None:
    """Validate and return the next Congress.gov page URL, if any."""
    if not emitted or not isinstance(next_url, str):
        return None
    page_limit = MAX_PAGINATION_PAGES if max_pages is None else max_pages
    if page_count >= page_limit:
        raise ValueError(f"pagination exceeded maximum page count: {page_limit}")
    resolved = safe_pagination_next(current_url, next_url, base_url=base_url)
    if resolved in seen_urls:
        raise ValueError(f"pagination.next repeated URL: {next_url!r}")
    return resolved


def _validate_congress_api_url(
    url: str,
    *,
    base_url: str = BASE_URL,
    context: str = "Congress API URL",
) -> None:
    parsed = urlparse(url)
    base = urlparse(base_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{context} has invalid port: {url!r}") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.params
        or port not in (None, 443)
    ):
        raise ValueError(f"unsupported {context}: {url!r}")
    if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
        raise ValueError(f"{context} points off origin: {url!r}")
    base_path = base.path if base.path.endswith("/") else f"{base.path}/"
    if not parsed.path.startswith(base_path):
        raise ValueError(f"{context} escapes API base path: {url!r}")


def _validate_congress_api_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    official = urlparse(BASE_URL)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Congress API base URL has invalid port: {base_url!r}") from exc
    if (
        parsed.scheme != official.scheme
        or parsed.hostname != official.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.params
        or port not in (None, 443)
    ):
        raise ValueError(f"unsupported Congress API base URL: {base_url!r}")
    official_path = official.path if official.path.endswith("/") else f"{official.path}/"
    parsed_path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
    if parsed_path != official_path:
        raise ValueError(f"unsupported Congress API base URL: {base_url!r}")


def _content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    get = getattr(headers, "get", None)
    if get is None:
        return None
    raw = get("content-length")
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    if not isinstance(raw, (str, int)):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _validate_congress_api_response(
    response: Any,
    *,
    request_url: str,
    base_url: str,
    max_bytes: int = MAX_API_RESPONSE_BYTES,
) -> None:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and 300 <= status_code < 400:
        raise ValueError(f"redirect response rejected for Congress API URL: {request_url!r}")

    response_url = getattr(response, "url", None)
    if isinstance(response_url, (str, httpx.URL)):
        _validate_congress_api_url(
            str(response_url),
            base_url=base_url,
            context="Congress API response URL",
        )

    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise ValueError(
            f"Congress API response exceeds maximum size of {max_bytes} bytes: {length}"
        )
    try:
        content = getattr(response, "content", None)
    except httpx.ResponseNotRead:
        content = None
    if isinstance(content, bytes) and len(content) > max_bytes:
        raise ValueError(f"Congress API response exceeds maximum size of {max_bytes} bytes")


def _bounded_congress_response_bytes(response: Any, *, max_bytes: int) -> bytes:
    try:
        content = getattr(response, "content", None)
    except httpx.ResponseNotRead:
        content = None
    if isinstance(content, bytes):
        if len(content) > max_bytes:
            raise ValueError(f"Congress API response exceeds maximum size of {max_bytes} bytes")
        return content

    iter_bytes = getattr(response, "iter_bytes", None)
    if not callable(iter_bytes):
        raise ValueError("Congress API response did not provide a readable body")

    total = 0
    chunks: list[bytes] = []
    for chunk in iter_bytes():
        if not isinstance(chunk, bytes):
            raise ValueError("Congress API response yielded non-bytes chunk")
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Congress API response exceeds maximum size of {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_congress_api_json(
    response: Any,
    *,
    request_url: str,
    base_url: str,
    max_bytes: int,
) -> dict[str, Any]:
    _validate_congress_api_response(
        response,
        request_url=request_url,
        base_url=base_url,
        max_bytes=max_bytes,
    )
    response.raise_for_status()
    content = _bounded_congress_response_bytes(response, max_bytes=max_bytes)
    return _json_object(json.loads(content.decode("utf-8")), context=f"response from {request_url}")


def _parse_date(raw: Any) -> datetime.date | None:
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) == 4 and text.isdigit():
        return datetime.date(int(text), 1, 3)
    return datetime.date.fromisoformat(text[:10])


def _normalize_member_chamber(raw: object | None) -> MemberChamber:
    if raw is None:
        return "house"
    lower = str(raw).lower().strip()
    if lower in ("senate", "s"):
        return "senate"
    if "house" not in lower and "representative" not in lower and lower not in ("", "h"):
        raise ValueError(f"unsupported member chamber: {raw!r}")
    return "house"


def _normalize_committee_chamber(raw: object | None) -> CommitteeChamber:
    if raw is None:
        return "house"
    lower = str(raw).lower().strip()
    if "joint" in lower or lower == "j":
        return "joint"
    if "senate" in lower or lower == "s":
        return "senate"
    if not lower or "house" in lower or "representative" in lower or lower == "h":
        return "house"
    raise ValueError(f"unsupported committee chamber: {raw!r}")


def _normalize_committee_type(raw: object | None) -> CommitteeType:
    mapping: dict[str, CommitteeType] = {
        "standing": "standing",
        "select": "select",
        "joint": "joint",
        "subcommittee": "subcommittee",
        "other": "other",
    }
    normalized = str(raw or "other").lower().strip()
    return mapping.get(normalized, "other")


def _normalize_bill_type(raw: object) -> BillType:
    mapping: dict[str, BillType] = {
        "hr": "hr",
        "s": "s",
        "hjres": "hjres",
        "sjres": "sjres",
        "hconres": "hconres",
        "sconres": "sconres",
        "hres": "hres",
        "sres": "sres",
    }
    normalized = str(raw).lower().replace(".", "")
    if normalized not in mapping:
        raise ValueError(f"unsupported bill type: {raw!r}")
    return mapping[normalized]


def _json_object(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object, got {type(value).__name__}")
    return value


def _identity_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return int(value)


def normalize_member(data: dict[str, Any], *, source_url: str | None = None) -> MemberRecord:
    terms_node = _json_object(data.get("terms", {}), context="member.terms")
    raw_items = terms_node.get("item", [])
    terms = (
        [item for item in raw_items if isinstance(item, dict)]
        if isinstance(raw_items, list)
        else []
    )
    latest_term = terms[-1] if terms else {}
    fallback_term = terms[0] if terms else {}
    return MemberRecord(
        bioguide_id=data["bioguideId"],
        first_name=data.get("firstName", data.get("directOrderName", "").split(",")[0].strip()),
        last_name=data.get("lastName", ""),
        full_name=data.get(
            "directOrderName", f"{data.get('firstName', '')} {data.get('lastName', '')}"
        ),
        chamber=_normalize_member_chamber(latest_term.get("chamber", fallback_term.get("chamber"))),
        party=data.get("partyName"),
        state=data.get("state"),
        middle_name=data.get("middleName"),
        lis_member_id=data.get("lisId"),
        current_term_start=_parse_date(latest_term.get("startYear")),
        current_term_end=_parse_date(latest_term.get("endYear")),
        is_current=data.get("currentMember", False),
        source_url=source_url,
    )


def normalize_committee(
    data: dict[str, Any], *, congress: int, source_url: str | None = None
) -> CommitteeRecord:
    chamber_raw = data.get("chamber", {})
    chamber_name = (
        chamber_raw
        if isinstance(chamber_raw, str)
        else chamber_raw.get("name", "")
        if isinstance(chamber_raw, dict)
        else ""
    )
    parent_committee = data.get("parentCommittee")
    parent_committee_code = (
        parent_committee.get("systemCode") if isinstance(parent_committee, dict) else None
    )
    return CommitteeRecord(
        committee_code=data["systemCode"],
        congress=congress,
        chamber=_normalize_committee_chamber(chamber_name),
        committee_type=_normalize_committee_type(data.get("committeeTypeCode", "other")),
        name=data.get("name", ""),
        parent_committee_code=parent_committee_code,
        source_url=source_url,
    )


def normalize_bill(data: dict[str, Any], *, source_url: str | None = None) -> BillRecord:
    latest_action = data.get("latestAction", {})
    latest_action_node = latest_action if isinstance(latest_action, dict) else {}
    return BillRecord(
        congress=_identity_int(data["congress"], "congress"),
        bill_type=_normalize_bill_type(data["type"]),
        bill_number=_identity_int(data["number"], "number"),
        title=data.get("title", ""),
        short_title=data.get("shortTitle"),
        introduced_date=_parse_date(data.get("introducedDate")),
        latest_action_date=_parse_date(latest_action_node.get("actionDate")),
        current_status=latest_action_node.get("text"),
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
        if not api_key.strip():
            raise ValueError("api_key is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        _validate_congress_api_base_url(base_url)
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
        _validate_congress_api_url(url, base_url=self._base_url)
        if isinstance(self._client, _HTTPX_CLIENT_CLS):
            with self._client.stream("GET", url, follow_redirects=False) as resp:
                return _decode_congress_api_json(
                    resp,
                    request_url=url,
                    base_url=self._base_url,
                    max_bytes=MAX_API_RESPONSE_BYTES,
                )

        resp = self._client.get(url, follow_redirects=False)
        _validate_congress_api_response(
            resp,
            request_url=url,
            base_url=self._base_url,
            max_bytes=MAX_API_RESPONSE_BYTES,
        )
        resp.raise_for_status()
        return _json_object(resp.json(), context=f"response from {url}")

    def _paginate(self, url: str, items_key: str) -> Iterator[tuple[dict[str, Any], str]]:
        current_url: str | None = url
        seen_urls: set[str] = set()
        page_count = 0
        while current_url:
            if current_url in seen_urls:
                raise ValueError(f"pagination.next repeated URL: {current_url!r}")
            seen_urls.add(current_url)
            page_count += 1
            body = self._get(current_url)
            items = body.get(items_key, [])
            emitted = False
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        emitted = True
                        yield item, current_url
            pagination = body.get("pagination", {})
            pagination_obj = pagination if isinstance(pagination, dict) else {}
            next_info = pagination_obj.get("next")
            current_url = next_pagination_url(
                current_url,
                next_info,
                emitted=emitted,
                seen_urls=seen_urls,
                page_count=page_count,
                base_url=self._base_url,
            )

    def iter_members(self, congress: int | None = None) -> Iterator[MemberRecord]:
        url = members_url(congress)
        for raw, source_url in self._paginate(url, "members"):
            yield normalize_member(raw, source_url=source_url)

    def iter_committees(
        self, congress: int, chamber: str | None = None
    ) -> Iterator[CommitteeRecord]:
        url = committees_url(congress, chamber)
        for raw, source_url in self._paginate(url, "committees"):
            yield normalize_committee(raw, congress=congress, source_url=source_url)

    def iter_bills(self, congress: int, bill_type: str | None = None) -> Iterator[BillRecord]:
        url = bills_url(congress, bill_type)
        for raw, source_url in self._paginate(url, "bills"):
            yield normalize_bill(raw, source_url=source_url)

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
        return _json_object(body["member"], context=f"member detail payload for {bioguide_id}")

    def get_bill_detail_payload(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
    ) -> dict[str, Any]:
        url = bill_detail_url(congress, bill_type, bill_number)
        body = self._get(url)
        return _json_object(
            body["bill"],
            context=f"bill detail payload for {congress}/{bill_type}/{bill_number}",
        )

    def iter_cosponsors(
        self, congress: int, bill_type: str, bill_number: int
    ) -> Iterator[CosponsorRecord]:
        url = cosponsors_url(congress, bill_type, bill_number)
        for raw, source_url in self._paginate(url, "cosponsors"):
            yield normalize_cosponsor(
                raw,
                congress=congress,
                bill_type=bill_type,
                bill_number=bill_number,
                source_url=source_url,
            )
