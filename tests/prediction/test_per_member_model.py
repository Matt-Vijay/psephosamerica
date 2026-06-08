"""Tests for the hierarchical partial-pooling per-member vote model.

The model is the architectural unlock identified by OVERALL_GOAL.md's
controlled experiment: a per-member intercept + per-member signal slope,
shrunk toward a shared global model. On the cross-pressured slice (party
pull and member-specific sector pull diverge) a single global logistic
model is pinned near chance because members respond to the same bill
signal in opposite directions; learning a per-member slope recovers that
member-specific response.
"""

from __future__ import annotations

import math
import random
from datetime import date

from src.prediction.backtest import (
    PredictionBacktestMetricsPayload,
    PredictionBacktestPayload,
    PredictionBacktestPredictionPayload,
    _event_key,
)
from src.prediction.per_member_model import (
    PER_MEMBER_MODEL_NAME,
    MemberVoteExample,
    PerMemberModel,
    member_vote_examples_from_predictions,
    score_per_member_backtest,
    train_per_member_model,
)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _synthetic_house(
    seed: int,
    *,
    members: int = 80,
    train_bills: int = 120,
    eval_bills: int = 60,
) -> tuple[list[MemberVoteExample], list[MemberVoteExample], list[bool]]:
    """Generate a causally-clean synthetic chamber.

    Each member carries a *hidden* sector polarity that the model never
    observes directly; it only sees the bill's sector exposure and a
    party-alignment indicator. Members split evenly on polarity sign, so
    the globally optimal sector coefficient is ~0 and a global model must
    fall back to party. Returns (train, eval, eval_is_cross_pressured).
    """
    rng = random.Random(seed)
    roster = [
        (
            f"M{index:03d}",
            "D" if index < int(members * 0.6) else "R",
            rng.uniform(-1.0, 1.0),
        )
        for index in range(members)
    ]

    def make_bills(count: int) -> list[tuple[bool, float]]:
        return [(rng.random() < 0.5, rng.uniform(-1.0, 1.0)) for _ in range(count)]

    def votes(bills: list[tuple[bool, float]]) -> tuple[list[MemberVoteExample], list[bool]]:
        examples: list[MemberVoteExample] = []
        cross_pressured: list[bool] = []
        for d_favored, sector_exposure in bills:
            for member_id, party, polarity in roster:
                aligned = (party == "D") == d_favored
                party_pull = 2.0 if aligned else -2.0
                sector_pull = polarity * sector_exposure * 4.0
                probability = _sigmoid(party_pull + sector_pull)
                examples.append(
                    MemberVoteExample(
                        member_id=member_id,
                        signals={
                            "party_alignment": 1.0 if aligned else -1.0,
                            "sector_exposure": sector_exposure,
                        },
                        is_yea=rng.random() < probability,
                    )
                )
                cross_pressured.append(
                    (party_pull > 0) != (sector_pull > 0) and abs(sector_pull) >= 2.0
                )
        return examples, cross_pressured

    train_examples, _ = votes(make_bills(train_bills))
    eval_examples, eval_cross = votes(make_bills(eval_bills))
    return train_examples, eval_examples, eval_cross


def _accuracy_and_brier(
    model: PerMemberModel, examples: list[MemberVoteExample]
) -> tuple[float, float]:
    correct = 0
    brier_total = 0.0
    for example in examples:
        probability = model.predict_probability(example.member_id, example.signals)
        target = 1.0 if example.is_yea else 0.0
        if (probability >= 0.5) == example.is_yea:
            correct += 1
        brier_total += (probability - target) ** 2
    count = len(examples)
    return correct / count, brier_total / count


def test_per_member_model_beats_global_on_cross_pressured_slice() -> None:
    train, evaluation, cross = _synthetic_house(seed=20260608)
    cross_slice = [example for example, flag in zip(evaluation, cross, strict=True) if flag]
    assert len(cross_slice) >= 200  # the slice anyone cares about predicting must exist

    global_model = train_per_member_model(train, pooling_penalty=1e9)
    per_member = train_per_member_model(train, pooling_penalty=1.0)

    global_acc, global_brier = _accuracy_and_brier(global_model, cross_slice)
    member_acc, member_brier = _accuracy_and_brier(per_member, cross_slice)

    # Global model is pinned at/under chance on the cross-pressured slice: it
    # learns a ~0 sector coefficient (members split evenly) and falls back to
    # party, which is exactly the wrong call when sector pull dominates.
    assert global_acc < 0.55
    # The per-member slope recovers the hidden member-specific response.
    assert member_acc >= global_acc + 0.12
    assert member_brier < global_brier - 0.10


def test_large_pooling_penalty_collapses_to_global_model() -> None:
    train, _evaluation, _cross = _synthetic_house(seed=7)
    model = train_per_member_model(train, pooling_penalty=1e9)

    assert model.member_intercept_offsets  # offsets are tracked per seen member
    max_offset = max(abs(value) for value in model.member_intercept_offsets.values())
    max_slope = max(
        abs(value)
        for offsets in model.member_coefficient_offsets.values()
        for value in offsets.values()
    )
    assert max_offset < 1e-3
    assert max_slope < 1e-3


def test_unknown_member_falls_back_to_global_prediction() -> None:
    train, _evaluation, _cross = _synthetic_house(seed=11)
    model = train_per_member_model(train, pooling_penalty=1.0)

    signals = {"party_alignment": 1.0, "sector_exposure": 0.5}
    raw = model.global_intercept + sum(
        model.global_coefficients[name] * signals.get(name, 0.0)
        for name in model.global_coefficients
    )
    expected = 1.0 / (1.0 + math.exp(-raw))
    assert model.predict_probability("not-a-real-member", signals) == expected


def test_train_per_member_model_reports_counts_and_name() -> None:
    train, _evaluation, _cross = _synthetic_house(seed=3, members=10, train_bills=4)
    model = train_per_member_model(train, pooling_penalty=1.0)

    assert PER_MEMBER_MODEL_NAME == "per_member_signal_model"
    assert model.training_example_count == len(train)
    assert model.member_count == 10
    assert sorted(model.signal_names) == ["party_alignment", "sector_exposure"]


def _prediction(
    *,
    member_id: str,
    vote_option: str,
    signals: dict[str, float] | None = None,
    skipped_reason: str | None = None,
    roll_call_number: int = 7,
) -> PredictionBacktestPredictionPayload:
    event_key = _event_key(
        jurisdiction_id="us_congress",
        legislative_body_id="us_congress_house",
        legislative_session_id=None,
        chamber="house",
        congress=119,
        session_number=1,
        roll_call_number=roll_call_number,
    )
    scored = skipped_reason is None
    binary = scored and vote_option in {"yea", "nay"}
    distribution = {"yea": 0.7, "nay": 0.3} if scored else {}
    target_yea = vote_option == "yea"
    return PredictionBacktestPredictionPayload(
        vote_event_id=100 + roll_call_number,
        event_key=event_key,
        chamber="house",
        congress=119,
        session_number=1,
        roll_call_number=roll_call_number,
        vote_date=date(2025, 2, 1),
        question="On Passage",
        member_bioguide_id=member_id,
        actual_vote_option=vote_option,  # type: ignore[arg-type]
        feature_vote_count=5,
        predicted_probability_yea=0.7 if scored else None,
        predicted_vote_option="yea" if scored else None,
        predicted_vote_probabilities=distribution,  # type: ignore[arg-type]
        correct=target_yea if scored else None,
        brier_score=(0.7 - (1.0 if target_yea else 0.0)) ** 2 if binary else None,
        log_loss=-math.log(0.7 if target_yea else 0.3) if binary else None,
        skipped_reason=skipped_reason,
        feature_signals=signals or {},
    )


def test_member_vote_examples_from_predictions_keeps_only_binary_scored_examples() -> None:
    predictions = [
        _prediction(
            member_id="A000001",
            vote_option="yea",
            signals={"party_alignment": 1.0},
            roll_call_number=1,
        ),
        _prediction(
            member_id="A000002",
            vote_option="nay",
            signals={"party_alignment": -1.0},
            roll_call_number=2,
        ),
        # present votes are not binary labels -> excluded
        _prediction(
            member_id="A000003",
            vote_option="present",
            signals={"party_alignment": 1.0},
            roll_call_number=3,
        ),
        # skipped predictions carry no usable label -> excluded
        _prediction(
            member_id="A000004",
            vote_option="yea",
            skipped_reason="no_prior_votes",
            roll_call_number=4,
        ),
        # no feature signals -> excluded
        _prediction(member_id="A000005", vote_option="yea", signals={}, roll_call_number=5),
    ]

    examples = member_vote_examples_from_predictions(predictions)  # type: ignore[arg-type]

    assert [(item.member_id, item.is_yea) for item in examples] == [
        ("A000001", True),
        ("A000002", False),
    ]
    assert examples[0].signals == {"party_alignment": 1.0}


def _make_backtest(
    predictions: list[PredictionBacktestPredictionPayload],
) -> PredictionBacktestPayload:
    evaluated = [item for item in predictions if item.correct is not None]
    briers = [item.brier_score for item in evaluated if item.brier_score is not None]
    losses = [item.log_loss for item in evaluated if item.log_loss is not None]
    correct = sum(1 for item in evaluated if item.correct)
    metrics = PredictionBacktestMetricsPayload(
        label_count=len(predictions),
        evaluated_count=len(evaluated),
        correct_count=correct,
        skipped_count=sum(1 for item in predictions if item.skipped_reason is not None),
        accuracy=correct / len(evaluated) if evaluated else None,
        brier_score=sum(briers) / len(briers) if briers else None,
        log_loss=sum(losses) / len(losses) if losses else None,
    )
    return PredictionBacktestPayload(
        model_name="ontology_signal_model",
        feature_cutoff=date(2025, 1, 1),
        label_start=date(2025, 2, 1),
        label_end=date(2025, 2, 1),
        feature_vote_event_count=3,
        label_vote_event_count=len({item.vote_event_id for item in predictions}),
        member_count=len({item.member_bioguide_id for item in predictions}),
        metrics=metrics,
        predictions=predictions,
    )


def test_score_per_member_backtest_rescores_probabilities_and_relabels_model() -> None:
    predictions = [
        _prediction(
            member_id="A000001",
            vote_option="yea",
            signals={"party_alignment": 1.0},
            roll_call_number=1,
        ),
        _prediction(
            member_id="A000002",
            vote_option="nay",
            signals={"party_alignment": 1.0},
            roll_call_number=2,
        ),
    ]
    backtest = _make_backtest(predictions)
    model = train_per_member_model(
        member_vote_examples_from_predictions(predictions), pooling_penalty=1.0
    )

    scored = score_per_member_backtest(backtest, model)

    assert scored.model_name == PER_MEMBER_MODEL_NAME
    assert scored.label_start == backtest.label_start
    assert scored.metrics.label_count == 2
    assert scored.metrics.evaluated_count == 2
    for rescored, original in zip(scored.predictions, predictions, strict=True):
        expected = model.predict_probability(original.member_bioguide_id, original.feature_signals)
        assert rescored.predicted_probability_yea == expected


def test_score_per_member_backtest_skips_predictions_without_feature_signals() -> None:
    predictions = [
        _prediction(member_id="A000001", vote_option="yea", signals={}, roll_call_number=1),
    ]
    backtest = _make_backtest(predictions)
    model = train_per_member_model(
        [MemberVoteExample(member_id="X", signals={"party_alignment": 1.0}, is_yea=True)],
        pooling_penalty=1.0,
    )

    scored = score_per_member_backtest(backtest, model)

    assert scored.predictions[0].skipped_reason == "missing_per_member_feature_signals"
    assert scored.metrics.evaluated_count == 0


def test_score_per_member_backtest_skips_when_model_untrained() -> None:
    predictions = [
        _prediction(
            member_id="A000001",
            vote_option="yea",
            signals={"party_alignment": 1.0},
            roll_call_number=1,
        ),
    ]
    backtest = _make_backtest(predictions)
    empty_model = train_per_member_model([], pooling_penalty=1.0)

    scored = score_per_member_backtest(backtest, empty_model)

    assert scored.predictions[0].skipped_reason == "missing_per_member_training_examples"
