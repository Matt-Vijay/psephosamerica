"""Tests for the bill-journey loader (sidecar -> floor-event join, censoring)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.bill_journey import (
    JourneyFeaturizer,
    congress_window,
    load_bill_journeys,
    load_floor_events,
)


def _sidecar_row(congress: int, number: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "canonical_id": f"cb-{congress}-{number}",
        "congress": congress,
        "bill_type": "hr",
        "number": number,
        "introduced_date": f"{1789 + 2 * (congress - 1)}-02-01",
        "policy_area": "Health",
        "subjects": [],
        "sponsors": [{"full_name": "Rep. Doe, Jane [R-TX-12]"}],
        "cosponsors": [],
        "committees": [{"name": "Judiciary", "system_code": "hsju00", "chamber": "House"}],
    }
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_congress_window() -> None:
    assert congress_window(118) == (date(2023, 1, 3), date(2025, 1, 3))


def test_event_join_and_censoring(tmp_path: Path) -> None:
    sidecar = _write_jsonl(
        tmp_path / "sidecar.jsonl",
        [_sidecar_row(118, 1), _sidecar_row(118, 2)],
    )
    house = _write_jsonl(
        tmp_path / "house.jsonl",
        [
            {
                "bill_id": "us_congress:118:h-r-1",
                "date": "2023-06-01",
                "congress": 118,
                "votes": [],
            },
            {
                "bill_id": "us_congress:118:h-r-99",
                "date": "2024-11-01",
                "congress": 118,
                "votes": [],
            },
        ],
    )
    journeys = load_bill_journeys(sidecar, load_floor_events([house]), ({}, {}))
    by_key = {j.bill_key: j for j in journeys}
    voted = by_key["118:hr:1"]
    assert voted.event_observed and voted.duration_days == float(
        (date(2023, 6, 1) - date(2023, 2, 1)).days
    )
    censored = by_key["118:hr:2"]
    # censored at coverage end (2024-11-01 + 1d), which precedes the congress end
    assert not censored.event_observed
    assert censored.duration_days == float((date(2024, 11, 2) - date(2023, 2, 1)).days)


def test_chamber_without_coverage_is_skipped(tmp_path: Path) -> None:
    sidecar = _write_jsonl(tmp_path / "sidecar.jsonl", [_sidecar_row(114, 1)])
    journeys = load_bill_journeys(sidecar, ({}, {}), ({}, {}))
    assert journeys == []  # House 114 floor data not ingested -> no row, no false label


def test_only_early_cosponsors_count(tmp_path: Path) -> None:
    row = _sidecar_row(
        118,
        3,
        cosponsors=[
            {"full_name": "Rep. A [D-CA-1]", "sponsorship_date": "2023-02-10"},
            {"full_name": "Rep. B [D-NY-2]", "sponsorship_date": "2024-06-01"},
        ],
    )
    sidecar = _write_jsonl(tmp_path / "sidecar.jsonl", [row])
    house = _write_jsonl(
        tmp_path / "house.jsonl",
        [{"bill_id": "us_congress:118:h-r-3", "date": "2023-06-01", "congress": 118, "votes": []}],
    )
    (journey,) = load_bill_journeys(sidecar, load_floor_events([house]), ({}, {}))
    assert journey.cosponsor_count == 1  # the 2024 joiner leaks the bill's success
    assert journey.bipartisan_share == 1.0
    assert journey.sponsor_party == "R"
    assert journey.sponsor_in_majority  # R majority in the 118th House


def test_featurizer_one_hots_train_policy_areas(tmp_path: Path) -> None:
    sidecar = _write_jsonl(
        tmp_path / "sidecar.jsonl",
        [_sidecar_row(118, 1), _sidecar_row(118, 2, policy_area="Energy")],
    )
    house = _write_jsonl(
        tmp_path / "house.jsonl",
        [{"bill_id": "us_congress:118:h-r-1", "date": "2023-06-01", "congress": 118, "votes": []}],
    )
    journeys = load_bill_journeys(sidecar, load_floor_events([house]), ({}, {}))
    featurizer = JourneyFeaturizer.fit(journeys)
    x = featurizer.transform(journeys)
    assert x.shape == (2, len(featurizer.feature_names))
    health_col = featurizer.feature_names.index("policy:Health")
    assert x[0, health_col] == 1.0 and x[1, health_col] == 0.0
