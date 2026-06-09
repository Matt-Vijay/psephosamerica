"""Tests for the real-data benchmark runner (offline, fixture corpus)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.real_benchmark import (
    baseline_from_window,
    build_report,
    default_congress_windows,
    main,
)
from src.prediction.real_data_eval import EvalWindow, VoteRow, evaluate_windows


def _rows() -> list[VoteRow]:
    rows: list[VoteRow] = []
    for index in range(60):
        member = f"M{index % 10}"
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append(
            VoteRow(
                member_bioguide_id=member,
                canonical_bill_id="bill",
                vote_option="yea" if aligned > 0 else "nay",
                vote_date=date(2024, 6, 1),
                party="D" if index % 2 else "R",
                state="CA" if index % 2 else "TX",
                jurisdiction_id="us_congress",
                signals={"party_alignment": aligned},
                is_cross_pressured=index % 5 == 0,
            )
        )
    # pre-cutoff training votes (same rule, earlier dates)
    for index in range(60):
        member = f"M{index % 10}"
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append(
            VoteRow(
                member_bioguide_id=member,
                canonical_bill_id="bill",
                vote_option="yea" if aligned > 0 else "nay",
                vote_date=date(2023, 6, 1),
                party="D" if index % 2 else "R",
                state="CA" if index % 2 else "TX",
                jurisdiction_id="us_congress",
                signals={"party_alignment": aligned},
            )
        )
    return rows


def test_default_windows_span_recent_congresses() -> None:
    names = [w.name for w in default_congress_windows()]
    assert names[:3] == ["115th", "116th", "117th"]
    assert "118th-h2" in names


def test_build_report_serializes_windows_and_slices() -> None:
    window = EvalWindow("118th-h2", date(2024, 3, 31), date(2024, 4, 1), date(2025, 1, 2))
    result = evaluate_windows(_rows(), windows=[window])
    report = build_report(result)
    assert report["window_count"] == 1
    slices = report["windows"]["118th-h2"]  # type: ignore[index]
    assert "all" in slices
    assert set(slices["all"]) == {
        "slice_name",
        "brier_score",
        "log_loss",
        "accuracy",
        "ece",
        "sample_count",
    }


def test_baseline_from_window_pins_slices() -> None:
    window = EvalWindow("118th-h2", date(2024, 3, 31), date(2024, 4, 1), date(2025, 1, 2))
    result = evaluate_windows(_rows(), windows=[window])
    baseline = baseline_from_window(result, window="118th-h2")
    assert baseline.model_name == "per_member_signal_model"
    assert any(s.slice_name == "all" for s in baseline.slices)


def test_main_writes_report_and_baseline(tmp_path: Path) -> None:
    corpus = tmp_path / "votes.jsonl"
    with corpus.open("w", encoding="utf-8") as fh:
        for row in _rows():
            fh.write(
                json.dumps(
                    {
                        "member_bioguide_id": row.member_bioguide_id,
                        "canonical_bill_id": row.canonical_bill_id,
                        "vote_option": row.vote_option,
                        "vote_date": row.vote_date.isoformat(),
                        "party": row.party,
                        "state": row.state,
                        "jurisdiction_id": row.jurisdiction_id,
                        "signals": row.signals,
                        "is_cross_pressured": row.is_cross_pressured,
                    }
                )
                + "\n"
            )
    report = tmp_path / "report.json"
    baseline = tmp_path / "baseline.json"
    code = main(
        [
            "--corpus",
            str(corpus),
            "--report",
            str(report),
            "--pin-baseline",
            str(baseline),
        ]
    )
    assert code == 0
    assert report.exists() and baseline.exists()
    assert json.loads(report.read_text())["window_count"] >= 1


def test_main_missing_corpus_is_error(tmp_path: Path) -> None:
    code = main(["--corpus", str(tmp_path / "nope.jsonl"), "--report", str(tmp_path / "r.json")])
    assert code == 2
