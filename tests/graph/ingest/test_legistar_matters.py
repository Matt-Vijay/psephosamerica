from __future__ import annotations

from datetime import date

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.legistar_matters import (
    LegistarMatter,
    matter_bill_canonical_id,
    parse_legistar_matter,
)

_JURISDICTION = "us-wa-city-seattle"

_RECORD = {
    "MatterId": 17000,
    "MatterFile": "CB 120555",
    "MatterName": "An ordinance relating to land use",
    "MatterTypeName": "Ordinance (Ord)",
    "MatterStatusName": "Passed",
    "MatterIntroDate": "2024-03-12T00:00:00",
}


def test_parse_matter() -> None:
    matter = parse_legistar_matter(_RECORD)
    assert isinstance(matter, LegistarMatter)
    assert matter.matter_id == 17000
    assert matter.file == "CB 120555"
    assert matter.matter_type == "Ordinance (Ord)"
    assert matter.status == "Passed"
    assert matter.intro_date == date(2024, 3, 12)


def test_matter_with_missing_optional_fields() -> None:
    matter = parse_legistar_matter({"MatterId": 1, "MatterFile": "RES 1"})
    assert matter.matter_id == 1
    assert matter.intro_date is None
    assert matter.matter_type == ""


def test_missing_matter_id_rejected() -> None:
    with pytest.raises(ValueError, match="MatterId"):
        parse_legistar_matter({"MatterFile": "CB 1"})


def test_invalid_intro_date_is_none() -> None:
    matter = parse_legistar_matter({"MatterId": 1, "MatterFile": "CB 1", "MatterIntroDate": "n/a"})
    assert matter.intro_date is None


def test_malformed_long_intro_date_is_none() -> None:
    # A string long enough to slice but not a real date.
    matter = parse_legistar_matter(
        {"MatterId": 1, "MatterFile": "CB 1", "MatterIntroDate": "2024-99-99T00:00:00"}
    )
    assert matter.intro_date is None


def test_non_string_intro_date_is_none() -> None:
    matter = parse_legistar_matter({"MatterId": 1, "MatterFile": "CB 1", "MatterIntroDate": 12345})
    assert matter.intro_date is None


# ── canonical municipal bill ID ────────────────────────────────────


def test_matter_bill_canonical_id() -> None:
    matter = parse_legistar_matter(_RECORD)
    bill_id = matter_bill_canonical_id(matter, jurisdiction_code=_JURISDICTION)
    assert bill_id.startswith("cb-")
    # Same triple via BillRef directly.
    assert (
        bill_id
        == BillRef(
            jurisdiction_id=_JURISDICTION, session_id="2024", identifier="CB 120555"
        ).canonical_id
    )


def test_canonical_id_varies_by_city() -> None:
    matter = parse_legistar_matter(_RECORD)
    seattle = matter_bill_canonical_id(matter, jurisdiction_code="us-wa-city-seattle")
    oakland = matter_bill_canonical_id(matter, jurisdiction_code="us-ca-city-oakland")
    assert seattle != oakland


def test_matter_without_file_cannot_make_bill_id() -> None:
    matter = parse_legistar_matter({"MatterId": 9, "MatterFile": ""})
    with pytest.raises(ValueError, match="file"):
        matter_bill_canonical_id(matter, jurisdiction_code=_JURISDICTION)


def test_matter_without_intro_date_uses_unknown_session() -> None:
    matter = parse_legistar_matter({"MatterId": 9, "MatterFile": "CB 9"})
    bill_id = matter_bill_canonical_id(matter, jurisdiction_code=_JURISDICTION)
    assert (
        bill_id
        == BillRef(
            jurisdiction_id=_JURISDICTION, session_id="unknown", identifier="CB 9"
        ).canonical_id
    )
