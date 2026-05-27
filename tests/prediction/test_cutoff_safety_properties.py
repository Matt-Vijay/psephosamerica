"""Property/fuzz tests for the cutoff-safety core (no-leakage invariant).

These use seeded stdlib randomness (hypothesis is not available offline) to
exercise the availability predicates over many date/cutoff combinations. They
pin the safety-critical invariant that a signal is admitted iff it can be
*proven* to predate the feature cutoff.
"""

from __future__ import annotations

import datetime as dt
import random

from src.export.contracts import SourceAnchor
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.backtest import (
    _EDGE_AVAILABILITY_DATE_KEYS,
    _date_from_value,
    _edge_available_at_or_before,
    _edge_has_known_availability,
    _signal_row_available_at_or_before,
)

_KEYS = ("contribution_date", "date", "as_of_date")


def _rand_date(rng: random.Random) -> dt.date:
    return dt.date(rng.randint(2000, 2030), rng.randint(1, 12), rng.randint(1, 28))


def test_date_from_value_roundtrips_dates_and_iso_strings_and_rejects_junk() -> None:
    rng = random.Random(1234)
    for _ in range(500):
        d = _rand_date(rng)
        assert _date_from_value(d) == d
        assert _date_from_value(d.isoformat()) == d
    assert _date_from_value(None) is None
    assert _date_from_value("not-a-date") is None
    assert _date_from_value("2024-13-45") is None  # out-of-range -> None


def test_signal_availability_follows_first_parseable_date() -> None:
    rng = random.Random(99)
    for _ in range(2000):
        cutoff = _rand_date(rng)
        row: dict[str, object] = {}
        for key in _KEYS:
            r = rng.random()
            if r < 0.45:
                row[key] = _rand_date(rng).isoformat()
            elif r < 0.55:
                row[key] = "unparseable"  # present but not a date -> skipped

        result = _signal_row_available_at_or_before(row, cutoff, _KEYS)

        # Expected: the first key (in _KEYS order) with a parseable date decides;
        # if none is parseable, the row is conservatively unavailable.
        first_date = next(
            (d for d in (_date_from_value(row.get(k)) for k in _KEYS) if d is not None),
            None,
        )
        if first_date is None:
            assert result is False
        else:
            assert result == (first_date <= cutoff)


def test_signal_availability_is_monotonic_in_cutoff() -> None:
    # If a row is available at cutoff C, it must be available at every C' >= C
    # (and conversely unavailable cutoffs stay unavailable as they decrease).
    rng = random.Random(7)
    for _ in range(1000):
        row = {"date": _rand_date(rng).isoformat()}
        cutoff = _rand_date(rng)
        available = _signal_row_available_at_or_before(row, cutoff, _KEYS)
        if available:
            later = cutoff + dt.timedelta(days=rng.randint(0, 400))
            assert _signal_row_available_at_or_before(row, later, _KEYS) is True
        else:
            earlier = cutoff - dt.timedelta(days=rng.randint(0, 400))
            assert _signal_row_available_at_or_before(row, earlier, _KEYS) is False


def test_signal_with_no_date_keys_is_never_available() -> None:
    rng = random.Random(3)
    for _ in range(200):
        # Rows carrying only non-date fields can never be proven pre-cutoff.
        row = {"sector": "energy", "alignment_score": 0.9}
        assert _signal_row_available_at_or_before(row, _rand_date(rng), _KEYS) is False


def _edge(attributes: dict[str, object]) -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-fuzz",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="X"),
        object=OntologyNodeRef(node_type="committee", node_id="HSEC", label="E"),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="m",
            )
        ],
        attributes=attributes,
    )


def test_edge_available_iff_known_and_all_dates_within_cutoff() -> None:
    rng = random.Random(2024)
    for _ in range(1500):
        cutoff = _rand_date(rng)
        attrs: dict[str, object] = {}
        dates: list[dt.date] = []
        for key in _EDGE_AVAILABILITY_DATE_KEYS:
            if rng.random() < 0.25:
                d = _rand_date(rng)
                attrs[key] = d.isoformat()
                dates.append(d)
        edge = _edge(attrs)
        result = _edge_available_at_or_before(edge, cutoff)
        if not dates:
            assert result is False
            assert _edge_has_known_availability(edge) is False
        else:
            assert _edge_has_known_availability(edge) is True
            # Available iff EVERY known date is at/before the cutoff.
            assert result == (max(dates) <= cutoff)


def test_edge_availability_is_monotonic_in_cutoff() -> None:
    rng = random.Random(5)
    for _ in range(800):
        edge = _edge({"transaction_date": _rand_date(rng).isoformat()})
        cutoff = _rand_date(rng)
        if _edge_available_at_or_before(edge, cutoff):
            later = cutoff + dt.timedelta(days=rng.randint(0, 400))
            assert _edge_available_at_or_before(edge, later) is True
