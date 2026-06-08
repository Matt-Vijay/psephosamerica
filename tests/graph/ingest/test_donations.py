from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.donations import donation_edge, donation_provenance

_DONOR = "ce-donor1"
_RECIPIENT = "ce-committee1"


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://www.fec.gov/data/receipts/?sub_id=1",
        "content_sha256": "a" * 64,
        "contribution_date": date(2024, 1, 15),
        "report_filed_date": date(2024, 4, 15),
        "first_observed_at": datetime(2024, 4, 20, tzinfo=UTC),
    }
    base.update(overrides)
    return donation_provenance(**base)  # type: ignore[arg-type]


# ── disclosure-lag leakage rule ────────────────────────────────────


def test_donation_known_at_is_report_filing_not_contribution() -> None:
    prov = _prov()
    # Happened (valid) in January, disclosed (knowable) in April.
    assert prov.valid_from == date(2024, 1, 15)
    assert prov.known_at == datetime(2024, 4, 15, tzinfo=UTC)


def test_donation_is_not_knowable_between_gift_and_disclosure() -> None:
    edge = donation_edge(
        donor_canonical_id=_DONOR,
        recipient_canonical_id=_RECIPIENT,
        amount_cents=250000,
        transaction_id="SA11AI.1",
        provenance=_prov(),
    )
    # February: the donation has happened but is not yet disclosed -> invisible.
    assert edge.known_as_of(datetime(2024, 2, 1, tzinfo=UTC)) is False
    # May: disclosed -> visible.
    assert edge.known_as_of(datetime(2024, 5, 1, tzinfo=UTC)) is True


def test_donation_provenance_explicit_known_at() -> None:
    prov = _prov(known_at=datetime(2024, 4, 15, 9, 0, tzinfo=UTC))
    assert prov.known_at == datetime(2024, 4, 15, 9, 0, tzinfo=UTC)


# ── donation_edge mapping ──────────────────────────────────────────


def test_donation_edge_fields() -> None:
    edge = donation_edge(
        donor_canonical_id=_DONOR,
        recipient_canonical_id=_RECIPIENT,
        amount_cents=250000,
        transaction_id="SA11AI.1",
        provenance=_prov(),
    )
    assert edge.edge_type == "donation"
    assert edge.src_id == _DONOR
    assert edge.dst_id == _RECIPIENT
    assert edge.attributes == {"amount_cents": "250000"}
    assert edge.external_key == "SA11AI.1"


def test_same_day_donations_stay_distinct_via_transaction_id() -> None:
    a = donation_edge(
        donor_canonical_id=_DONOR,
        recipient_canonical_id=_RECIPIENT,
        amount_cents=100000,
        transaction_id="TXN-A",
        provenance=_prov(),
    )
    b = donation_edge(
        donor_canonical_id=_DONOR,
        recipient_canonical_id=_RECIPIENT,
        amount_cents=500000,
        transaction_id="TXN-B",
        provenance=_prov(),
    )
    assert a.edge_id != b.edge_id


@pytest.mark.parametrize("bad", [0, -1, -250000])
def test_nonpositive_amount_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="amount_cents"):
        donation_edge(
            donor_canonical_id=_DONOR,
            recipient_canonical_id=_RECIPIENT,
            amount_cents=bad,
            transaction_id="T",
            provenance=_prov(),
        )


def test_boolean_amount_rejected() -> None:
    with pytest.raises(ValueError, match="amount_cents"):
        donation_edge(
            donor_canonical_id=_DONOR,
            recipient_canonical_id=_RECIPIENT,
            amount_cents=True,  # type: ignore[arg-type]
            transaction_id="T",
            provenance=_prov(),
        )


def test_self_donation_rejected() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        donation_edge(
            donor_canonical_id="ce-x",
            recipient_canonical_id="ce-x",
            amount_cents=100,
            transaction_id="T",
            provenance=_prov(),
        )
