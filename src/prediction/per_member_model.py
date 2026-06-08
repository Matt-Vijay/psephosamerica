"""Hierarchical partial-pooling per-member vote model.

OVERALL_GOAL.md's controlled experiment showed that the global linear
models (``member_vote_rate_baseline``, ``ontology_signal_model``,
``learned_signal_logistic``) sit at or below the predict-the-global-rate
reference on the cross-pressured slice, while a tiny per-member model
(per-member intercept + per-member signal slope + a shared global term)
lifts accuracy from ~55% to ~81%. The architectural constraint, not the
data plumbing, was pinning the metrics.

This module implements that model with **hierarchical partial pooling**:
every member shares a global intercept and global per-signal coefficients,
and additionally carries member-specific *offsets* on the intercept and on
each signal slope. The offsets are L2-shrunk toward zero with strength
``pooling_penalty``. A member with few votes cannot overcome the penalty,
so their prediction falls back to the global (pooled) model; a member with
many votes accumulates enough gradient to express their own response. As
votes accumulate, the prior weight shrinks -- exactly the thin-record
handling the blueprint calls for, and the most data-efficient prior for
brand-new officials with zero votes (who get the pure global prediction).

The model is leakage-safe by construction: it reads only ``feature_signals``
and the realized binary label from each training example, and the caller is
responsible (as elsewhere in this package) for passing only pre-cutoff
training predictions. It is pure Python with no third-party ML dependency,
matching the existing ``_train_learned_signal_model`` in ``eval_report``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

from src.prediction.backtest import (
    PredictionBacktestMetricsPayload,
    PredictionBacktestPayload,
    PredictionBacktestPredictionPayload,
    _distribution_with_binary_probability,
    _log_loss,
    _predicted_vote_option,
    _rate,
)

PER_MEMBER_MODEL_NAME = "per_member_signal_model"

DEFAULT_POOLING_PENALTY = 1.0
DEFAULT_GLOBAL_L2 = 0.01
DEFAULT_LEARNING_RATE = 0.35
DEFAULT_EPOCHS = 400

_LOGIT_CLAMP = 40.0
_PRIOR_RATE_FLOOR = 0.05
_PRIOR_RATE_CEILING = 0.95


class MemberVoteExample(BaseModel, frozen=True):
    """One realized member vote with its pre-cutoff feature signals."""

    member_id: str
    signals: Mapping[str, float]
    is_yea: bool


class PerMemberModel(BaseModel):
    """A fitted partial-pooling logistic model over member-vote signals."""

    signal_names: list[str] = Field(default_factory=list)
    global_intercept: float = 0.0
    global_coefficients: dict[str, float] = Field(default_factory=dict)
    member_intercept_offsets: dict[str, float] = Field(default_factory=dict)
    member_coefficient_offsets: dict[str, dict[str, float]] = Field(default_factory=dict)
    pooling_penalty: float = DEFAULT_POOLING_PENALTY
    training_example_count: int = 0
    member_count: int = 0

    def predict_probability(self, member_id: str, signals: Mapping[str, float]) -> float:
        """Yea probability for a member; unknown members use the global model."""
        intercept = self.global_intercept + self.member_intercept_offsets.get(member_id, 0.0)
        member_offsets = self.member_coefficient_offsets.get(member_id, {})
        raw = intercept + sum(
            (self.global_coefficients[name] + member_offsets.get(name, 0.0))
            * signals.get(name, 0.0)
            for name in self.global_coefficients
        )
        return 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))


def _logit(value: float) -> float:
    bounded = min(_PRIOR_RATE_CEILING, max(_PRIOR_RATE_FLOOR, value))
    return math.log(bounded / (1.0 - bounded))


def train_per_member_model(
    examples: Iterable[MemberVoteExample],
    *,
    pooling_penalty: float = DEFAULT_POOLING_PENALTY,
    global_l2: float = DEFAULT_GLOBAL_L2,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    epochs: int = DEFAULT_EPOCHS,
) -> PerMemberModel:
    """Fit the partial-pooling model with deterministic full-batch gradient descent.

    ``pooling_penalty`` is the L2 strength on every member-specific offset.
    As it grows large the offsets collapse to zero and the model reduces to a
    single shared global logistic regression; at moderate values members with
    enough votes express their own intercept and slopes.
    """
    if pooling_penalty < 0.0:
        raise ValueError("pooling_penalty must be non-negative")
    if global_l2 < 0.0:
        raise ValueError("global_l2 must be non-negative")
    if learning_rate <= 0.0:
        raise ValueError("learning_rate must be positive")
    if epochs <= 0:
        raise ValueError("epochs must be positive")

    materialized = list(examples)
    signal_names = sorted({signal for example in materialized for signal in example.signals})
    member_ids = sorted({example.member_id for example in materialized})
    if not materialized or not signal_names:
        return PerMemberModel(
            signal_names=signal_names,
            global_coefficients={signal: 0.0 for signal in signal_names},
            pooling_penalty=pooling_penalty,
            training_example_count=len(materialized),
            member_count=len(member_ids),
        )

    global_intercept = _logit(
        sum(1 for example in materialized if example.is_yea) / len(materialized)
    )
    global_coefficients = {signal: 0.0 for signal in signal_names}
    intercept_offsets = {member_id: 0.0 for member_id in member_ids}
    coefficient_offsets = {
        member_id: {signal: 0.0 for signal in signal_names} for member_id in member_ids
    }
    scale = 1.0 / len(materialized)
    member_example_counts = {member_id: 0 for member_id in member_ids}
    for example in materialized:
        member_example_counts[example.member_id] += 1

    for _ in range(epochs):
        global_intercept_gradient = 0.0
        global_coefficient_gradients = {signal: 0.0 for signal in signal_names}
        member_intercept_gradients = {member_id: 0.0 for member_id in member_ids}
        member_coefficient_gradients = {
            member_id: {signal: 0.0 for signal in signal_names} for member_id in member_ids
        }
        for example in materialized:
            member_id = example.member_id
            member_offsets = coefficient_offsets[member_id]
            raw = (global_intercept + intercept_offsets[member_id]) + sum(
                (global_coefficients[signal] + member_offsets[signal])
                * example.signals.get(signal, 0.0)
                for signal in signal_names
            )
            predicted = 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))
            error = predicted - (1.0 if example.is_yea else 0.0)
            global_intercept_gradient += error
            member_intercept_gradients[member_id] += error
            for signal in signal_names:
                feature = example.signals.get(signal, 0.0)
                global_coefficient_gradients[signal] += error * feature
                member_coefficient_gradients[member_id][signal] += error * feature

        global_intercept -= learning_rate * global_intercept_gradient * scale
        for signal in signal_names:
            gradient = (
                global_coefficient_gradients[signal] * scale
                + global_l2 * global_coefficients[signal]
            )
            global_coefficients[signal] -= learning_rate * gradient
        # Member offsets are fit at each member's own data rate (1/n_m) so the
        # slope is learned regardless of how many *other* members are in the
        # batch, then shrunk by a proximal L2 operator whose strength scales
        # with penalty / n_m. This is volume-aware hierarchical partial pooling:
        # a member with many votes (penalty / n_m small) keeps their own slope;
        # a thin-record member (penalty / n_m large) is shrunk back toward the
        # shared global fit. The proximal form is numerically stable for any
        # penalty -- a plain gradient step on a huge penalty diverges -- and as
        # pooling_penalty -> infinity every offset collapses to zero (pure global).
        for member_id in member_ids:
            member_scale = 1.0 / member_example_counts[member_id]
            shrink = 1.0 / (1.0 + learning_rate * pooling_penalty * member_scale)
            intercept = intercept_offsets[member_id]
            intercept -= learning_rate * member_intercept_gradients[member_id] * member_scale
            intercept_offsets[member_id] = intercept * shrink
            member_offsets = coefficient_offsets[member_id]
            member_gradients = member_coefficient_gradients[member_id]
            for signal in signal_names:
                offset = member_offsets[signal]
                offset -= learning_rate * member_gradients[signal] * member_scale
                member_offsets[signal] = offset * shrink

    return PerMemberModel(
        signal_names=signal_names,
        global_intercept=global_intercept,
        global_coefficients=global_coefficients,
        member_intercept_offsets=intercept_offsets,
        member_coefficient_offsets=coefficient_offsets,
        pooling_penalty=pooling_penalty,
        training_example_count=len(materialized),
        member_count=len(member_ids),
    )


def member_vote_examples_from_predictions(
    predictions: Iterable[PredictionBacktestPredictionPayload],
) -> list[MemberVoteExample]:
    """Adapt scored backtest predictions into binary training examples.

    Mirrors ``_train_learned_signal_model``'s filter: keep only non-skipped
    predictions with a binary yea/nay label and at least one feature signal.
    """
    return [
        MemberVoteExample(
            member_id=prediction.member_bioguide_id,
            signals=dict(prediction.feature_signals),
            is_yea=prediction.actual_vote_option == "yea",
        )
        for prediction in predictions
        if prediction.skipped_reason is None
        and prediction.actual_vote_option in {"yea", "nay"}
        and prediction.feature_signals
    ]


def _score_per_member_prediction(
    prediction: PredictionBacktestPredictionPayload,
    model: PerMemberModel,
) -> PredictionBacktestPredictionPayload:
    data = prediction.model_dump()
    data.update(
        {
            "predicted_probability_yea": None,
            "predicted_vote_option": None,
            "predicted_vote_probabilities": {},
            "correct": None,
            "brier_score": None,
            "log_loss": None,
        }
    )
    if prediction.skipped_reason is not None:
        data["skipped_reason"] = prediction.skipped_reason
        return PredictionBacktestPredictionPayload.model_validate(data)
    if not model.global_coefficients:
        data["skipped_reason"] = "missing_per_member_training_examples"
        return PredictionBacktestPredictionPayload.model_validate(data)
    if not prediction.feature_signals:
        data["skipped_reason"] = "missing_per_member_feature_signals"
        return PredictionBacktestPredictionPayload.model_validate(data)

    probability = model.predict_probability(
        prediction.member_bioguide_id, prediction.feature_signals
    )
    distribution = _distribution_with_binary_probability(
        prediction.predicted_vote_probabilities,
        probability_yea=probability,
    )
    probability_yea = distribution["yea"]
    predicted_vote = _predicted_vote_option(distribution)
    is_binary = prediction.actual_vote_option in {"yea", "nay"}
    actual_yea = 1.0 if prediction.actual_vote_option == "yea" else 0.0
    data.update(
        {
            "predicted_probability_yea": probability_yea,
            "predicted_vote_option": predicted_vote,
            "predicted_vote_probabilities": distribution,
            "correct": predicted_vote == prediction.actual_vote_option,
            "brier_score": (probability_yea - actual_yea) ** 2 if is_binary else None,
            "log_loss": _log_loss(probability_yea, actual_yea) if is_binary else None,
            "skipped_reason": None,
        }
    )
    return PredictionBacktestPredictionPayload.model_validate(data)


def score_per_member_backtest(
    backtest: PredictionBacktestPayload,
    model: PerMemberModel,
) -> PredictionBacktestPayload:
    """Re-score an existing (cutoff-safe) backtest's labels with the per-member model.

    The label window, member set, and vote-event accounting are inherited from
    ``backtest`` (typically the ontology backtest over the evaluation window),
    so no-leakage discipline is preserved: only the per-vote probability is
    replaced. Returns a validated payload under ``PER_MEMBER_MODEL_NAME``.
    """
    predictions = [
        _score_per_member_prediction(prediction, model) for prediction in backtest.predictions
    ]
    evaluated = [item for item in predictions if item.correct is not None]
    brier_scores = [item.brier_score for item in evaluated if item.brier_score is not None]
    log_losses = [item.log_loss for item in evaluated if item.log_loss is not None]
    correct_count = sum(1 for item in evaluated if item.correct)
    metrics = PredictionBacktestMetricsPayload(
        label_count=len(predictions),
        evaluated_count=len(evaluated),
        correct_count=correct_count,
        skipped_count=sum(1 for item in predictions if item.skipped_reason is not None),
        accuracy=_rate(correct_count, len(evaluated)) if evaluated else None,
        brier_score=sum(brier_scores) / len(brier_scores) if brier_scores else None,
        log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
    )
    return PredictionBacktestPayload(
        model_name=PER_MEMBER_MODEL_NAME,
        feature_cutoff=backtest.feature_cutoff,
        label_start=backtest.label_start,
        label_end=backtest.label_end,
        feature_vote_event_count=backtest.feature_vote_event_count,
        label_vote_event_count=backtest.label_vote_event_count,
        member_count=backtest.member_count,
        metrics=metrics,
        predictions=predictions,
    )
