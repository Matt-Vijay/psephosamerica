"""Tests for the thin-record prior.

OVERALL_GOAL.md's scaling key: a brand-new official with no votes is predicted
via a weighted blend of (their party in their state) + (their endorsers'
cluster) + (similar officials by network position), and the prior weight
shrinks as they accumulate their own votes.
"""

from __future__ import annotations

import math

import pytest

from src.prediction.nn.structural_embeddings import StructuralEdge, compute_structural_embeddings
from src.prediction.thin_record_prior import (
    StanceReference,
    ThinRecordTarget,
    posterior_after_votes,
    thin_record_prior,
)


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))


def test_prior_uses_party_in_state_group() -> None:
    references = [
        StanceReference(
            official_id="a", stance_logit=_logit(0.9), party="D", state="CA", endorsers=frozenset()
        ),
        StanceReference(
            official_id="b", stance_logit=_logit(0.9), party="D", state="CA", endorsers=frozenset()
        ),
        # A different party/state that should be ignored when only party-state weight is on.
        StanceReference(
            official_id="c", stance_logit=_logit(0.1), party="R", state="TX", endorsers=frozenset()
        ),
    ]
    target = ThinRecordTarget(party="D", state="CA", endorsers=frozenset())
    prior = thin_record_prior(
        target,
        references,
        party_state_weight=1.0,
        endorser_weight=0.0,
        network_weight=0.0,
    )
    assert prior == pytest.approx(0.9, abs=0.02)


def test_prior_uses_endorser_cluster() -> None:
    references = [
        StanceReference(
            official_id="a",
            stance_logit=_logit(0.8),
            party="D",
            state="NY",
            endorsers=frozenset({"sierra_club"}),
        ),
        StanceReference(
            official_id="b",
            stance_logit=_logit(0.2),
            party="R",
            state="TX",
            endorsers=frozenset({"chamber"}),
        ),
    ]
    target = ThinRecordTarget(party="I", state="VT", endorsers=frozenset({"sierra_club"}))
    prior = thin_record_prior(
        target,
        references,
        party_state_weight=0.0,
        endorser_weight=1.0,
        network_weight=0.0,
    )
    # Only the Sierra-Club-endorsed reference shares an endorser.
    assert prior == pytest.approx(0.8, abs=0.02)


def test_prior_uses_network_similarity() -> None:
    # person:1 and person:2 are network twins (both endorsed by org:a).
    edges = [
        StructuralEdge(relation="endorses", source="org:a", target="person:1"),
        StructuralEdge(relation="endorses", source="org:a", target="person:2"),
        StructuralEdge(relation="endorses", source="org:b", target="person:3"),
    ]
    embeddings = compute_structural_embeddings(edges, dim=16, num_layers=2, seed=0)
    references = [
        StanceReference(
            official_id="person:2",
            stance_logit=_logit(0.85),
            party="D",
            state="WA",
            endorsers=frozenset(),
            structural_key="person:2",
        ),
        StanceReference(
            official_id="person:3",
            stance_logit=_logit(0.15),
            party="R",
            state="ID",
            endorsers=frozenset(),
            structural_key="person:3",
        ),
    ]
    target = ThinRecordTarget(
        party="D", state="WA", endorsers=frozenset(), structural_key="person:1"
    )
    prior = thin_record_prior(
        target,
        references,
        embeddings=embeddings,
        party_state_weight=0.0,
        endorser_weight=0.0,
        network_weight=1.0,
        network_top_k=1,
    )
    # person:1's network twin is person:2 (yea-leaning), not person:3.
    assert prior > 0.6


def test_prior_is_neutral_without_references() -> None:
    target = ThinRecordTarget(party="D", state="CA", endorsers=frozenset())
    assert thin_record_prior(target, []) == pytest.approx(0.5)


def test_posterior_shrinks_prior_toward_observed_votes() -> None:
    prior_logit = _logit(0.9)  # strong yea prior
    # With many observed nay-leaning votes, the posterior moves toward observed.
    sparse = posterior_after_votes(prior_logit, yea_count=1, total=4, prior_strength=4.0)
    dense = posterior_after_votes(prior_logit, yea_count=25, total=100, prior_strength=4.0)
    assert dense < sparse  # more data -> prior matters less -> closer to observed 0.25
    assert dense == pytest.approx(0.25, abs=0.05)


def test_posterior_with_no_votes_returns_prior() -> None:
    prior_logit = _logit(0.7)
    assert posterior_after_votes(
        prior_logit, yea_count=0, total=0, prior_strength=4.0
    ) == pytest.approx(0.7, abs=1e-9)
