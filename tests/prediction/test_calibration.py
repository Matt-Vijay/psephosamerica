"""Tests for the prediction calibration layer.

OVERALL_GOAL.md requires honest uncertainty: per-politician temperature
scaling + isotonic regression on a held-out window + conformal prediction
sets. These tests pin the calibration contract on synthetic miscalibrated
data where the right answer is known by construction.
"""

from __future__ import annotations

import math
import random

from src.prediction.calibration import (
    CalibrationExample,
    apply_temperature,
    conformal_label_set,
    expected_calibration_error,
    fit_conformal_threshold,
    fit_global_temperature,
    fit_isotonic,
    fit_per_politician_temperature,
)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _logit(value: float) -> float:
    bounded = min(1 - 1e-12, max(1e-12, value))
    return math.log(bounded / (1.0 - bounded))


def _log_loss(examples: list[CalibrationExample], probabilities: list[float]) -> float:
    total = 0.0
    for example, probability in zip(examples, probabilities, strict=True):
        clamped = min(1 - 1e-15, max(1e-15, probability))
        total += -(
            (1.0 if example.is_yea else 0.0) * math.log(clamped)
            + (0.0 if example.is_yea else 1.0) * math.log(1.0 - clamped)
        )
    return total / len(examples)


def _overconfident_examples(
    seed: int, *, count: int = 4000, sharpen: float = 2.0, member_id: str = "M"
) -> list[CalibrationExample]:
    """Model is overconfident: its logits are ``sharpen`` x the true logits.

    The temperature that fixes this is exactly ``sharpen``.
    """
    rng = random.Random(seed)
    examples: list[CalibrationExample] = []
    for _ in range(count):
        true_logit = rng.uniform(-3.0, 3.0)
        true_probability = _sigmoid(true_logit)
        model_probability = _sigmoid(sharpen * true_logit)
        examples.append(
            CalibrationExample(
                member_id=member_id,
                probability_yea=model_probability,
                is_yea=rng.random() < true_probability,
            )
        )
    return examples


def test_fit_global_temperature_recovers_overconfidence_and_lowers_log_loss() -> None:
    examples = _overconfident_examples(seed=1, sharpen=2.0)
    temperature = fit_global_temperature(examples)

    # The data is sharpened 2x, so the corrective temperature is ~2.
    assert 1.5 < temperature < 2.6

    raw = [example.probability_yea for example in examples]
    calibrated = [apply_temperature(example.probability_yea, temperature) for example in examples]
    assert _log_loss(examples, calibrated) < _log_loss(examples, raw)


def test_apply_temperature_of_one_is_identity() -> None:
    assert apply_temperature(0.73, 1.0) == 0.73
    assert apply_temperature(0.01, 1.0) == 0.01


def test_well_calibrated_data_keeps_temperature_near_one() -> None:
    examples = _overconfident_examples(seed=2, sharpen=1.0)
    temperature = fit_global_temperature(examples)
    assert 0.85 < temperature < 1.2


def test_fit_isotonic_is_monotone_and_lowers_brier_on_distorted_scores() -> None:
    rng = random.Random(3)
    examples: list[CalibrationExample] = []
    for _ in range(4000):
        true_probability = rng.random()
        # Squaring is a monotone distortion that pushes scores toward 0.
        distorted = true_probability**2
        examples.append(
            CalibrationExample(
                member_id="M",
                probability_yea=distorted,
                is_yea=rng.random() < true_probability,
            )
        )
    calibrator = fit_isotonic(examples)

    # Monotone non-decreasing.
    grid = [i / 50 for i in range(51)]
    mapped = [calibrator.calibrate(value) for value in grid]
    assert all(later >= earlier - 1e-9 for earlier, later in zip(mapped, mapped[1:], strict=False))

    def brier(probabilities: list[float]) -> float:
        return sum(
            (probability - (1.0 if example.is_yea else 0.0)) ** 2
            for example, probability in zip(examples, probabilities, strict=True)
        ) / len(examples)

    raw = [example.probability_yea for example in examples]
    calibrated = [calibrator.calibrate(example.probability_yea) for example in examples]
    assert brier(calibrated) < brier(raw)


def test_isotonic_calibrator_clamps_to_unit_interval() -> None:
    examples = [
        CalibrationExample(member_id="M", probability_yea=0.2, is_yea=False),
        CalibrationExample(member_id="M", probability_yea=0.8, is_yea=True),
    ]
    calibrator = fit_isotonic(examples)
    assert 0.0 <= calibrator.calibrate(-5.0) <= 1.0
    assert 0.0 <= calibrator.calibrate(5.0) <= 1.0


def test_per_politician_temperature_pools_toward_global() -> None:
    overconfident = _overconfident_examples(seed=4, sharpen=2.0, count=2000, member_id="A")
    underconfident = _overconfident_examples(seed=5, sharpen=0.5, count=2000, member_id="B")
    examples = overconfident + underconfident

    loose = fit_per_politician_temperature(examples, prior_strength=1.0)
    tight = fit_per_politician_temperature(examples, prior_strength=1e6)

    # A loose prior lets each member express their own temperature: the
    # overconfident member wants T > 1, the underconfident one wants T < 1.
    assert loose.member_temperatures["A"] > 1.4
    assert loose.member_temperatures["B"] < 0.9
    assert loose.member_temperatures["A"] > loose.member_temperatures["B"]

    # A strong prior pools every member back toward the shared global fit --
    # exactly the behavior thin-record officials rely on.
    assert abs(tight.member_temperatures["A"] - tight.global_temperature) < 0.1
    assert abs(tight.member_temperatures["B"] - tight.global_temperature) < 0.1


def test_per_politician_calibrate_falls_back_to_global_for_unknown_member() -> None:
    examples = _overconfident_examples(seed=6, sharpen=2.0)
    calibrator = fit_per_politician_temperature(examples, prior_strength=10.0)
    expected = apply_temperature(0.9, calibrator.global_temperature)
    assert calibrator.calibrate("never-seen", 0.9) == expected


def test_expected_calibration_error_drops_after_temperature_scaling() -> None:
    examples = _overconfident_examples(seed=7, sharpen=2.0)
    raw_ece = expected_calibration_error(
        [example.probability_yea for example in examples],
        [example.is_yea for example in examples],
    )
    temperature = fit_global_temperature(examples)
    calibrated_ece = expected_calibration_error(
        [apply_temperature(example.probability_yea, temperature) for example in examples],
        [example.is_yea for example in examples],
    )
    assert calibrated_ece < raw_ece


def test_conformal_label_set_covers_at_target_rate() -> None:
    rng = random.Random(8)
    calibration: list[CalibrationExample] = []
    holdout: list[CalibrationExample] = []
    for index in range(8000):
        probability = rng.random()
        example = CalibrationExample(
            member_id="M", probability_yea=probability, is_yea=rng.random() < probability
        )
        (calibration if index % 2 == 0 else holdout).append(example)

    alpha = 0.1
    threshold = fit_conformal_threshold(calibration, alpha=alpha)
    covered = 0
    for example in holdout:
        label_set = conformal_label_set(example.probability_yea, threshold=threshold)
        actual = "yea" if example.is_yea else "nay"
        if actual in label_set:
            covered += 1
    coverage = covered / len(holdout)
    # Split-conformal guarantees ~(1 - alpha) marginal coverage.
    assert coverage >= 1 - alpha - 0.03


def test_conformal_label_set_returns_subset_of_classes() -> None:
    # A class is kept when its nonconformity (1 - prob) is within threshold.
    assert conformal_label_set(0.5, threshold=0.9) == frozenset({"yea", "nay"})
    high_confidence = conformal_label_set(0.99, threshold=0.2)
    assert high_confidence == frozenset({"yea"})
