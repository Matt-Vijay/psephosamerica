from __future__ import annotations

import datetime
from typing import Any

from src.load.congress import MemberTermSpec

from .models import MemberRecord


def _parse_term_date(raw: Any) -> datetime.date | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 4 and s.isdigit():
        return datetime.date(int(s), 1, 3)
    return datetime.date.fromisoformat(s[:10])


def _chamber_from_raw(raw: str) -> str:
    if "senate" in raw.lower():
        return "senate"
    return "house"


def _identity_int(raw: Any, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be an integer")
    return int(raw)


def member_term_specs_from_detail(
    detail: dict[str, Any],
    member: MemberRecord,
) -> list[MemberTermSpec]:
    raw_terms = detail.get("terms", {})
    items: list[dict[str, Any]] = raw_terms.get("item", []) if isinstance(raw_terms, dict) else []

    specs: list[MemberTermSpec] = []
    for item in items:
        congress_raw = item.get("congress")
        if congress_raw is None:
            continue
        congress = _identity_int(congress_raw, "congress")

        start_date = _parse_term_date(item.get("startYear"))
        if start_date is None:
            continue

        end_date = _parse_term_date(item.get("endYear"))
        is_current = end_date is None

        chamber_raw = item.get("chamber", "")
        chamber = _chamber_from_raw(chamber_raw) if chamber_raw else member.chamber

        if chamber == "senate":
            district = None
        else:
            district_raw = item.get("district")
            district = _identity_int(district_raw, "district") if district_raw is not None else None

        state = item.get("stateCode") or member.state

        specs.append(
            MemberTermSpec(
                record=member,
                congress=congress,
                chamber=chamber,
                state=state,
                district=district,
                start_date=start_date,
                end_date=end_date,
                is_current=is_current,
            )
        )

    specs.sort(key=lambda s: s.start_date)
    return specs
