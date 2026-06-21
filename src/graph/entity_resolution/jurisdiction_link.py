"""Resolve LOCUS jurisdictions to canonical jurisdiction entities (deliverable #2).

LOCUS names a jurisdiction by ``(state, place-slug, level)`` — e.g.
``("ak", "kingcove", "city")`` — but its slugs drop internal spacing
(``kingcove`` for "King Cove"), so they do *not* equal the repo's canonical
codes (``us-ak-city-king_cove``) on the nose. This module bridges the two:

* :func:`fold_place` collapses a place name to a separator-free key
  (``"King Cove" -> "kingcove"``, ``"king_cove" -> "kingcove"``), so the LOCUS
  slug and a canonical display name land on the same key.
* :class:`JurisdictionUniverse` indexes every *known* canonical jurisdiction by
  ``(state, level, folded place)`` and, crucially, records when two **distinct**
  canonical places collapse to one key within a state+level — the municipal-name
  collision hazard LOCUS itself flags. Colliding keys are held as ambiguous and
  never auto-merged (zero false merges).
* :func:`resolve_jurisdiction` returns a :class:`JurisdictionMatch` carrying the
  matched canonical code (or ``None``), the match method, and whether the key was
  ambiguous — so precision/recall can be measured and disputed matches persist.

When a LOCUS jurisdiction matches no known canonical entity, it is *minted* as a
new canonical jurisdiction (LOCUS is itself authoritative for the place's
existence) and reported as ``method="minted"`` — coverage, not a failed join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from src.graph.jurisdictions import Jurisdiction, JurisdictionLevel

MatchMethod = Literal["exact_code", "folded", "minted", "ambiguous"]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold_place(name: str) -> str:
    """Collapse a place name/slug to a separator-free, accent-free key.

    ``"King Cove" -> "kingcove"``; ``"king_cove" -> "kingcove"``;
    ``"St. Paul" -> "stpaul"``. This is the join key that makes a LOCUS slug and a
    canonical display name comparable despite spacing/punctuation differences.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = _NON_ALNUM.sub("", without_accents.lower())
    if not folded:
        raise ValueError("place name must not be blank after folding")
    return folded


def _fold_code_place(code: str) -> tuple[str, str, str] | None:
    """Split a canonical jurisdiction code into ``(state, level, folded place)``.

    ``us-ca-city-los_angeles`` -> ``("ca", "city", "losangeles")``.
    Returns ``None`` for codes without a place tail (federal / bare state).
    """
    parts = code.split("-")
    if len(parts) < 4 or parts[0] != "us":
        return None
    state = parts[1]
    level_token = parts[2]
    place = "-".join(parts[3:])
    level = {"city": "city", "county": "county", "sd": "special_district"}.get(level_token)
    if level is None:
        return None
    return state, level, fold_place(place)


@dataclass
class JurisdictionUniverse:
    """An index of known canonical jurisdictions for folded-key resolution."""

    # (state, level, folded place) -> set of canonical codes sharing that key.
    _by_key: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)
    _codes: set[str] = field(default_factory=set)

    def add(self, jurisdiction: Jurisdiction) -> None:
        """Register one known canonical jurisdiction."""
        self._codes.add(jurisdiction.code)
        folded = _fold_code_place(jurisdiction.code)
        if folded is not None:
            self._by_key.setdefault(folded, set()).add(jurisdiction.code)

    def add_code(self, code: str) -> None:
        """Register a canonical jurisdiction by its code string."""
        self._codes.add(code)
        folded = _fold_code_place(code)
        if folded is not None:
            self._by_key.setdefault(folded, set()).add(code)

    @property
    def size(self) -> int:
        return len(self._codes)

    @property
    def collision_keys(self) -> list[tuple[str, str, str]]:
        """Folded keys that two or more *distinct* canonical places share."""
        return sorted(key for key, codes in self._by_key.items() if len(codes) > 1)

    def has_code(self, code: str) -> bool:
        """True when ``code`` is a registered canonical jurisdiction."""
        return code in self._codes

    def candidates(self, state: str, level: str, folded: str) -> set[str]:
        return set(self._by_key.get((state, level, folded), set()))


@dataclass(frozen=True)
class JurisdictionMatch:
    """The outcome of resolving one LOCUS jurisdiction."""

    locus_code: str
    canonical_code: str | None
    method: MatchMethod
    ambiguous: bool
    candidates: tuple[str, ...] = ()

    @property
    def is_linked(self) -> bool:
        """True when this resolves to an *existing* canonical entity (a real join)."""
        return self.method in ("exact_code", "folded")


def resolve_jurisdiction(
    jurisdiction: Jurisdiction,
    universe: JurisdictionUniverse,
) -> JurisdictionMatch:
    """Resolve one LOCUS jurisdiction against the known canonical universe.

    Priority: exact canonical-code hit > unique folded-key hit > ambiguous
    (collision, held apart) > minted (new canonical jurisdiction, LOCUS-attested).
    """
    code = jurisdiction.code
    if universe.has_code(code):
        return JurisdictionMatch(code, code, "exact_code", ambiguous=False)
    folded = _fold_code_place(code)
    if folded is None:
        return JurisdictionMatch(code, code, "minted", ambiguous=False)
    state, level, place = folded
    candidates = universe.candidates(state, level, place)
    # Drop the jurisdiction's own code from the candidate set (handled above).
    candidates.discard(code)
    if len(candidates) == 1:
        (match,) = tuple(candidates)
        return JurisdictionMatch(code, match, "folded", ambiguous=False)
    if len(candidates) > 1:
        # Genuine collision: distinct canonical places fold to one key. Hold apart.
        return JurisdictionMatch(
            code, None, "ambiguous", ambiguous=True, candidates=tuple(sorted(candidates))
        )
    return JurisdictionMatch(code, code, "minted", ambiguous=False)


def jurisdiction_from_locus(*, state: str, place: str, level: JurisdictionLevel) -> Jurisdiction:
    """Build the canonical :class:`Jurisdiction` for a LOCUS ``(state, place, level)``."""
    if level == "county":
        return Jurisdiction.county(state, place)
    if level == "special_district":
        return Jurisdiction.special_district(state, place)
    return Jurisdiction.city(state, place)
