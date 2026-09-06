from __future__ import annotations

import pytest

from src.graph.bills import (
    CONGRESS_BILL_TYPES,
    BillRef,
    congress_bill_identifier,
    normalize_bill_identifier,
    parse_congress_bill_identifier,
)

# ── identifier normalization ───────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HR 1", "hr-1"),
        ("  H.R. 1  ", "hr-1"),
        ("SB-50", "sb-50"),
        ("Ordinance 2024/12", "ordinance-2024-12"),
        ("AB__99", "ab-99"),
    ],
)
def test_normalize_bill_identifier(raw: str, expected: str) -> None:
    assert normalize_bill_identifier(raw) == expected


def test_normalize_blank_identifier_raises() -> None:
    with pytest.raises(ValueError, match="identifier"):
        normalize_bill_identifier("   ")


# ── congress bill helpers ──────────────────────────────────────────


def test_congress_bill_types_cover_all_chambers() -> None:
    assert CONGRESS_BILL_TYPES == frozenset(
        {"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"}
    )


def test_congress_bill_identifier() -> None:
    assert congress_bill_identifier("HR", 1) == "hr-1"
    assert congress_bill_identifier("h.r.", 1) == "hr-1"


def test_congress_bill_identifier_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="bill type"):
        congress_bill_identifier("zz", 1)


def test_congress_bill_identifier_rejects_nonpositive_number() -> None:
    with pytest.raises(ValueError, match="number"):
        congress_bill_identifier("hr", 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("H.R. 1", "hr-1"),
        ("HR1", "hr-1"),
        ("S. 50", "s-50"),
        ("H.J.Res. 5", "hjres-5"),
        ("S.Con.Res. 12", "sconres-12"),
        ("H. Res. 100", "hres-100"),
    ],
)
def test_parse_congress_bill_identifier(text: str, expected: str) -> None:
    assert parse_congress_bill_identifier(text) == expected


@pytest.mark.parametrize("bad", ["", "just text", "HR", "123", "X.Y. 9"])
def test_parse_congress_bill_identifier_rejects_bad(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_congress_bill_identifier(bad)


# ── BillRef + canonical id ─────────────────────────────────────────


def test_billref_normalizes_fields() -> None:
    ref = BillRef(jurisdiction_id="  US-Congress ", session_id=" 118 ", identifier="H.R. 1")
    assert ref.jurisdiction_id == "us-congress"
    assert ref.session_id == "118"
    assert ref.identifier == "hr-1"


def test_billref_canonical_id_is_deterministic_and_prefixed() -> None:
    ref = BillRef(jurisdiction_id="us-congress", session_id="118", identifier="hr-1")
    assert ref.canonical_id.startswith("cb-")
    assert (
        ref.canonical_id
        == BillRef(jurisdiction_id="US-CONGRESS", session_id="118", identifier="HR 1").canonical_id
    )


def test_billref_canonical_id_varies_by_each_field() -> None:
    base = BillRef(jurisdiction_id="us-congress", session_id="118", identifier="hr-1")
    assert (
        base.canonical_id
        != BillRef(jurisdiction_id="ca-state", session_id="118", identifier="hr-1").canonical_id
    )
    assert (
        base.canonical_id
        != BillRef(jurisdiction_id="us-congress", session_id="117", identifier="hr-1").canonical_id
    )
    assert (
        base.canonical_id
        != BillRef(jurisdiction_id="us-congress", session_id="118", identifier="hr-2").canonical_id
    )


def test_billref_blank_fields_rejected() -> None:
    with pytest.raises(ValueError):
        BillRef(jurisdiction_id="", session_id="118", identifier="hr-1")
    with pytest.raises(ValueError):
        BillRef(jurisdiction_id="us", session_id="  ", identifier="hr-1")


def test_billref_non_string_fields_rejected() -> None:
    # Non-strings fall through the normalizers to pydantic's type validation.
    with pytest.raises(ValueError):
        BillRef(jurisdiction_id=123, session_id="118", identifier="hr-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BillRef(jurisdiction_id="us", session_id="118", identifier=5)  # type: ignore[arg-type]


def test_billref_for_congress() -> None:
    ref = BillRef.for_congress(118, "H.R. 1")
    assert ref.jurisdiction_id == "us-congress"
    assert ref.session_id == "118"
    assert ref.identifier == "hr-1"
    assert (
        ref.canonical_id
        == BillRef(jurisdiction_id="us-congress", session_id="118", identifier="hr-1").canonical_id
    )


def test_billref_is_frozen() -> None:
    from pydantic import ValidationError

    ref = BillRef.for_congress(118, "H.R. 1")
    with pytest.raises(ValidationError):
        ref.identifier = "hr-2"  # type: ignore[misc]
