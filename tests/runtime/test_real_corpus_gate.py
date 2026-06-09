"""Tests for the --from-real-corpus benchmark gate mode (deterministic, offline)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from src.runtime.prediction_benchmark_gate import main


def _write_corpus(path: Path) -> None:
    rows = []
    # Training votes before the 118th-h2 cutoff (2024-03-31).
    for index in range(80):
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append((f"M{index % 12}", date(2023, 6, 1), aligned))
    # Evaluation votes inside the window (2024-04-01 .. 2025-01-02).
    for index in range(40):
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append((f"M{index % 12}", date(2024, 6, 1) + timedelta(days=index % 30), aligned))
    with path.open("w", encoding="utf-8") as fh:
        for member, vote_date, aligned in rows:
            fh.write(
                json.dumps(
                    {
                        "member_bioguide_id": member,
                        "canonical_bill_id": "bill",
                        "vote_option": "yea" if aligned > 0 else "nay",
                        "vote_date": vote_date.isoformat(),
                        "party": "D",
                        "state": "CA",
                        "jurisdiction_id": "us_congress",
                        "signals": {"party_alignment": aligned},
                        "is_cross_pressured": False,
                    }
                )
                + "\n"
            )


def test_real_corpus_gate_passes_against_freshly_pinned_baseline(tmp_path: Path) -> None:
    corpus = tmp_path / "votes.jsonl"
    baseline = tmp_path / "baseline.json"
    _write_corpus(corpus)
    assert (
        main(
            [
                "--from-real-corpus",
                str(corpus),
                "--window",
                "118th-h2",
                "--baseline",
                str(baseline),
                "--update-baseline",
            ]
        )
        == 0
    )
    # Re-running the deterministic corpus must pass at zero tolerance.
    assert (
        main(
            [
                "--from-real-corpus",
                str(corpus),
                "--window",
                "118th-h2",
                "--baseline",
                str(baseline),
                "--tolerance",
                "0.0",
            ]
        )
        == 0
    )


def test_real_corpus_gate_flags_regression(tmp_path: Path) -> None:
    corpus = tmp_path / "votes.jsonl"
    baseline = tmp_path / "baseline.json"
    _write_corpus(corpus)
    # Pin an unrealistically strong baseline; the real run must flag the regression.
    baseline.write_text(
        json.dumps(
            {
                "model_name": "per_member_signal_model",
                "slices": [{"slice_name": "all", "brier_score": 0.0, "log_loss": 0.0}],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--from-real-corpus",
                str(corpus),
                "--window",
                "118th-h2",
                "--baseline",
                str(baseline),
                "--tolerance",
                "0.005",
            ]
        )
        == 1
    )


def test_real_corpus_gate_requires_exactly_one_source(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"model_name": "m", "slices": []}', encoding="utf-8")
    # Both --from-synthetic and --from-real-corpus -> usage error.
    assert (
        main(
            [
                "--from-synthetic",
                "1",
                "--from-real-corpus",
                str(tmp_path / "c.jsonl"),
                "--baseline",
                str(baseline),
            ]
        )
        == 2
    )
