"""Fellegi-Sunter match scoring for entity-resolution candidate pairs.

Given two :class:`~src.graph.entity_resolution.records.SourceRecord` objects,
:func:`score_pair` returns a calibrated :class:`MatchScore` — an additive
log-odds, its logistic probability, a three-way ``decision``, and a list of
human-readable ``reasons`` (the audit trail every merge must carry).

The three-way decision is deliberate. ``"possible"`` is the band that feeds the
human-in-the-loop review queue: too uncertain to auto-merge, too plausible to
auto-reject. Disputed pairs stay as separate possibilities rather than being
force-collapsed (the blueprint's explicit requirement).

Two hard rules override the additive score because they encode source-asserted
identity rather than fuzzy similarity:

* **shared external ID** (matching FEC/bioguide value) -> ``match``;
* **conflicting external ID** (same namespace, disjoint values) -> ``no_match``.

Weights default to a documented hand-tuned table; a learned Splink model drops
in by passing a different :class:`MatchWeights`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from src.graph.entity_resolution.names import (
    given_names_compatible,
    normalize_name_token,
)
from src.graph.entity_resolution.records import SourceRecord

MatchDecision = Literal["match", "possible", "no_match"]

# Logit ceiling for hard rules: logistic(+/-16) is within 1e-7 of 1/0, so hard
# decisions stay finite (and JSON-serializable) while reading as certain.
_LOGIT_CAP = 16.0


def logistic(log_odds: float) -> float:
    """The logistic (sigmoid) of a log-odds value, clamped to avoid overflow."""
    if log_odds >= _LOGIT_CAP:
        return 1.0 / (1.0 + math.exp(-_LOGIT_CAP))
    if log_odds <= -_LOGIT_CAP:
        return 1.0 / (1.0 + math.exp(_LOGIT_CAP))
    return 1.0 / (1.0 + math.exp(-log_odds))


@dataclass(frozen=True)
class MatchWeights:
    """Additive log-odds contributions for each comparator (Fellegi-Sunter)."""

    prior: float = -1.5
    family_agree: float = 2.5
    family_disagree: float = -3.0
    given_agree: float = 1.5
    given_disagree: float = -2.5
    middle_agree: float = 0.5
    middle_disagree: float = -2.0
    suffix_conflict: float = -3.0
    jurisdiction_agree: float = 0.5
    jurisdiction_disagree: float = -0.5
    region_agree: float = 0.7
    region_disagree: float = -0.3


@dataclass(frozen=True)
class MatchThresholds:
    """Probability cutoffs separating match / possible / no_match."""

    match_prob: float = 0.9
    possible_prob: float = 0.45


@dataclass(frozen=True)
class MatchScore:
    """The scored outcome of comparing two source records."""

    log_odds: float
    probability: float
    decision: MatchDecision
    reasons: tuple[str, ...]


DEFAULT_WEIGHTS = MatchWeights()
DEFAULT_THRESHOLDS = MatchThresholds()


def _values_by_system(record: SourceRecord) -> dict[str, set[str]]:
    systems: dict[str, set[str]] = {}
    for ext in record.external_ids:
        systems.setdefault(ext.system, set()).add(ext.value.casefold())
    return systems


def _external_id_conflict(a: SourceRecord, b: SourceRecord) -> bool:
    """True when a shared namespace carries entirely different values."""
    systems_a = _values_by_system(a)
    systems_b = _values_by_system(b)
    for system in systems_a.keys() & systems_b.keys():
        if not (systems_a[system] & systems_b[system]):
            return True
    return False


# Comparator dimensions whose agree/disagree weights can be learned from data.
LEARNABLE_DIMENSIONS = ("family", "given", "middle", "jurisdiction", "region")


def person_comparison_features(a: SourceRecord, b: SourceRecord) -> dict[str, bool]:
    """The per-comparator agree/disagree outcomes for two *person* records.

    Returns ``{dimension: agreed}`` for each applicable dimension (a dimension is
    absent when one side lacks the data — e.g. no middle name). This is the
    canonical feature view that both the scorer's term logic and the
    weight learner (:mod:`src.graph.entity_resolution.weight_learning`) agree on,
    so learned weights line up with how ``score_pair`` consumes them.
    """
    features: dict[str, bool] = {}
    name_a, name_b = a.person_name(), b.person_name()
    assert name_a is not None and name_b is not None  # callers pass person records
    if name_a.family and name_b.family:
        features["family"] = name_a.family == name_b.family
    if name_a.given and name_b.given:
        features["given"] = given_names_compatible(name_a.given, name_b.given)
    if name_a.middle and name_b.middle:
        features["middle"] = name_a.comparison_vector(name_b).middle_compatible
    if a.jurisdiction is not None and b.jurisdiction is not None:
        features["jurisdiction"] = a.jurisdiction == b.jurisdiction
    if a.region is not None and b.region is not None:
        features["region"] = a.region.casefold() == b.region.casefold()
    return features


def _person_terms(a: SourceRecord, b: SourceRecord, w: MatchWeights) -> list[tuple[str, float]]:
    name_a = a.person_name()
    name_b = b.person_name()
    assert name_a is not None and name_b is not None  # guaranteed: both person
    terms: list[tuple[str, float]] = []

    if name_a.family and name_b.family:
        if name_a.family == name_b.family:
            terms.append(("family_exact", w.family_agree))
        else:
            terms.append(("family_mismatch", w.family_disagree))

    if name_a.given and name_b.given:
        if given_names_compatible(name_a.given, name_b.given):
            terms.append(("given_compatible", w.given_agree))
        else:
            terms.append(("given_mismatch", w.given_disagree))

    if name_a.middle and name_b.middle:
        if name_a.comparison_vector(name_b).middle_compatible:
            terms.append(("middle_compatible", w.middle_agree))
        else:
            terms.append(("middle_conflict", w.middle_disagree))

    if name_a.comparison_vector(name_b).suffix_conflict:
        terms.append(("suffix_conflict", w.suffix_conflict))

    return terms


def _org_terms(a: SourceRecord, b: SourceRecord, w: MatchWeights) -> list[tuple[str, float]]:
    name_a = normalize_name_token(a.display_name)
    name_b = normalize_name_token(b.display_name)
    if not name_a or not name_b:
        return []
    if name_a == name_b:
        return [("org_name_match", w.family_agree)]
    return [("org_name_mismatch", w.family_disagree)]


def _context_terms(a: SourceRecord, b: SourceRecord, w: MatchWeights) -> list[tuple[str, float]]:
    terms: list[tuple[str, float]] = []
    if a.jurisdiction is not None and b.jurisdiction is not None:
        if a.jurisdiction == b.jurisdiction:
            terms.append(("jurisdiction_match", w.jurisdiction_agree))
        else:
            terms.append(("jurisdiction_mismatch", w.jurisdiction_disagree))
    if a.region is not None and b.region is not None:
        if a.region.casefold() == b.region.casefold():
            terms.append(("region_match", w.region_agree))
        else:
            terms.append(("region_mismatch", w.region_disagree))
    return terms


def score_pair(
    a: SourceRecord,
    b: SourceRecord,
    *,
    weights: MatchWeights = DEFAULT_WEIGHTS,
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
) -> MatchScore:
    """Score whether two records denote the same entity."""
    if a.entity_type != b.entity_type:
        return MatchScore(-_LOGIT_CAP, logistic(-_LOGIT_CAP), "no_match", ("entity_type_mismatch",))

    if a.entity_type == "person":
        terms = _person_terms(a, b, weights)
    else:
        terms = _org_terms(a, b, weights)
    terms += _context_terms(a, b, weights)
    reasons = tuple(reason for reason, _ in terms)

    if _external_id_conflict(a, b):
        return MatchScore(
            -_LOGIT_CAP, logistic(-_LOGIT_CAP), "no_match", ("external_id_conflict",) + reasons
        )
    if a.shares_external_id(b):
        return MatchScore(
            _LOGIT_CAP, logistic(_LOGIT_CAP), "match", ("shared_external_id",) + reasons
        )

    log_odds = weights.prior + math.fsum(weight for _, weight in terms)
    probability = logistic(log_odds)
    return MatchScore(log_odds, probability, _decide(probability, thresholds), reasons)


def _decide(probability: float, thresholds: MatchThresholds) -> MatchDecision:
    if probability >= thresholds.match_prob:
        return "match"
    if probability >= thresholds.possible_prob:
        return "possible"
    return "no_match"
