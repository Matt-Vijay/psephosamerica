"""Tests for the chain composer."""

from __future__ import annotations

import numpy as np

from src.prediction.chain_composer import (
    ChainLink,
    attribution_table,
    chamber_pivot,
    compose_chain,
    passage_probability,
)
from src.prediction.vote_count_pmf import poisson_binomial_pmf


def _link(name: str, p: float) -> ChainLink:
    return ChainLink(name=name, probability=p, source="test", detail="")


def test_compose_multiplies_links() -> None:
    price = compose_chain([_link("a", 0.5), _link("b", 0.4), _link("c", 1.0)])
    assert abs(price.probability - 0.2) < 1e-12
    assert len(price.links) == 3


def test_compose_clamps_out_of_range_links() -> None:
    price = compose_chain([_link("a", 1.7), _link("b", -0.2)])
    assert price.probability == 0.0


def test_passage_probability_is_tail_mass() -> None:
    pmf = poisson_binomial_pmf(np.full(10, 0.5))
    assert abs(passage_probability(pmf, 0) - 1.0) < 1e-12
    assert abs(passage_probability(pmf, 11) - 0.0) < 1e-12
    assert abs(passage_probability(pmf, 6) - float(pmf[6:].sum())) < 1e-12


def test_chamber_pivot() -> None:
    assert chamber_pivot(435) == 218
    assert chamber_pivot(100) == 51


def test_attribution_identifies_binding_link() -> None:
    price = compose_chain([_link("cheap", 0.9), _link("binding", 0.05), _link("free", 1.0)])
    table = attribution_table(price)
    by_name = {row["name"]: row for row in table}
    assert by_name["binding"]["log_cost_share"] > 0.9
    shares = sum(float(row["log_cost_share"]) for row in table)
    assert abs(shares - 1.0) < 1e-9
