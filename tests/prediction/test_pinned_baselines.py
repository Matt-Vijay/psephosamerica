"""Guard the committed defection baselines: well-formed + self-consistent gates.

The bill-content / defection-AUC gates can't recompute AUC in CI (they need the
gitignored contract + rich corpora), but the *pins* are committed JSON. This test
runs in CI with no data: it loads each pinned baseline, asserts it parses, has
unique slices with in-range AUCs, and that the gate passes against its own pinned
values (a malformed or contradictory pin fails CI here). It also asserts the known
SOTA pins are present and ordered (combined > bill-content > base), so a silent
regression of the pinned numbers is caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.prediction.defection_gate import (
    DefectionBaseline,
    DefectionSliceMetrics,
    evaluate_defection_gate,
)

_BENCH = Path(__file__).resolve().parents[2] / "benchmarks"


@pytest.mark.parametrize("filename", ["defection_auc_baseline.json", "bill_content_baseline.json"])
def test_pinned_baseline_is_wellformed_and_self_consistent(filename: str) -> None:
    baseline = DefectionBaseline.load(_BENCH / filename)
    assert baseline.slices, f"{filename} has no slices"
    names = [s.slice_name for s in baseline.slices]
    assert len(names) == len(set(names)), "duplicate slice names"
    for s in baseline.slices:
        assert 0.0 <= s.auc <= 1.0
    # the gate must pass against the baseline's own pinned values
    current = [
        DefectionSliceMetrics(
            slice_name=s.slice_name, auc=s.auc, sample_count=s.sample_count, positives=s.positives
        )
        for s in baseline.slices
    ]
    assert evaluate_defection_gate(baseline, current, tolerance=0.005).passed


def test_bill_content_sota_ordering_pinned() -> None:
    # combined (bill-RAG + CRS) > bill-content (bill-RAG) > base, on the 118th.
    baseline = DefectionBaseline.load(_BENCH / "bill_content_baseline.json")
    auc = {s.slice_name: s.auc for s in baseline.slices}
    assert (
        auc["congress-118-combined"] > auc["congress-118-bill-content"] > auc["congress-118-base"]
    )
    # the headline SOTA is pinned at ~0.80
    assert auc["congress-118-combined"] >= 0.79


def test_vote_only_pin_present() -> None:
    auc = {
        s.slice_name: s.auc
        for s in DefectionBaseline.load(_BENCH / "defection_auc_baseline.json").slices
    }
    assert auc.get("congress-118", 0.0) >= 0.72  # the vote-only defection pin


def test_stage_hazard_pin_wellformed() -> None:
    payload = json.loads((_BENCH / "stage_hazard_baseline.json").read_text(encoding="utf-8"))
    assert payload["slice"] == "floor-vote-hazard-118"
    # the covariate model must beat the intercept-only baseline by a real margin
    assert payload["c_index"] >= payload["c_index_intercept_only"] + 0.1
    assert 0.5 <= payload["c_index"] <= 1.0
    assert 0.0 <= payload["ece_365"] <= 0.05
    assert payload["eval_bills"] > 10_000


def test_count_pmf_pin_wellformed() -> None:
    payload = json.loads((_BENCH / "count_pmf_baseline.json").read_text(encoding="utf-8"))
    assert payload["slice"] == "count-pmf-118"
    # the correlated PMF must beat independence on CRPS and fix the tail coverage
    assert payload["crps_correlated"] < payload["crps_independence"]
    assert 0.8 <= payload["coverage90_correlated"] <= 1.0
    assert payload["sigma_party"] > 0.0
