"""Tests for per-slice benchmark metric computation.

OVERALL_GOAL.md wants Brier + log-loss reported per jurisdiction / party /
faction and gated for regression. The eval report already produces scored
prediction payloads (each carrying party, jurisdiction, Brier and log-loss);
this computes the per-slice rollups the dashboards and the no-regression gate
consume, then composes them with the gate.
"""

from __future__ import annotations

import math
from datetime import date

from src.prediction.backtest import PredictionBacktestPredictionPayload, _event_key
from src.prediction.benchmark_gate import BenchmarkBaseline, BenchmarkSliceMetrics
from src.prediction.benchmark_slices import (
    compute_benchmark_slices,
    evaluate_predictions_against_baseline,
)


def _prediction(
    *,
    member_id: str,
    party: str | None,
    vote_option: str,
    probability_yea: float,
    jurisdiction: str = "us_congress",
    legislative_body_id: str | None = None,
    legislative_session_id: str | None = None,
    chamber: str = "house",
    roll_call_number: int,
    skipped: bool = False,
) -> PredictionBacktestPredictionPayload:
    body = legislative_body_id or f"{jurisdiction}_{chamber}"
    event_key = _event_key(
        jurisdiction_id=jurisdiction,
        legislative_body_id=body,
        legislative_session_id=legislative_session_id,
        chamber=chamber,
        congress=119,
        session_number=1,
        roll_call_number=roll_call_number,
    )
    binary = vote_option in {"yea", "nay"}
    target = 1.0 if vote_option == "yea" else 0.0
    common: dict[str, object] = {
        "vote_event_id": 1000 + roll_call_number,
        "event_key": event_key,
        "jurisdiction_id": jurisdiction,
        "legislative_body_id": legislative_body_id,
        "legislative_session_id": legislative_session_id,
        "chamber": chamber,
        "congress": 119,
        "session_number": 1,
        "roll_call_number": roll_call_number,
        "vote_date": date(2025, 2, 1),
        "question": "On Passage",
        "member_bioguide_id": member_id,
        "party": party,
        "actual_vote_option": vote_option,
        "feature_vote_count": 5,
    }
    if skipped:
        return PredictionBacktestPredictionPayload(**common, skipped_reason="no_prior_votes")  # type: ignore[arg-type]
    probability = max(1e-9, min(1 - 1e-9, probability_yea))
    return PredictionBacktestPredictionPayload(
        **common,  # type: ignore[arg-type]
        predicted_probability_yea=probability,
        predicted_vote_option="yea" if probability >= 0.5 else "nay",
        predicted_vote_probabilities={"yea": probability, "nay": 1.0 - probability},
        correct=(probability >= 0.5) == (vote_option == "yea") if binary else False,
        brier_score=(probability - target) ** 2 if binary else None,
        log_loss=-(target * math.log(probability) + (1 - target) * math.log(1 - probability))
        if binary
        else None,
    )


def _sample() -> list[PredictionBacktestPredictionPayload]:
    return [
        _prediction(
            member_id="D1", party="D", vote_option="yea", probability_yea=0.9, roll_call_number=1
        ),
        _prediction(
            member_id="D2", party="D", vote_option="nay", probability_yea=0.2, roll_call_number=2
        ),
        _prediction(
            member_id="R1", party="R", vote_option="nay", probability_yea=0.1, roll_call_number=3
        ),
        _prediction(
            member_id="R2",
            party="R",
            vote_option="yea",
            probability_yea=0.4,
            jurisdiction="us_state_ca",
            legislative_body_id="ca_assembly",
            legislative_session_id="2025",
            roll_call_number=4,
        ),
        # skipped prediction contributes to no slice
        _prediction(
            member_id="D3",
            party="D",
            vote_option="yea",
            probability_yea=0.5,
            roll_call_number=5,
            skipped=True,
        ),
    ]


def test_compute_benchmark_slices_rolls_up_overall_party_and_jurisdiction() -> None:
    slices = {item.slice_name: item for item in compute_benchmark_slices(_sample())}

    # Overall covers the 4 evaluated (non-skipped, binary) predictions.
    assert slices["overall"].sample_count == 4
    assert slices["party:D"].sample_count == 2
    assert slices["party:R"].sample_count == 2
    assert slices["jurisdiction:us_congress"].sample_count == 3
    assert slices["jurisdiction:us_state_ca"].sample_count == 1

    # The overall Brier is the mean of per-prediction Brier scores.
    expected_overall_brier = ((0.9 - 1) ** 2 + (0.2 - 0) ** 2 + (0.1 - 0) ** 2 + (0.4 - 1) ** 2) / 4
    assert math.isclose(slices["overall"].brier_score, expected_overall_brier)


def test_compute_benchmark_slices_can_disable_dimensions() -> None:
    slices = {
        item.slice_name: item
        for item in compute_benchmark_slices(_sample(), by_party=False, by_jurisdiction=False)
    }
    assert list(slices) == ["overall"]


def test_compute_benchmark_slices_skips_party_when_absent() -> None:
    predictions = [
        _prediction(
            member_id="X",
            party=None,
            vote_option="yea",
            probability_yea=0.8,
            roll_call_number=1,
        )
    ]
    slices = {item.slice_name: item for item in compute_benchmark_slices(predictions)}
    assert "overall" in slices
    assert not any(name.startswith("party:") for name in slices)


def test_evaluate_predictions_against_baseline_passes_when_within_pin() -> None:
    predictions = _sample()
    computed = {item.slice_name: item for item in compute_benchmark_slices(predictions)}
    # Pin the baseline to the computed metrics: an exact replay must pass.
    baseline = BenchmarkBaseline(
        model_name="per_member_signal_model",
        slices=[
            BenchmarkSliceMetrics(
                slice_name=item.slice_name,
                brier_score=item.brier_score,
                log_loss=item.log_loss,
                sample_count=item.sample_count,
            )
            for item in computed.values()
        ],
    )
    result = evaluate_predictions_against_baseline(predictions, baseline, tolerance=1e-9)
    assert result.passed
    assert result.regressions == []


def test_evaluate_predictions_against_baseline_flags_regression() -> None:
    baseline = BenchmarkBaseline(
        model_name="per_member_signal_model",
        slices=[BenchmarkSliceMetrics(slice_name="overall", brier_score=0.01, log_loss=0.05)],
    )
    result = evaluate_predictions_against_baseline(_sample(), baseline, tolerance=0.01)
    assert not result.passed
    assert any(regression.slice_name == "overall" for regression in result.regressions)
