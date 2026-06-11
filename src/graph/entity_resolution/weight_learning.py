"""Learn Fellegi-Sunter match weights from labeled pairs.

The scorer's default weights are hand-tuned; this is the data-driven path a
Splink-style linkage uses. Given labeled ``(record_a, record_b, is_match)``
pairs, :func:`learn_match_weights` estimates, per comparator dimension, the
``m`` and ``u`` probabilities — ``m = P(agree | match)`` and
``u = P(agree | non-match)`` — and converts them to log-odds:

* agreement weight ``= ln(m / u)``
* disagreement weight ``= ln((1 - m) / (1 - u))``

plus a prior ``= ln(p / (1 - p))`` from the labeled match rate. Counts are
Laplace-smoothed so a dimension never produces ``ln 0``. Dimensions with no
applicable evidence (or an empty set) keep the supplied default weights, so the
result is always a complete, usable :class:`MatchWeights` that drops straight
into :func:`~src.graph.entity_resolution.scoring.score_pair`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace

from src.graph.entity_resolution.records import SourceRecord
from src.graph.entity_resolution.scoring import (
    DEFAULT_WEIGHTS,
    LEARNABLE_DIMENSIONS,
    MatchWeights,
    person_comparison_features,
)

LabeledPair = tuple[SourceRecord, SourceRecord, bool]


def learn_match_weights(
    labeled_pairs: Iterable[LabeledPair],
    *,
    default: MatchWeights = DEFAULT_WEIGHTS,
    pseudocount: float = 0.5,
) -> MatchWeights:
    """Estimate Fellegi-Sunter weights from labeled pairs (falling back to ``default``)."""
    agree_match = dict.fromkeys(LEARNABLE_DIMENSIONS, 0)
    seen_match = dict.fromkeys(LEARNABLE_DIMENSIONS, 0)
    agree_non = dict.fromkeys(LEARNABLE_DIMENSIONS, 0)
    seen_non = dict.fromkeys(LEARNABLE_DIMENSIONS, 0)
    n_match = 0
    n_total = 0

    for record_a, record_b, is_match in labeled_pairs:
        n_total += 1
        n_match += int(is_match)
        for dim, agreed in person_comparison_features(record_a, record_b).items():
            if is_match:
                seen_match[dim] += 1
                agree_match[dim] += int(agreed)
            else:
                seen_non[dim] += 1
                agree_non[dim] += int(agreed)

    if n_total == 0:
        return default

    overrides: dict[str, float] = {}
    for dim in LEARNABLE_DIMENSIONS:
        if seen_match[dim] == 0 or seen_non[dim] == 0:
            continue  # not enough evidence — keep the default for this dimension
        m = _smoothed(agree_match[dim], seen_match[dim], pseudocount)
        u = _smoothed(agree_non[dim], seen_non[dim], pseudocount)
        overrides[f"{dim}_agree"] = math.log(m / u)
        overrides[f"{dim}_disagree"] = math.log((1.0 - m) / (1.0 - u))

    prior_p = _smoothed(n_match, n_total, pseudocount)
    overrides["prior"] = math.log(prior_p / (1.0 - prior_p))

    return replace(default, **overrides)


def _smoothed(agree: int, total: int, pseudocount: float) -> float:
    return (agree + pseudocount) / (total + 2.0 * pseudocount)
