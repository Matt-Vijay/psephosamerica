from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.sponsorships import sponsorship_edge, sponsorship_provenance

_MEMBER = "ce-person1"
_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://www.congress.gov/bill/118th/house-bill/1/cosponsors",
        "content_sha256": "a" * 64,
        "action_date": date(2024, 1, 10),
        "first_observed_at": datetime(2024, 1, 12, tzinfo=UTC),
    }
    base.update(overrides)
    return sponsorship_provenance(**base)  # type: ignore[arg-type]


# ── role -> edge type ──────────────────────────────────────────────


def test_sponsor_role_maps_to_sponsorship_edge() -> None:
    edge = sponsorship_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, role="sponsor", provenance=_prov()
    )
    assert edge.edge_type == "sponsorship"
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _BILL


def test_cosponsor_role_maps_to_cosponsorship_edge() -> None:
    edge = sponsorship_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, role="cosponsor", provenance=_prov()
    )
    assert edge.edge_type == "cosponsorship"


def test_role_is_case_insensitive() -> None:
    assert (
        sponsorship_edge(
            member_canonical_id=_MEMBER,
            bill_canonical_id=_BILL,
            role="CoSponsor",
            provenance=_prov(),
        ).edge_type
        == "cosponsorship"
    )


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError, match="role"):
        sponsorship_edge(
            member_canonical_id=_MEMBER, bill_canonical_id=_BILL, role="author", provenance=_prov()
        )


# ── leakage gate ───────────────────────────────────────────────────


def test_known_at_defaults_to_action_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2024, 1, 10)
    assert prov.known_at == datetime(2024, 1, 10, tzinfo=UTC)


def test_sponsorship_leakage_gate() -> None:
    edge = sponsorship_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, role="sponsor", provenance=_prov()
    )
    assert edge.known_as_of(datetime(2024, 1, 10, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 1, 9, tzinfo=UTC)) is False


# ── withdrawal: valid_to window ────────────────────────────────────


def test_active_cosponsorship_is_open_ended() -> None:
    edge = sponsorship_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, role="cosponsor", provenance=_prov()
    )
    assert edge.covers(date(2024, 1, 10)) is True
    assert edge.covers(date(2030, 1, 1)) is True  # still in effect


def test_withdrawn_cosponsorship_has_closed_window() -> None:
    edge = sponsorship_edge(
        member_canonical_id=_MEMBER,
        bill_canonical_id=_BILL,
        role="cosponsor",
        provenance=_prov(withdrawn_date=date(2024, 3, 1)),
    )
    assert edge.covers(date(2024, 2, 1)) is True  # during the cosponsorship
    assert edge.covers(date(2024, 3, 1)) is False  # withdrawn (exclusive end)
    assert edge.covers(date(2024, 4, 1)) is False


def test_withdrawn_before_sponsored_is_rejected() -> None:
    with pytest.raises(ValueError):
        _prov(action_date=date(2024, 3, 1), withdrawn_date=date(2024, 1, 1))


def test_explicit_known_at() -> None:
    prov = _prov(known_at=datetime(2024, 1, 10, 14, 0, tzinfo=UTC))
    assert prov.known_at == datetime(2024, 1, 10, 14, 0, tzinfo=UTC)
