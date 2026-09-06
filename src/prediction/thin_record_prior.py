"""Thin-record prior for officials with little or no voting history.

OVERALL_GOAL.md's scaling key: a first-term official with zero votes is still
predicted, via a weighted blend of three priors --

1. **Party-in-state.** Officials of the same party in the same state.
2. **Endorser cluster.** Officials who share at least one endorser.
3. **Similar-by-network.** Officials nearest in structural-embedding space
   (``nn/structural_embeddings.py``), the most data-efficient signal for an
   unknown official.

The blend happens in logit space over whichever groups have members. As the
official accumulates their own votes, :func:`posterior_after_votes` shrinks the
prior's weight toward the observed rate -- so the prior dominates at zero votes
and fades as evidence arrives, exactly the partial-pooling behavior the
blueprint specifies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.prediction.logistic import clamped_sigmoid
from src.prediction.nn.structural_embeddings import StructuralEmbeddings

_PROBABILITY_FLOOR = 1e-6


@dataclass(frozen=True)
class StanceReference:
    """A known official's yea-tendency (as a logit) plus its grouping attributes."""

    official_id: str
    stance_logit: float
    party: str
    state: str
    endorsers: frozenset[str]
    structural_key: str | None = None


@dataclass(frozen=True)
class ThinRecordTarget:
    """The unknown official whose stance prior we are forming."""

    party: str
    state: str
    endorsers: frozenset[str]
    structural_key: str | None = None


def _sigmoid(value: float) -> float:
    # Shared clamped sigmoid: an extreme stance logit must not overflow math.exp.
    return clamped_sigmoid(value)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _party_state_logit(target: ThinRecordTarget, references: list[StanceReference]) -> float | None:
    matches = [
        ref.stance_logit
        for ref in references
        if ref.party == target.party and ref.state == target.state
    ]
    return _mean(matches) if matches else None


def _endorser_logit(target: ThinRecordTarget, references: list[StanceReference]) -> float | None:
    matches = [ref.stance_logit for ref in references if ref.endorsers & target.endorsers]
    return _mean(matches) if matches else None


def _network_logit(
    target: ThinRecordTarget,
    references: list[StanceReference],
    embeddings: StructuralEmbeddings | None,
    network_top_k: int,
) -> float | None:
    if embeddings is None or target.structural_key is None:
        return None
    if embeddings.get(target.structural_key) is None:
        return None
    scored: list[tuple[float, str, float]] = []
    for ref in references:
        if ref.structural_key is None or embeddings.get(ref.structural_key) is None:
            continue
        similarity = embeddings.similarity(target.structural_key, ref.structural_key)
        if similarity > 0.0:
            scored.append((similarity, ref.official_id, ref.stance_logit))
    if not scored:
        return None
    # BLAS implementations can differ in the last few bits. Rank numerical ties
    # by identity, not input order or rounding noise; retain full-precision weights.
    scored.sort(key=lambda item: (-round(item[0], 12), item[1]))
    top = scored[:network_top_k]
    weight_total = sum(similarity for similarity, _, _ in top)
    return sum(similarity * logit for similarity, _, logit in top) / weight_total


def thin_record_prior(
    target: ThinRecordTarget,
    references: list[StanceReference],
    *,
    embeddings: StructuralEmbeddings | None = None,
    party_state_weight: float = 1.0,
    endorser_weight: float = 1.0,
    network_weight: float = 1.0,
    network_top_k: int = 5,
) -> float:
    """Blend the available prior groups into a yea probability for an unknown official.

    Groups with no members (or zero weight) are skipped; the remaining group
    means are combined as a weight-normalized average in logit space. With no
    usable group the prior is neutral (0.5).
    """
    contributions: list[tuple[float, float]] = []
    party_state = _party_state_logit(target, references)
    if party_state is not None and party_state_weight > 0.0:
        contributions.append((party_state_weight, party_state))
    endorser = _endorser_logit(target, references)
    if endorser is not None and endorser_weight > 0.0:
        contributions.append((endorser_weight, endorser))
    network = _network_logit(target, references, embeddings, network_top_k)
    if network is not None and network_weight > 0.0:
        contributions.append((network_weight, network))

    if not contributions:
        return 0.5
    weight_total = sum(weight for weight, _ in contributions)
    prior_logit = sum(weight * logit for weight, logit in contributions) / weight_total
    return _sigmoid(prior_logit)


def posterior_after_votes(
    prior_logit: float,
    *,
    yea_count: int,
    total: int,
    prior_strength: float,
) -> float:
    """Shrink the prior toward the observed vote rate as the official accumulates votes.

    ``prior_strength`` is the pseudo-count of prior evidence; once ``total``
    real votes exceed it, the observed rate dominates.
    """
    if prior_strength < 0.0:
        raise ValueError("prior_strength must be non-negative")
    if total < 0 or yea_count < 0 or yea_count > total:
        raise ValueError("vote counts must satisfy 0 <= yea_count <= total")
    if total == 0:
        return _sigmoid(prior_logit)
    observed = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, yea_count / total))
    observed_logit = math.log(observed / (1.0 - observed))
    posterior_logit = (prior_strength * prior_logit + total * observed_logit) / (
        prior_strength + total
    )
    return _sigmoid(posterior_logit)
