"""Tests for the defection-AUC no-regression gate + its pinned real baseline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.prediction.defection_gate import (
    DefectionBaseline,
    DefectionSliceMetrics,
    evaluate_defection_gate,
)
from src.prediction.defection_head import defection_ranking_over_window
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls

_BASELINE_PATH = Path("benchmarks/defection_auc_baseline.json")
_RICH_CORPUS = Path("data/real/house_118_rich.jsonl")
_TOLERANCE = 0.005


_CUTOFF = date(2024, 4, 20)
_PINNED_AUC = 0.7247


def _baseline() -> DefectionBaseline:
    return DefectionBaseline(
        model_name="defection_head_logreg",
        cutoff="2024-04-20",
        slices=[
            DefectionSliceMetrics(slice_name="congress-118", auc=_PINNED_AUC, sample_count=100)
        ],
    )


def test_gate_passes_when_auc_holds_or_improves() -> None:
    current = [DefectionSliceMetrics(slice_name="congress-118", auc=0.75, sample_count=100)]
    result = evaluate_defection_gate(_baseline(), current, tolerance=_TOLERANCE)
    assert result.passed
    assert result.improvements and result.improvements[0].slice_name == "congress-118"


def test_gate_tolerates_tiny_drop_within_tolerance() -> None:
    current = [DefectionSliceMetrics(slice_name="congress-118", auc=_PINNED_AUC - 0.004)]
    assert evaluate_defection_gate(_baseline(), current, tolerance=_TOLERANCE).passed


def test_gate_fails_on_auc_regression_beyond_tolerance() -> None:
    current = [DefectionSliceMetrics(slice_name="congress-118", auc=0.60)]
    result = evaluate_defection_gate(_baseline(), current, tolerance=_TOLERANCE)
    assert not result.passed
    assert result.regressions[0].slice_name == "congress-118"
    assert not result.regressions[0].approved


def test_gate_fails_on_missing_slice() -> None:
    result = evaluate_defection_gate(_baseline(), [], tolerance=_TOLERANCE)
    assert not result.passed
    assert result.missing_slices == ["congress-118"]


def test_gate_allows_approved_regression() -> None:
    current = [DefectionSliceMetrics(slice_name="congress-118", auc=0.50)]
    result = evaluate_defection_gate(
        _baseline(), current, tolerance=_TOLERANCE, approved_slice_names=frozenset({"congress-118"})
    )
    assert result.passed
    assert result.regressions[0].approved


def test_gate_reports_new_slices_without_blocking() -> None:
    current = [
        DefectionSliceMetrics(slice_name="congress-118", auc=_PINNED_AUC),
        DefectionSliceMetrics(slice_name="congress-119", auc=0.66),
    ]
    result = evaluate_defection_gate(_baseline(), current, tolerance=_TOLERANCE)
    assert result.passed
    assert result.new_slices == ["congress-119"]


def test_baseline_rejects_duplicate_slice_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        DefectionBaseline(
            model_name="m",
            slices=[
                DefectionSliceMetrics(slice_name="s", auc=0.5),
                DefectionSliceMetrics(slice_name="s", auc=0.6),
            ],
        )


def test_baseline_roundtrips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    _baseline().dump(path)
    loaded = DefectionBaseline.load(path)
    assert loaded.slices[0].auc == _PINNED_AUC
    assert loaded.cutoff == "2024-04-20"


def test_pinned_baseline_file_is_valid_and_has_congress_118() -> None:
    baseline = DefectionBaseline.load(_BASELINE_PATH)
    names = {s.slice_name for s in baseline.slices}
    assert "congress-118" in names
    assert all(0.0 <= s.auc <= 1.0 for s in baseline.slices)


@pytest.mark.skipif(not _RICH_CORPUS.exists(), reason="real rich corpus not present (gitignored)")
def test_real_corpus_meets_pinned_floor() -> None:
    # The gate is real: recompute AUC on Track A's corpus and require it clears
    # the pinned floor (baseline - tolerance). Skipped when the 16M corpus is absent.
    records = build_vote_records(load_rich_rollcalls(_RICH_CORPUS))
    metrics = defection_ranking_over_window(records, cutoff=_CUTOFF, eval_end=date(2024, 12, 31))
    pinned = next(
        s for s in DefectionBaseline.load(_BASELINE_PATH).slices if s.slice_name == "congress-118"
    )
    assert metrics.auc >= pinned.auc - _TOLERANCE


def test_window_runner_is_deterministic_and_separable() -> None:
    # A separable synthetic window: the defector outranks loyalists -> AUC == 1.0.
    records = []
    for day in range(1, 25):
        records.append(_rec("defector", is_yea=True, lean_yea=False, day=day))
        records.append(_rec("loyal", is_yea=False, lean_yea=False, day=day))
    # one eval defection by the historically disloyal member
    records.append(_rec("defector", is_yea=True, lean_yea=False, day=2, year=2024))
    records.append(_rec("loyal", is_yea=False, lean_yea=False, day=2, year=2024))
    metrics = defection_ranking_over_window(
        records, cutoff=date(2023, 12, 31), eval_end=date(2024, 12, 31)
    )
    assert metrics.sample_count == 2
    assert metrics.auc == 1.0


def test_window_runner_handles_empty_eval_window() -> None:
    records = [_rec("a", is_yea=True, lean_yea=True, day=1)]
    metrics = defection_ranking_over_window(
        records, cutoff=date(2023, 12, 31), eval_end=date(2024, 12, 31)
    )
    assert metrics.sample_count == 0
    assert metrics.auc == 0.5


def _rec(member, *, is_yea, lean_yea, day, year=2023):
    from src.runtime.cross_pressured_experiment import VoteRecord

    return VoteRecord(
        member=member,
        party="R",
        state="TX",
        vote_date=date(year, 1, day),
        is_yea=is_yea,
        party_alignment=1.0 if lean_yea else -1.0,
        sectors=("energy",),
        is_cross_pressured=is_yea != lean_yea,
    )
