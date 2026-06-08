"""Multi-row property/fuzz coverage for the no-leakage validator.

The existing cutoff-safety tests pin single-row boundary cases. This extends
them with the stronger whole-validator invariant over arbitrary-size random
row sets: ``_validate_no_leakage`` raises *if and only if* at least one feature
row carries a vote after the cutoff or at least one label row falls outside the
evaluation window -- and never raises on a clean set. Seeded stdlib randomness
(hypothesis is unavailable offline), matching the rest of this suite.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from src.prediction.backtest import _validate_no_leakage


def _rand_date(rng: random.Random) -> dt.date:
    return dt.date(rng.randint(2005, 2028), rng.randint(1, 12), rng.randint(1, 28))


def _feature_rows(rng: random.Random, cutoff: dt.date) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for _ in range(rng.randint(0, 5)):
        choice = rng.random()
        if choice < 0.2:
            rows.append({})  # no latest_vote_date key at all -> never a violation
        elif choice < 0.4:
            rows.append({"latest_vote_date": None})  # missing date -> never a violation
        elif choice < 0.7:
            rows.append({"latest_vote_date": cutoff - dt.timedelta(days=rng.randint(0, 200))})
        else:
            rows.append({"latest_vote_date": cutoff + dt.timedelta(days=rng.randint(1, 200))})
    return rows


def _label_rows(
    rng: random.Random, label_start: dt.date, label_end: dt.date
) -> list[dict[str, object]]:
    span = (label_end - label_start).days
    rows: list[dict[str, object]] = []
    for _ in range(rng.randint(0, 5)):
        choice = rng.random()
        if choice < 0.6:
            rows.append({"vote_date": label_start + dt.timedelta(days=rng.randint(0, span))})
        elif choice < 0.8:
            rows.append({"vote_date": label_start - dt.timedelta(days=rng.randint(1, 40))})
        else:
            rows.append({"vote_date": label_end + dt.timedelta(days=rng.randint(1, 40))})
    return rows


def test_validate_no_leakage_raises_iff_some_row_violates() -> None:
    rng = random.Random(20260608)
    for _ in range(4000):
        cutoff = _rand_date(rng)
        label_start = cutoff + dt.timedelta(days=rng.randint(1, 30))
        label_end = label_start + dt.timedelta(days=rng.randint(0, 60))
        feature_rows = _feature_rows(rng, cutoff)
        label_rows = _label_rows(rng, label_start, label_end)

        feature_violation = any(
            isinstance(row.get("latest_vote_date"), dt.date) and row["latest_vote_date"] > cutoff  # type: ignore[operator]
            for row in feature_rows
        )
        label_violation = any(
            row["vote_date"] < label_start or row["vote_date"] > label_end  # type: ignore[operator]
            for row in label_rows
        )
        should_raise = feature_violation or label_violation

        if should_raise:
            with pytest.raises(ValueError):
                _validate_no_leakage(cutoff, label_start, label_end, feature_rows, label_rows)
        else:
            # A clean set must validate silently.
            _validate_no_leakage(cutoff, label_start, label_end, feature_rows, label_rows)


def test_validate_no_leakage_one_bad_row_poisons_an_otherwise_clean_feature_set() -> None:
    cutoff = dt.date(2024, 6, 1)
    label_start = dt.date(2024, 7, 1)
    label_end = dt.date(2024, 8, 1)
    clean: list[dict[str, object]] = [
        {"latest_vote_date": cutoff - dt.timedelta(days=index)} for index in range(20)
    ]
    # Clean set passes; inserting a single post-cutoff vote at any position must fail.
    _validate_no_leakage(cutoff, label_start, label_end, clean, [])
    for position in (0, len(clean) // 2, len(clean)):
        poisoned = list(clean)
        poisoned.insert(position, {"latest_vote_date": cutoff + dt.timedelta(days=1)})
        with pytest.raises(ValueError):
            _validate_no_leakage(cutoff, label_start, label_end, poisoned, [])
