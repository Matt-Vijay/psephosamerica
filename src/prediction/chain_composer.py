"""Compose the legislative chain: P(becomes law by deadline) with per-link attribution (v7 #3).

Markets price the compound event -- committee -> floor -> House passage ->
Senate cloture -> Senate passage -> signature, by a deadline. Each link is
priced by its own head and the product is reported WITH the per-link
attribution, so a consumer can see exactly which link moves the price:

* origin-chamber floor arrival: the stage-hazard head (``stage_hazard``);
* chamber passage / cloture: tail mass of the correlated yes-count PMF at the
  chamber pivot (median; 60th for cloture) (``vote_count_pmf``);
* cross-chamber transition: an empirical, data-grounded rate measured from the
  ingested corpora (fraction of origin-passing bills later seen on the other
  chamber's floor), supplied by the caller with its citation;
* signature: a cited historical prior (vetoes are rare in the modern sample).

Every link carries its source and detail string -- no uncited numbers. The
composer itself is pure arithmetic over link probabilities; heads are fit
elsewhere and passed in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ChainLink:
    """One priced link of the compound event."""

    name: str
    probability: float
    source: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "probability": self.probability,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ChainPrice:
    """The composed compound-event price plus its full attribution."""

    probability: float
    links: tuple[ChainLink, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "probability": self.probability,
            "links": [link.as_dict() for link in self.links],
        }


def compose_chain(links: list[ChainLink]) -> ChainPrice:
    """Multiply link probabilities (each conditioned on the previous link holding)."""
    p = 1.0
    for link in links:
        p *= min(1.0, max(0.0, link.probability))
    return ChainPrice(probability=p, links=tuple(links))


def passage_probability(count_pmf: Array, pivot: int) -> float:
    """P(yes count >= pivot) under a count PMF -- the chamber-pivot link."""
    if pivot <= 0:
        return 1.0
    if pivot >= count_pmf.shape[0]:
        return 0.0
    return float(count_pmf[pivot:].sum())


def chamber_pivot(n_voting: int) -> int:
    """Simple-majority pivot for a chamber of ``n_voting`` members."""
    return n_voting // 2 + 1


def attribution_table(price: ChainPrice) -> list[dict[str, object]]:
    """Per-link share of the compound log-price (which link costs the most)."""
    logs = [float(-np.log(max(link.probability, 1e-12))) for link in price.links]
    total = sum(logs) or 1.0
    return [
        {
            "name": link.name,
            "probability": link.probability,
            "log_cost_share": logs[i] / total,
            "source": link.source,
        }
        for i, link in enumerate(price.links)
    ]
