"""Tests for the per-member bootstrap seed-ensemble and uncertainty report."""

from __future__ import annotations

from datetime import date

from src.prediction.per_member_ensemble import (
    ensemble_uncertainty_report,
    predict_interval,
    train_per_member_seed_ensemble,
)
from src.prediction.per_member_model import MemberVoteExample
from src.prediction.real_data_eval import VoteRow


def _examples() -> list[MemberVoteExample]:
    rows: list[MemberVoteExample] = []
    for index in range(120):
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append(
            MemberVoteExample(
                member_id=f"M{index % 10}",
                signals={"party_alignment": aligned},
                is_yea=aligned > 0,
            )
        )
    return rows


def test_ensemble_has_one_model_per_seed() -> None:
    models = train_per_member_seed_ensemble(_examples(), seeds=[1, 2, 3, 4, 5])
    assert len(models) == 5


def test_predict_interval_brackets_mean() -> None:
    models = train_per_member_seed_ensemble(_examples(), seeds=[1, 2, 3])
    mean, lower, upper = predict_interval(
        models, "M0", {"party_alignment": 1.0}, confidence_level=0.9
    )
    assert 0.0 <= lower <= mean <= upper <= 1.0


def _eval_rows() -> list[VoteRow]:
    rows: list[VoteRow] = []
    for index in range(60):
        aligned = 1.0 if index % 3 != 0 else -1.0
        is_yea = aligned > 0
        rows.append(
            VoteRow(
                member_bioguide_id=f"M{index % 10}",
                canonical_bill_id="bill",
                vote_option="yea" if is_yea else "nay",
                vote_date=date(2024, 6, 1),
                party="D" if index % 2 else "R",
                state="CA",
                jurisdiction_id="us_congress",
                signals={"party_alignment": aligned},
                # cross-pressured := member votes against party_alignment direction
                is_cross_pressured=index % 7 == 0,
            )
        )
    return rows


def test_uncertainty_report_covers_slices() -> None:
    models = train_per_member_seed_ensemble(_examples(), seeds=[1, 2, 3, 4, 5])
    report = ensemble_uncertainty_report(models, _eval_rows(), confidence_level=0.9)
    assert "all" in report
    for slice_metrics in report.values():
        assert slice_metrics.mean_interval_width >= 0.0
        assert 0.0 <= slice_metrics.mean_brier <= 1.0
        assert slice_metrics.sample_count > 0
