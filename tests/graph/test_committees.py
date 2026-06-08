from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.graph.committees import CommitteeRef


def test_committee_ref_normalizes() -> None:
    ref = CommitteeRef(jurisdiction_id="  US-Congress ", code=" HSAG ", chamber="House")
    assert ref.jurisdiction_id == "us-congress"
    assert ref.code == "hsag"
    assert ref.chamber == "house"


def test_canonical_id_prefixed_and_deterministic() -> None:
    ref = CommitteeRef(jurisdiction_id="us-congress", code="hsag", chamber="house")
    assert ref.canonical_id.startswith("cc-")
    assert (
        ref.canonical_id
        == CommitteeRef(jurisdiction_id="US-CONGRESS", code="HSAG", chamber="house").canonical_id
    )


def test_canonical_id_varies_by_field() -> None:
    base = CommitteeRef(jurisdiction_id="us-congress", code="hsag", chamber="house")
    assert (
        base.canonical_id
        != CommitteeRef(jurisdiction_id="us-congress", code="hsag", chamber="senate").canonical_id
    )
    assert (
        base.canonical_id
        != CommitteeRef(jurisdiction_id="us-congress", code="ssfr", chamber="house").canonical_id
    )


def test_chamber_optional() -> None:
    ref = CommitteeRef(jurisdiction_id="us-ca-city-los_angeles", code="budget")
    assert ref.chamber is None
    assert ref.canonical_id.startswith("cc-")


def test_invalid_chamber_rejected() -> None:
    with pytest.raises(ValidationError):
        CommitteeRef(jurisdiction_id="us-congress", code="hsag", chamber="upper")  # type: ignore[arg-type]


def test_blank_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        CommitteeRef(jurisdiction_id="", code="hsag", chamber="house")
    with pytest.raises(ValidationError):
        CommitteeRef(jurisdiction_id="us-congress", code="  ", chamber="house")


def test_non_string_fields_rejected() -> None:
    # Non-strings fall through the normalizers to pydantic type validation.
    with pytest.raises(ValidationError):
        CommitteeRef(jurisdiction_id=123, code="hsag")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CommitteeRef(jurisdiction_id="us-congress", code="hsag", chamber=5)  # type: ignore[arg-type]


def test_for_congress() -> None:
    ref = CommitteeRef.for_congress("house", "HSAG")
    assert ref.jurisdiction_id == "us-congress"
    assert ref.chamber == "house"
    assert ref.code == "hsag"


def test_is_frozen() -> None:
    ref = CommitteeRef.for_congress("house", "HSAG")
    with pytest.raises(ValidationError):
        ref.code = "ssfr"  # type: ignore[misc]
