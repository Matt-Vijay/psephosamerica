from __future__ import annotations

from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS, LegistarClient


def test_at_least_120_clients() -> None:
    assert len(LEGISTAR_CLIENTS) >= 120


def test_client_codes_unique() -> None:
    codes = [c.client for c in LEGISTAR_CLIENTS]
    assert len(codes) == len(set(codes))


def test_jurisdiction_codes_unique() -> None:
    codes = [c.jurisdiction().code for c in LEGISTAR_CLIENTS]
    assert len(codes) == len(set(codes))


def test_every_client_builds_a_valid_jurisdiction() -> None:
    for c in LEGISTAR_CLIENTS:
        j = c.jurisdiction()
        assert j.code.startswith("us-")
        assert j.level in ("city", "county")
        assert j.parent_id == f"us-{c.state}"


def test_city_and_county_levels_present() -> None:
    levels = {c.level for c in LEGISTAR_CLIENTS}
    assert levels == {"city", "county"}


def test_jurisdiction_for_known_client() -> None:
    seattle = next(c for c in LEGISTAR_CLIENTS if c.client == "seattle")
    assert seattle.jurisdiction().code == "us-wa-city-seattle"
    king = next(c for c in LEGISTAR_CLIENTS if c.client == "kingcounty")
    assert king.jurisdiction().code == "us-wa-county-king_county"


def test_states_span_many() -> None:
    states = {c.state for c in LEGISTAR_CLIENTS}
    assert len(states) >= 12  # broad geographic coverage


def test_dataclass_is_frozen() -> None:
    import pytest

    c = LegistarClient("x", "city", "ca", "X")
    with pytest.raises(Exception):
        c.client = "y"  # type: ignore[misc]
