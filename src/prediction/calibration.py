"""Calibration layer for vote predictions.

OVERALL_GOAL.md requires every prediction to ship with honest uncertainty:
per-politician temperature scaling + isotonic regression on a held-out
window + conformal prediction sets. A model can be accurate yet badly
calibrated -- its 0.9s are right only 70% of the time -- and a calibrated
probability is what the public read API and the per-jurisdiction/party/
faction dashboards depend on.

This module operates on held-out ``CalibrationExample`` rows (a model
probability and the realized binary label). It never sees raw features and
never trains on the evaluation window's labels for anything but post-hoc
calibration, so it composes with the strict no-leakage discipline in
``backtest.py``: callers fit on a held-out window and apply forward.

Three calibrators, all pure Python with no third-party ML dependency:

* **Temperature scaling** -- a single scalar ``T`` rescales the logit
  (``sigmoid(logit(p) / T)``). ``T > 1`` softens an overconfident model,
  ``T < 1`` sharpens an underconfident one. Per-politician temperatures are
  partial-pooled toward the global temperature so thin-record officials fall
  back to the population fit (mirroring the per-member model's pooling).
* **Isotonic regression** -- a monotone non-parametric remap fit by the
  pool-adjacent-violators algorithm; corrects any monotone distortion.
* **Split conformal** -- distribution-free prediction sets with a marginal
  coverage guarantee of ~``1 - alpha``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

_PROBABILITY_FLOOR = 1e-12
_LOGIT_CLAMP = 40.0
_MIN_TEMPERATURE = 0.1
_MAX_TEMPERATURE = 20.0
_GOLDEN_SECTION_ITERATIONS = 100


class CalibrationExample(BaseModel, frozen=True):
    """One held-out prediction: a model probability and the realized label."""

    member_id: str
    probability_yea: float = Field(ge=0.0, le=1.0)
    is_yea: bool


def _logit(probability: float) -> float:
    bounded = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, probability))
    return math.log(bounded / (1.0 - bounded))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, value))))


def apply_temperature(probability_yea: float, temperature: float) -> float:
    """Rescale a probability's logit by ``temperature``.

    ``temperature == 1`` is the exact identity so an un-fit calibrator never
    perturbs a probability.
    """
    if temperature <= 0.0:
        raise ValueError("temperature must be positive")
    if temperature == 1.0:
        return probability_yea
    return _sigmoid(_logit(probability_yea) / temperature)


def _temperature_log_loss(
    logits: Sequence[float], targets: Sequence[float], temperature: float
) -> float:
    total = 0.0
    inverse = 1.0 / temperature
    for logit, target in zip(logits, targets, strict=True):
        probability = _sigmoid(logit * inverse)
        clamped = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, probability))
        total += -(target * math.log(clamped) + (1.0 - target) * math.log(1.0 - clamped))
    return total / len(logits)


def _fit_temperature_from_logits(logits: Sequence[float], targets: Sequence[float]) -> float:
    """Minimize log-loss over the temperature by golden-section search.

    Log-loss is convex in the inverse temperature, hence unimodal in ``T``.
    """
    if not logits:
        return 1.0
    golden = (math.sqrt(5.0) - 1.0) / 2.0
    low, high = _MIN_TEMPERATURE, _MAX_TEMPERATURE
    left = high - golden * (high - low)
    right = low + golden * (high - low)
    left_loss = _temperature_log_loss(logits, targets, left)
    right_loss = _temperature_log_loss(logits, targets, right)
    for _ in range(_GOLDEN_SECTION_ITERATIONS):
        if left_loss < right_loss:
            high, right, right_loss = right, left, left_loss
            left = high - golden * (high - low)
            left_loss = _temperature_log_loss(logits, targets, left)
        else:
            low, left, left_loss = left, right, right_loss
            right = low + golden * (high - low)
            right_loss = _temperature_log_loss(logits, targets, right)
    return (low + high) / 2.0


def fit_global_temperature(examples: Iterable[CalibrationExample]) -> float:
    """Fit one temperature that minimizes held-out log-loss across all members."""
    materialized = list(examples)
    logits = [_logit(example.probability_yea) for example in materialized]
    targets = [1.0 if example.is_yea else 0.0 for example in materialized]
    return _fit_temperature_from_logits(logits, targets)


class PerPoliticianTemperatureCalibrator(BaseModel):
    """Per-politician temperatures shrunk toward a shared global temperature."""

    global_temperature: float = 1.0
    member_temperatures: dict[str, float] = Field(default_factory=dict)
    prior_strength: float = 0.0

    def calibrate(self, member_id: str, probability_yea: float) -> float:
        temperature = self.member_temperatures.get(member_id, self.global_temperature)
        return apply_temperature(probability_yea, temperature)


def fit_per_politician_temperature(
    examples: Iterable[CalibrationExample],
    *,
    prior_strength: float = 10.0,
) -> PerPoliticianTemperatureCalibrator:
    """Fit per-member temperatures, partial-pooled toward the global fit.

    Pooling happens in inverse-temperature space (the natural logistic scale):
    ``w_member = (n_member * w_hat + prior_strength * w_global) / (n_member +
    prior_strength)``. ``prior_strength`` is the pseudo-count of global
    evidence each member starts with, so members with few votes are dominated
    by the global fit and data-rich members express their own temperature.
    """
    if prior_strength < 0.0:
        raise ValueError("prior_strength must be non-negative")
    materialized = list(examples)
    global_temperature = fit_global_temperature(materialized)
    global_inverse = 1.0 / global_temperature

    by_member: dict[str, list[CalibrationExample]] = {}
    for example in materialized:
        by_member.setdefault(example.member_id, []).append(example)

    member_temperatures: dict[str, float] = {}
    for member_id, member_examples in by_member.items():
        logits = [_logit(example.probability_yea) for example in member_examples]
        targets = [1.0 if example.is_yea else 0.0 for example in member_examples]
        member_inverse = 1.0 / _fit_temperature_from_logits(logits, targets)
        count = len(member_examples)
        pooled_inverse = (count * member_inverse + prior_strength * global_inverse) / (
            count + prior_strength
        )
        member_temperatures[member_id] = 1.0 / pooled_inverse

    return PerPoliticianTemperatureCalibrator(
        global_temperature=global_temperature,
        member_temperatures=member_temperatures,
        prior_strength=prior_strength,
    )


class _IsotonicPoint(BaseModel):
    probability: float
    value: float


class IsotonicCalibrator(BaseModel):
    """A monotone non-decreasing calibration map fit by pool-adjacent-violators."""

    points: list[_IsotonicPoint] = Field(default_factory=list)

    def calibrate(self, probability_yea: float) -> float:
        if not self.points:
            return min(1.0, max(0.0, probability_yea))
        if probability_yea <= self.points[0].probability:
            return self.points[0].value
        if probability_yea >= self.points[-1].probability:
            return self.points[-1].value
        for left, right in zip(self.points, self.points[1:], strict=False):
            if probability_yea <= right.probability:
                span = right.probability - left.probability
                if span <= 0.0:
                    return right.value
                weight = (probability_yea - left.probability) / span
                interpolated = left.value + weight * (right.value - left.value)
                return min(1.0, max(0.0, interpolated))
        return self.points[-1].value


def fit_isotonic(examples: Iterable[CalibrationExample]) -> IsotonicCalibrator:
    """Fit a monotone calibration map via the pool-adjacent-violators algorithm."""
    materialized = sorted(examples, key=lambda example: example.probability_yea)
    if not materialized:
        return IsotonicCalibrator()

    # Each block tracks (weighted label sum, count, probability sum) so a merged
    # block's value is the mean label and its position is the mean probability.
    blocks: list[list[float]] = []
    for example in materialized:
        blocks.append([1.0 if example.is_yea else 0.0, 1.0, example.probability_yea])
        while len(blocks) >= 2 and (blocks[-2][0] / blocks[-2][1]) > (
            blocks[-1][0] / blocks[-1][1]
        ):
            label_sum, count, probability_sum = blocks.pop()
            blocks[-1][0] += label_sum
            blocks[-1][1] += count
            blocks[-1][2] += probability_sum

    points = [
        _IsotonicPoint(probability=probability_sum / count, value=label_sum / count)
        for label_sum, count, probability_sum in blocks
    ]
    return IsotonicCalibrator(points=points)


def expected_calibration_error(
    probabilities: Sequence[float],
    labels: Sequence[bool],
    *,
    bins: int = 10,
) -> float:
    """Equal-width binned expected calibration error (lower is better)."""
    if bins <= 0:
        raise ValueError("bins must be positive")
    if len(probabilities) != len(labels):
        raise ValueError("probabilities and labels must align")
    if not probabilities:
        return 0.0
    bucket_label_sum = [0.0] * bins
    bucket_probability_sum = [0.0] * bins
    bucket_count = [0] * bins
    for probability, label in zip(probabilities, labels, strict=True):
        index = min(bins - 1, max(0, int(probability * bins)))
        bucket_label_sum[index] += 1.0 if label else 0.0
        bucket_probability_sum[index] += probability
        bucket_count[index] += 1
    total = len(probabilities)
    error = 0.0
    for index in range(bins):
        count = bucket_count[index]
        if count == 0:
            continue
        accuracy = bucket_label_sum[index] / count
        confidence = bucket_probability_sum[index] / count
        error += (count / total) * abs(accuracy - confidence)
    return error


def fit_conformal_threshold(
    examples: Iterable[CalibrationExample],
    *,
    alpha: float,
) -> float:
    """Split-conformal nonconformity threshold for ~``1 - alpha`` coverage.

    The nonconformity score of a calibration example is ``1 - p(true label)``.
    The threshold is the ``ceil((n + 1)(1 - alpha)) / n`` empirical quantile of
    those scores, which yields the standard finite-sample coverage guarantee.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    scores = sorted(
        (1.0 - example.probability_yea) if example.is_yea else example.probability_yea
        for example in examples
    )
    count = len(scores)
    if count == 0:
        return 1.0
    rank = math.ceil((count + 1) * (1.0 - alpha))
    if rank >= count:
        return 1.0
    return scores[rank - 1]


def conformal_label_set(probability_yea: float, *, threshold: float) -> frozenset[str]:
    """The conformal prediction set: classes whose nonconformity is within threshold."""
    labels: set[str] = set()
    if (1.0 - probability_yea) <= threshold:
        labels.add("yea")
    if probability_yea <= threshold:
        labels.add("nay")
    return frozenset(labels)
