"""Exercise retained research workflows on tiny fixtures, never new benchmark pins."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path

import pytest

from src.runtime import (
    chain_composer_runner,
    confirmation_experiment,
    count_pmf_bayesian,
    count_pmf_experiment,
    defection_signals_experiment,
    paper_trader,
    registry_runner,
    stage_hazard_bayesian,
    stage_hazard_experiment,
)
from tests.runtime.test_bill_journey import _sidecar_row


def _jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _rolls() -> list[dict]:
    return [
        {
            "bill_id": f"us_congress:{congress}:h-r-{i + 1}",
            "date": day,
            "congress": congress,
            "question": "On the Nomination",
            "sectors": ["health"],
            "votes": [
                [f"M{j}", "D" if j < 30 else "R", "MI", "yea" if (j + i) % 4 else "nay"]
                for j in range(60)
            ],
        }
        for i, (day, congress) in enumerate(
            [
                ("2022-06-01", 117),
                ("2023-06-01", 118),
                ("2024-02-01", 118),
                ("2024-03-01", 118),
                ("2024-07-01", 118),
                ("2024-09-01", 118),
            ]
        )
    ]


def _check_checkpoint(report: dict) -> None:
    payload = json.loads(Path(report["checkpoint_path"]).read_text())
    assert (
        hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        == report["checkpoint_sha256"]
    )


def test_rollcall_windows_bootstrap_and_confirmation_use_real_algorithms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = _rolls()
    corpus = _jsonl(tmp_path / "rolls.jsonl", rows)
    window = dict(
        rates_cutoff=date(2023, 12, 31), fit_cutoff=date(2024, 4, 20), eval_end=date(2024, 12, 31)
    )
    report = count_pmf_experiment.run(corpus, **window)
    assert report["fit_rollcalls"] == report["eval_rollcalls"] == 2
    assert math.isfinite(report["crps_correlated"])
    assert 0 <= report["coverage90_correlated"] <= 1
    bootstrap = count_pmf_bayesian.run(corpus, **window, n_seeds=2)
    assert bootstrap["eval_rollcalls"] == 2
    _check_checkpoint(bootstrap)
    confirmed = confirmation_experiment.run(corpus)
    assert confirmed["train_votes"] == 60 and confirmed["eval_votes"] == 300
    assert 0 <= confirmed["brier"] <= 1
    # Changing only future outcomes must not refit history-derived shock scales.
    for row in rows[-2:]:
        for vote in row["votes"]:
            vote[3] = "nay"
    changed = count_pmf_experiment.run(_jsonl(corpus, rows), **window)
    assert (changed["sigma_common"], changed["sigma_party"]) == (
        report["sigma_common"],
        report["sigma_party"],
    )
    assert changed["crps_correlated"] != report["crps_correlated"]
    empty = _jsonl(tmp_path / "empty.jsonl", [])
    assert count_pmf_experiment.run(empty, **window)["error"] == "empty window"
    assert confirmation_experiment.run(empty)["error"] == "empty slice"


def test_journey_workflows_keep_temporal_slices_and_content_hashed_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        _sidecar_row(c, n, policy_area="Health" if n % 2 else "Energy")
        for c in (117, 118, 119)
        for n in range(1, 9)
    ]
    sidecar = _jsonl(tmp_path / "sidecar.jsonl", rows)
    house_rows = [
        {
            "bill_id": f"us_congress:{c}:h-r-{n}",
            "date": f"{1789 + 2 * (c - 1)}-06-{n:02d}",
            "congress": c,
            "votes": _rolls()[0]["votes"],
        }
        for c in (117, 118, 119)
        for n in (1, 3, 5, 7)
    ]
    house = _jsonl(tmp_path / "house.jsonl", house_rows)
    senate = _jsonl(
        tmp_path / "senate.jsonl",
        [{**row, "date": row["date"].replace("-06-", "-07-")} for row in house_rows],
    )
    for module in (stage_hazard_experiment, stage_hazard_bayesian, chain_composer_runner):
        monkeypatch.setattr(module, "_HOUSE_CORPORA", [str(house)])
        monkeypatch.setattr(module, "_SENATE_CORPORA", [str(senate)])
    report = stage_hazard_experiment.run(sidecar)
    assert report["train_bills"] == report["eval_bills"] == 8
    assert report["train_event_rate"] == report["eval_event_rate"] == 0.5
    bootstrap = stage_hazard_bayesian.run(sidecar, n_seeds=2)
    assert bootstrap["eval_bills"] == 8
    _check_checkpoint(bootstrap)
    # The composer expects its historical fixed input layout; keep it inside tmp.
    _jsonl(tmp_path / "data/real/house_119_rich.jsonl", house_rows[-4:])
    _jsonl(tmp_path / "data/real/senate_119_rich.jsonl", house_rows[-4:])
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks/count_pmf_baseline.json").write_text(
        '{"sigma_common": 0.0, "sigma_party": 0.0}'
    )
    composed = chain_composer_runner.run(
        sidecar, as_of=date(2025, 8, 1), deadline=date(2025, 12, 31), sample=2
    )
    assert len(composed["bills"]) == 2
    assert composed["cross_chamber_rate"] == 1
    for bill in composed["bills"]:
        assert 0 <= bill["p_law_by_deadline"] <= 1
        assert len(bill["links"]) == 6
    empty = _jsonl(tmp_path / "empty.jsonl", [])
    assert stage_hazard_experiment.run(empty)["error"] == "empty train or eval slice"

    records = _jsonl(
        tmp_path / "records.jsonl",
        [
            {
                "entity_type": "bill",
                "canonical_id": row["canonical_id"],
                "external_ids": [f"congress:{row['congress']}-hr-{row['number']}"],
            }
            for row in rows
        ],
    )
    markets = tmp_path / "markets"
    market_rows = [
        {
            "market_id": "count",
            "question": "Will 1-30 Democratic senators vote in favor?",
            "bill_ids": ["cb-119-1"],
        },
        {
            "market_id": "law",
            "question": "Will the bill become law?",
            "bill_ids": ["cb-119-2"],
            "end_date": "2025-12-31",
        },
        {"market_id": "passed", "question": "Will the bill become law?", "bill_ids": ["cb-119-1"]},
        {"market_id": "unknown", "question": "Unpriced question", "bill_ids": []},
        {
            "market_id": "closed",
            "question": "Will the bill become law?",
            "bill_ids": ["cb-119-2"],
            "closed": True,
        },
    ]
    _jsonl(markets / "markets.jsonl", market_rows)
    _jsonl(
        markets / "prices.jsonl",
        [
            {"market_id": row["market_id"], "price": 0.5, "known_at": "2025-07-01"}
            for row in market_rows
        ],
    )
    frozen = tmp_path / "frozen-market.json"
    registry = paper_trader.freeze(
        markets, records, sidecar, senate, as_of=date(2025, 8, 1), out_path=frozen
    )
    assert registry["n_markets"] == 2
    assert {row["method"] for row in registry["markets"]} == {"count_pmf", "chain_composer"}
    assert registry["content_sha256"] == paper_trader._content_hash(registry["markets"])
    original = frozen.read_bytes()
    _jsonl(markets / "markets.jsonl", [{**row, "closed": True} for row in market_rows])
    _jsonl(
        markets / "prices.jsonl",
        [
            {"market_id": row["market_id"], "price": 0.99, "known_at": "2025-12-31"}
            for row in market_rows
        ],
    )
    scored = paper_trader.score(frozen, markets)
    assert scored["n_resolved"] == 2
    assert scored["frozen_sha256"] == registry["content_sha256"]
    assert frozen.read_bytes() == original


def test_registry_roundtrip_and_absent_optional_signals_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    corpus = _jsonl(tmp_path / "rolls.jsonl", _rolls())
    frozen, scored = tmp_path / "frozen.json", tmp_path / "scored.json"
    assert (
        registry_runner.main(
            [
                "freeze",
                "--corpus",
                str(corpus),
                "--cutoff",
                "2024-04-20",
                "--target-end",
                "2024-12-31",
                "--max",
                "10",
                "--out",
                str(frozen),
            ]
        )
        == 0
    )
    before = frozen.read_bytes()
    assert (
        registry_runner.main(
            ["score", "--corpus", str(corpus), "--registry", str(frozen), "--out", str(scored)]
        )
        == 0
    )
    assert frozen.read_bytes() == before
    result = json.loads(scored.read_text())
    assert result["content_sha256"] == json.loads(before)["content_sha256"]
    assert result["metrics"]["resolved"] == 10
    signals = defection_signals_experiment.run(corpus, cutoff=date(2024, 4, 20), max_train=120)
    assert len(signals["signals"]) == 4
    assert signals["gates"]["donor_profile_present"] is False
    assert signals["gates"]["statement_corpus_members"] == 0
