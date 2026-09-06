from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.disclosures import disclosure_provenance, financial_interest_edge

_MEMBER = "ce-person1"
_ISSUER = "ci-acme"


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://disclosures-clerk.house.gov/filing/1",
        "content_sha256": "a" * 64,
        "as_of_date": date(2023, 7, 1),
        "filed_date": date(2024, 5, 15),
        "first_observed_at": datetime(2024, 5, 20, tzinfo=UTC),
    }
    base.update(overrides)
    return disclosure_provenance(**base)  # type: ignore[arg-type]


# ── disclosure-lag leakage rule ────────────────────────────────────


def test_known_at_is_filing_not_holding_date() -> None:
    prov = _prov()
    # Held as of July 2023, disclosed May 2024.
    assert prov.valid_from == date(2023, 7, 1)
    assert prov.known_at == datetime(2024, 5, 15, tzinfo=UTC)


def test_interest_invisible_until_disclosed() -> None:
    edge = financial_interest_edge(
        member_canonical_id=_MEMBER,
        issuer_canonical_id=_ISSUER,
        interest_type="holding",
        amount_range="$1,001-$15,000",
        provenance=_prov(),
    )
    assert edge.known_as_of(datetime(2023, 8, 1, tzinfo=UTC)) is False  # held, not yet disclosed
    assert edge.known_as_of(datetime(2024, 6, 1, tzinfo=UTC)) is True  # disclosed


def test_explicit_known_at() -> None:
    prov = _prov(known_at=datetime(2024, 5, 15, 12, 0, tzinfo=UTC))
    assert prov.known_at == datetime(2024, 5, 15, 12, 0, tzinfo=UTC)


# ── edge mapping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("holding", "holding"),
        ("Asset", "holding"),
        ("purchase", "purchase"),
        ("buy", "purchase"),
        ("sale", "sale"),
        ("sold", "sale"),
        ("exchange", "exchange"),
    ],
)
def test_interest_type_normalization(raw: str, expected: str) -> None:
    edge = financial_interest_edge(
        member_canonical_id=_MEMBER,
        issuer_canonical_id=_ISSUER,
        interest_type=raw,
        amount_range="$1,001-$15,000",
        provenance=_prov(),
    )
    assert edge.edge_type == "financial_interest"
    assert edge.attributes["interest_type"] == expected
    assert edge.attributes["amount_range"] == "$1,001-$15,000"
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _ISSUER


def test_unknown_interest_type_rejected() -> None:
    with pytest.raises(ValueError, match="interest type"):
        financial_interest_edge(
            member_canonical_id=_MEMBER,
            issuer_canonical_id=_ISSUER,
            interest_type="vibes",
            amount_range="$1-$2",
            provenance=_prov(),
        )


def test_blank_amount_range_rejected() -> None:
    with pytest.raises(ValueError, match="amount_range"):
        financial_interest_edge(
            member_canonical_id=_MEMBER,
            issuer_canonical_id=_ISSUER,
            interest_type="holding",
            amount_range="   ",
            provenance=_prov(),
        )


def test_transaction_id_distinguishes_same_day_transactions() -> None:
    a = financial_interest_edge(
        member_canonical_id=_MEMBER,
        issuer_canonical_id=_ISSUER,
        interest_type="purchase",
        amount_range="$1,001-$15,000",
        transaction_id="T1",
        provenance=_prov(),
    )
    b = financial_interest_edge(
        member_canonical_id=_MEMBER,
        issuer_canonical_id=_ISSUER,
        interest_type="sale",
        amount_range="$1,001-$15,000",
        transaction_id="T2",
        provenance=_prov(),
    )
    assert a.edge_id != b.edge_id


def test_self_interest_rejected() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        financial_interest_edge(
            member_canonical_id="ce-x",
            issuer_canonical_id="ce-x",
            interest_type="holding",
            amount_range="$1-$2",
            provenance=_prov(),
        )
