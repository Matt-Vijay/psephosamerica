from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.lobbying import lobbying_edge, lobbying_provenance

_CLIENT = "ce-acme-corp"
_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://lda.senate.gov/filings/1",
        "content_sha256": "a" * 64,
        "activity_date": date(2024, 1, 15),
        "filed_date": date(2024, 4, 20),
        "first_observed_at": datetime(2024, 4, 25, tzinfo=UTC),
    }
    base.update(overrides)
    return lobbying_provenance(**base)  # type: ignore[arg-type]


# ── disclosure-lag leakage ─────────────────────────────────────────


def test_known_at_is_filing_date() -> None:
    prov = _prov()
    assert prov.valid_from == date(2024, 1, 15)
    assert prov.known_at == datetime(2024, 4, 20, tzinfo=UTC)


def test_lobbying_invisible_until_filed() -> None:
    edge = lobbying_edge(
        client_canonical_id=_CLIENT,
        bill_canonical_id=_BILL,
        issue_area="Agriculture",
        filing_id="F1",
        provenance=_prov(),
    )
    assert edge.known_as_of(datetime(2024, 2, 1, tzinfo=UTC)) is False
    assert edge.known_as_of(datetime(2024, 5, 1, tzinfo=UTC)) is True


# ── edge mapping ───────────────────────────────────────────────────


def test_lobbying_edge_fields() -> None:
    edge = lobbying_edge(
        client_canonical_id=_CLIENT,
        bill_canonical_id=_BILL,
        issue_area="Agriculture",
        filing_id="F1",
        provenance=_prov(),
    )
    assert edge.edge_type == "lobbying_contact"
    assert edge.src_id == _CLIENT
    assert edge.dst_id == _BILL
    assert edge.attributes["issue_area"] == "Agriculture"
    assert edge.external_key == "F1"
    assert "amount_cents" not in edge.attributes


def test_lobbying_edge_optional_amount_and_registrant() -> None:
    edge = lobbying_edge(
        client_canonical_id=_CLIENT,
        bill_canonical_id=_BILL,
        issue_area="Agriculture",
        filing_id="F1",
        amount_cents=5000000,
        registrant="Big Lobby LLC",
        provenance=_prov(),
    )
    assert edge.attributes["amount_cents"] == "5000000"
    assert edge.attributes["registrant"] == "Big Lobby LLC"


def test_blank_issue_area_rejected() -> None:
    with pytest.raises(ValueError, match="issue_area"):
        lobbying_edge(
            client_canonical_id=_CLIENT,
            bill_canonical_id=_BILL,
            issue_area="  ",
            filing_id="F1",
            provenance=_prov(),
        )


@pytest.mark.parametrize("bad", [0, -1])
def test_nonpositive_amount_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="amount_cents"):
        lobbying_edge(
            client_canonical_id=_CLIENT,
            bill_canonical_id=_BILL,
            issue_area="Agriculture",
            filing_id="F1",
            amount_cents=bad,
            provenance=_prov(),
        )


def test_boolean_amount_rejected() -> None:
    with pytest.raises(ValueError, match="amount_cents"):
        lobbying_edge(
            client_canonical_id=_CLIENT,
            bill_canonical_id=_BILL,
            issue_area="Agriculture",
            filing_id="F1",
            amount_cents=True,  # type: ignore[arg-type]
            provenance=_prov(),
        )


def test_distinct_filings_stay_separate() -> None:
    a = lobbying_edge(
        client_canonical_id=_CLIENT,
        bill_canonical_id=_BILL,
        issue_area="Agriculture",
        filing_id="F1",
        provenance=_prov(),
    )
    b = lobbying_edge(
        client_canonical_id=_CLIENT,
        bill_canonical_id=_BILL,
        issue_area="Agriculture",
        filing_id="F2",
        provenance=_prov(),
    )
    assert a.edge_id != b.edge_id
