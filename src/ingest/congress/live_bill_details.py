from __future__ import annotations

from collections.abc import Iterable

from src.load.congress import PrimarySponsorSpec

from .congress_api import CongressAPIClient
from .models import BillRecord
from .primary_sponsors import primary_sponsor_spec_from_bill_detail


def fetch_primary_sponsor_specs(
    client: CongressAPIClient,
    bills: Iterable[BillRecord],
) -> list[PrimarySponsorSpec]:
    results: list[PrimarySponsorSpec] = []
    for bill in bills:
        detail = client.get_bill_detail_payload(
            bill.congress,
            bill.bill_type,
            bill.bill_number,
        )
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        if spec is not None:
            results.append(spec)
    return results
