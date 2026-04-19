from __future__ import annotations

import datetime
from typing import Any

from src.ingest.congress.models import BillRecord
from src.load.congress import PrimarySponsorSpec


def primary_sponsor_spec_from_bill_detail(
    detail: dict[str, Any],
    bill: BillRecord,
) -> PrimarySponsorSpec | None:
    bill_node = detail.get("bill", detail)
    if not isinstance(bill_node, dict):
        return None
    sponsors = bill_node.get("sponsors") or []
    if not isinstance(sponsors, list) or not sponsors:
        return None

    first = sponsors[0]
    if not isinstance(first, dict):
        return None
    bioguide_id = _usable_bioguide_id(first)
    if bioguide_id is None:
        return None

    sponsor_date = _pick_sponsor_date(first, bill)
    source_url = first.get("url") or bill.source_url

    return PrimarySponsorSpec(
        record=bill,
        bioguide_id=bioguide_id,
        sponsor_date=sponsor_date,
        source_url=source_url,
    )

def _usable_bioguide_id(sponsor_entry: dict[str, Any]) -> str | None:
    raw = sponsor_entry.get("bioguideId")
    if not raw or not str(raw).strip():
        return None
    return str(raw).strip()


def _pick_sponsor_date(
    sponsor_entry: dict[str, Any],
    bill: BillRecord,
) -> datetime.date | None:
    raw = sponsor_entry.get("sponsorshipDate")
    if raw:
        return _parse_date(raw)
    return bill.introduced_date


def _parse_date(raw: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None
