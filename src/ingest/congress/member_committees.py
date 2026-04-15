"""Extract committee memberships from Congress.gov member detail payloads.

Pure helpers only — no DB writes, no network calls.
"""

from __future__ import annotations

import datetime
from typing import Any

from src.ingest.congress.models import MemberRecord
from src.load.congress import CommitteeMembershipSpec


# Maps Congress.gov role strings (lowercased) to the canonical values used by
# the load layer.  Anything not in this table falls back to "member".
_ROLE_MAP: dict[str, str] = {
    "chair": "chair",
    "chairman": "chair",
    "chairwoman": "chair",
    "vice chair": "vice_chair",
    "vice chairman": "vice_chair",
    "vice chairwoman": "vice_chair",
    "ranking member": "ranking_member",
    "ranking minority member": "ranking_member",
    "ex officio": "ex_officio",
    "member": "member",
}


def _normalize_role(raw: str | None) -> str:
    if not raw:
        return "member"
    return _ROLE_MAP.get(raw.strip().lower(), "member")


def _parse_date(raw: str | None) -> datetime.date | None:
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def committee_membership_specs_from_detail(
    detail: dict[str, Any],
    member: MemberRecord,
) -> list[CommitteeMembershipSpec]:
    """Build CommitteeMembershipSpec list from one Congress.gov member detail payload.

    `detail` is the inner ``member`` object, already unwrapped from the API
    envelope (i.e. ``response["member"]``).

    Rows are silently dropped when:
    - the committee item has no ``systemCode`` (committee code is unresolvable)
    - the congress number is missing or non-integer
    - ``startDate`` is absent or unparseable (required field, part of UNIQUE key)
    """
    raw_items: list[dict[str, Any]] = detail.get("committees", {}).get("item", [])
    specs: list[CommitteeMembershipSpec] = []

    for item in raw_items:
        committee_data: dict[str, Any] = item.get("committee") or {}
        committee_code: str = committee_data.get("systemCode") or ""
        if not committee_code:
            continue

        congress_raw = item.get("congress")
        if congress_raw is None:
            continue
        try:
            congress = int(congress_raw)
        except (TypeError, ValueError):
            continue

        start_date = _parse_date(item.get("startDate"))
        if start_date is None:
            continue

        specs.append(
            CommitteeMembershipSpec(
                bioguide_id=member.bioguide_id,
                committee_code=committee_code,
                congress=congress,
                role=_normalize_role(item.get("role")),
                start_date=start_date,
                end_date=_parse_date(item.get("endDate")),
                is_current=bool(item.get("isCurrent", False)),
                source_url=committee_data.get("url"),
            )
        )

    return specs
