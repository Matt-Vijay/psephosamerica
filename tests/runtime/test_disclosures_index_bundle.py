"""Tests for disclosures index-bundle parsing (runtime/disclosures_bundle.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.parse.disclosures.house_index import HouseFilingKind
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_bundle import (
    disclosures_index_bundle_from_dict,
    load_disclosures_index_bundle,
)

_HOUSE_DOC = {
    "last_name": "Smith",
    "first_name": "Pat",
    "suffix": "",
    "raw_filing_type": "P",
    "state_dst": "CA12",
    "year": 2024,
    "filing_date": "2024-05-15",
    "doc_id": "10012345",
    "filing_kind": "ptr",
}
_SENATE_DOC = {
    "first_name": "Sam",
    "last_name": "Jones",
    "office": "Senator Jones",
    "report_type": "Annual",
    "date_filed": "2024-05-15",
    "doc_id": "DOC777",
    "filing_year": 2024,
}
_VALID = {"house": {"2024": {"10012345": _HOUSE_DOC}}, "senate": {"2024": {"DOC777": _SENATE_DOC}}}


def test_index_bundle_from_dict_builds_house_and_senate_rows() -> None:
    lookup = disclosures_index_bundle_from_dict(_VALID)
    house_entries = lookup[("house", 2024)]
    senate_entries = lookup[("senate", 2024)]
    assert house_entries["10012345"].filing_kind is HouseFilingKind.PTR
    assert house_entries["10012345"].state_dst == "CA12"
    assert isinstance(senate_entries["DOC777"], SenateIndexRow)
    assert senate_entries["DOC777"].office == "Senator Jones"


def test_index_bundle_from_dict_skips_absent_chamber() -> None:
    lookup = disclosures_index_bundle_from_dict({"house": {"2024": {"10012345": _HOUSE_DOC}}})
    assert set(lookup) == {("house", 2024)}


@pytest.mark.parametrize(
    "payload,match",
    [
        (["not", "a", "dict"], "must be a JSON object"),
        ({"house": []}, r"\['house'\] must be an object"),
        ({"house": {"2024": []}}, r"\['2024'\] must be an object"),
        ({"house": {"2024": {"d1": "nope"}}}, r"\['d1'\] must be an object"),
    ],
)
def test_index_bundle_from_dict_rejects_malformed_shapes(payload: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        disclosures_index_bundle_from_dict(payload)  # type: ignore[arg-type]


def test_load_index_bundle_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "index-bundle.json"
    path.write_text(json.dumps(_VALID), encoding="utf-8")
    lookup = load_disclosures_index_bundle(path)
    assert lookup[("house", 2024)]["10012345"].doc_id == "10012345"


def test_load_index_bundle_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_disclosures_index_bundle(path)
