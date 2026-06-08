from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.graph.jurisdictions import Jurisdiction, slugify_place


# ── slugify ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Los Angeles", "los_angeles"),
        ("St. Louis", "st_louis"),
        ("DeKalb County", "dekalb_county"),
        ("  Côte  ", "cote"),
    ],
)
def test_slugify_place(raw: str, expected: str) -> None:
    assert slugify_place(raw) == expected


def test_slugify_blank_raises() -> None:
    with pytest.raises(ValueError, match="place"):
        slugify_place("   ")


# ── constructors + hierarchy ───────────────────────────────────────


def test_federal() -> None:
    fed = Jurisdiction.federal()
    assert fed.level == "federal"
    assert fed.code == "us"
    assert fed.parent_id is None


def test_state() -> None:
    ca = Jurisdiction.state("ca")
    assert ca.level == "state"
    assert ca.code == "us-ca"
    assert ca.parent_id == "us"


def test_state_uppercases_and_validates() -> None:
    assert Jurisdiction.state("CA").code == "us-ca"
    with pytest.raises(ValueError, match="USPS"):
        Jurisdiction.state("california")
    with pytest.raises(ValueError, match="USPS"):
        Jurisdiction.state("c1")


def test_county() -> None:
    cook = Jurisdiction.county("il", "Cook County")
    assert cook.level == "county"
    assert cook.code == "us-il-county-cook_county"
    assert cook.parent_id == "us-il"


def test_city() -> None:
    la = Jurisdiction.city("ca", "Los Angeles")
    assert la.level == "city"
    assert la.code == "us-ca-city-los_angeles"
    assert la.parent_id == "us-ca"


def test_special_district() -> None:
    sd = Jurisdiction.special_district("ca", "East Bay MUD")
    assert sd.level == "special_district"
    assert sd.code == "us-ca-sd-east_bay_mud"
    assert sd.parent_id == "us-ca"


def test_county_and_city_same_name_do_not_collide() -> None:
    assert Jurisdiction.county("ny", "New York").code != Jurisdiction.city("ny", "New York").code


# ── direct construction + validation ───────────────────────────────


def test_direct_construction_normalizes_code() -> None:
    j = Jurisdiction(level="state", code="  US-CA ", name="California", parent_id="us")
    assert j.code == "us-ca"


def test_blank_code_or_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Jurisdiction(level="state", code="  ", name="X", parent_id="us")
    with pytest.raises(ValidationError):
        Jurisdiction(level="state", code="us-ca", name="  ", parent_id="us")


def test_non_string_code_rejected() -> None:
    with pytest.raises(ValidationError):
        Jurisdiction(level="state", code=123, name="X", parent_id="us")  # type: ignore[arg-type]


def test_is_frozen() -> None:
    ca = Jurisdiction.state("ca")
    with pytest.raises(ValidationError):
        ca.code = "us-tx"  # type: ignore[misc]


# ── ancestry ───────────────────────────────────────────────────────


def test_is_descendant_of() -> None:
    la = Jurisdiction.city("ca", "Los Angeles")
    assert la.is_descendant_of(Jurisdiction.state("ca")) is True
    assert la.is_descendant_of(Jurisdiction.federal()) is False  # only checks direct parent
    assert la.is_descendant_of(Jurisdiction.state("tx")) is False


def test_federal_has_no_parent() -> None:
    assert Jurisdiction.federal().is_descendant_of(Jurisdiction.federal()) is False
