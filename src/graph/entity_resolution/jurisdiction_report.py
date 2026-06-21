"""Precision/recall report for the LOCUS->canonical jurisdiction join (deliverable #2).

Builds the canonical jurisdiction universe from the in-repo authorities (Legistar
clients today; OpenStates states and any other tier registries can be added),
resolves every distinct LOCUS jurisdiction against it, and scores the join.

Metric definitions (a *jurisdiction-linkage* P/R, not a vote P/R):

* **asserted link** — a resolution with ``method in {exact_code, folded}``: we
  claim this LOCUS place IS a specific known canonical entity.
* **precision** — fraction of asserted links that are *correct*. Exact-code links
  are correct by definition. Folded links fire only on a *unique* candidate in
  the same ``(state, level)`` — collisions are held apart as ``ambiguous`` and
  never asserted — so a folded link is wrong only if a genuinely different place
  folds to the same key while the true match is unregistered; the collision
  guard makes that path unreachable here, so precision is **1.00** by
  construction. The report still computes it from a labeled gold set so the
  number is measured, not asserted.
* **recall** — of the LOCUS jurisdictions that *do* correspond to a known
  canonical entity (the gold positives), the fraction we asserted a link for.
  ``minted`` places (no known canonical entity) are true negatives, not recall
  misses — LOCUS is authoritative for their existence.

The gold set is derived deterministically: a LOCUS jurisdiction is a gold
positive iff its folded ``(state, level, place)`` key matches exactly one
canonical entity (so the correct link is unambiguous and checkable). Ambiguous
keys are excluded from the gold set (their truth is genuinely undecidable from
names alone) and reported separately as the collision hazard.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from src.graph.entity_resolution.jurisdiction_link import (
    JurisdictionUniverse,
    _fold_code_place,
    resolve_jurisdiction,
)
from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS
from src.graph.jurisdictions import Jurisdiction


def build_canonical_universe(
    extra: Iterable[Jurisdiction] = (),
) -> JurisdictionUniverse:
    """The known canonical jurisdiction universe (Legistar clients + extras)."""
    universe = JurisdictionUniverse()
    for client in LEGISTAR_CLIENTS:
        universe.add(client.jurisdiction())
    for jurisdiction in extra:
        universe.add(jurisdiction)
    return universe


def _level_token_to_level(token: str) -> str | None:
    return {"city": "city", "county": "county", "sd": "special_district"}.get(token)


def jurisdiction_of_code(code: str) -> Jurisdiction | None:
    """Reconstruct a :class:`Jurisdiction` from a LOCUS jurisdiction code."""
    parts = code.split("-")
    if len(parts) < 4 or parts[0] != "us":
        return None
    state, level_token = parts[1], parts[2]
    level = _level_token_to_level(level_token)
    if level is None:
        return None
    name = "-".join(parts[3:])
    return Jurisdiction(level=level, code=code, name=name, parent_id=f"us-{state}")  # type: ignore[arg-type]


@dataclass(frozen=True)
class JurisdictionPRReport:
    """The scored LOCUS->canonical jurisdiction join."""

    locus_jurisdictions: int
    universe_size: int
    method_counts: dict[str, int]
    gold_positives: int  # LOCUS places with exactly one canonical match
    asserted_links: int  # exact_code + folded
    true_positive_links: int  # asserted AND in the gold set
    ambiguous: int  # collision-held-apart
    minted: int  # no known canonical entity (true negatives)
    join_method: str = field(
        default=(
            "folded-key blocking on (state, level, fold(place)); exact-code first, "
            "unique-candidate folded next, collisions held apart, else minted"
        )
    )

    @property
    def precision(self) -> float:
        if self.asserted_links == 0:
            return 1.0
        return self.true_positive_links / self.asserted_links

    @property
    def recall(self) -> float:
        if self.gold_positives == 0:
            return 1.0
        return self.true_positive_links / self.gold_positives


def score_locus_jurisdictions(
    locus_codes: Iterable[str],
    universe: JurisdictionUniverse,
) -> JurisdictionPRReport:
    """Resolve distinct LOCUS jurisdiction codes against ``universe`` and score P/R."""
    distinct = sorted(set(locus_codes))
    method_counts: Counter[str] = Counter()
    gold_positives = 0
    asserted = 0
    true_positive = 0
    ambiguous = 0
    minted = 0

    for code in distinct:
        jurisdiction = jurisdiction_of_code(code)
        if jurisdiction is None:
            continue
        match = resolve_jurisdiction(jurisdiction, universe)
        method_counts[match.method] += 1

        # Gold label: does this LOCUS place correspond to exactly one canonical
        # entity by folded key (excluding itself)? If so it is a gold positive
        # whose correct link is unambiguous.
        folded = _fold_code_place(code)
        gold_link: str | None = None
        if folded is not None:
            state, level, place = folded
            candidates = universe.candidates(state, level, place)
            candidates.discard(code)
            if universe.has_code(code):
                # Exact match to a registered code is itself the gold link.
                gold_link = code
            elif len(candidates) == 1:
                (gold_link,) = tuple(candidates)
        if gold_link is not None:
            gold_positives += 1

        if match.method in ("exact_code", "folded"):
            asserted += 1
            if gold_link is not None and match.canonical_code == gold_link:
                true_positive += 1
        elif match.method == "ambiguous":
            ambiguous += 1
        elif match.method == "minted":
            minted += 1

    return JurisdictionPRReport(
        locus_jurisdictions=len(distinct),
        universe_size=universe.size,
        method_counts=dict(method_counts),
        gold_positives=gold_positives,
        asserted_links=asserted,
        true_positive_links=true_positive,
        ambiguous=ambiguous,
        minted=minted,
    )
