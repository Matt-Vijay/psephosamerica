from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.normalize.taxonomy_runtime import TaxonomyRuntime

ContributionSectorResolver = Callable[[dict[str, Any]], str | None]

_TEXT_KEYS = (
    "donor_name",
    "donor_employer",
    "donor_occupation",
    "memo",
    "fec_committee_name",
)
_STOP_TERMS = {
    "and",
    "the",
    "services",
    "industry",
    "international",
}
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def build_contribution_sector_resolver(
    taxonomy: TaxonomyRuntime,
) -> ContributionSectorResolver:
    """Build a conservative FEC contribution row → sector resolver.

    This intentionally only uses locked taxonomy labels/aliases and only
    resolves when the row text maps to exactly one sector. Ambiguous rows stay
    unresolved so they do not become false precision in prediction features.
    """
    terms_by_sector = _terms_by_sector(taxonomy)

    def resolve(row: dict[str, Any]) -> str | None:
        haystack = _row_text(row)
        if not haystack:
            return None
        matches = {
            sector_id
            for sector_id, terms in terms_by_sector.items()
            if any(_contains_term(haystack, term) for term in terms)
        }
        if len(matches) != 1:
            return None
        return next(iter(matches))

    return resolve


def _terms_by_sector(taxonomy: TaxonomyRuntime) -> dict[str, tuple[str, ...]]:
    terms_by_sector: dict[str, tuple[str, ...]] = {}
    for sector in taxonomy.sectors:
        raw_terms = [sector.sector_id, sector.label, *sector.aliases]
        terms = sorted(
            {
                term
                for raw in raw_terms
                for term in _candidate_terms(raw)
                if term and term not in _STOP_TERMS
            }
        )
        terms_by_sector[sector.sector_id] = tuple(terms)
    return terms_by_sector


def _candidate_terms(raw: str) -> list[str]:
    normalized = _normalize_text(raw)
    if not normalized:
        return []
    terms = [normalized]
    if " " in normalized:
        terms.extend(part for part in normalized.split() if len(part) >= 4)
    return terms


def _row_text(row: dict[str, Any]) -> str:
    values = [str(row.get(key) or "") for key in _TEXT_KEYS]
    return f" {_normalize_text(' '.join(values))} "


def _contains_term(haystack: str, term: str) -> bool:
    return f" {term} " in haystack


def _normalize_text(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()
