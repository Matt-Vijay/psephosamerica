"""Disclosure index-row provider helpers.

Exposes two explicit provider paths:
  bundle_index_matches  — resolves artifacts against a pre-loaded bundle lookup
  live_index_matches    — resolves artifacts via live network fetch

Both return ``IndexMatchResult`` (= ``list[ArtifactIndexMatch]``) with the same
row-shaped contract as ``fetch_index_rows_for_artifacts``.  No parsing is
performed here.

Shared output contract
----------------------
``IndexMatchResult`` is the canonical alias for the shared return type.
Both paths guarantee:
  - One ``ArtifactIndexMatch`` per input artifact, in the same order.
  - ``index_row`` is None when the artifact cannot be resolved.
  - ``artifact`` is the identical dict object that was passed in.

Intended use
------------
Callers choose the path explicitly:
  - Use bundle_index_matches for deterministic local oracle runs.
  - Use live_index_matches for production recompute with live source data.
"""

from __future__ import annotations

from typing import Any

from src.runtime.disclosures_bundle import DisclosuresLookup
from src.runtime.disclosures_index_rows import (
    ArtifactIndexMatch,
    fetch_index_rows_for_artifacts,
    filing_year_or_none,
)

#: Canonical return type for both bundle and live index provider paths.
#: One ArtifactIndexMatch per input artifact; order is preserved.
IndexMatchResult = list[ArtifactIndexMatch]


def bundle_index_matches(
    artifacts: list[dict[str, Any]],
    bundle: DisclosuresLookup,
) -> IndexMatchResult:
    """Resolve artifacts against a pre-loaded bundle lookup.

    For each artifact, looks up (chamber, filing_year) in bundle then
    resolves by source_record_id.  Artifacts whose chamber or filing_year
    is None, or whose doc_id is absent from the bundle, receive index_row=None.

    No network calls.  Deterministic given the same bundle and artifact list.
    """
    matches: list[ArtifactIndexMatch] = []
    for row in artifacts:
        chamber = row.get("chamber")
        year = row.get("filing_year")
        if chamber is None or year is None:
            matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
            continue
        filing_year = filing_year_or_none(year)
        if filing_year is None:
            matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
            continue
        lookup = bundle.get((chamber, filing_year), {})
        doc_id = row.get("source_record_id") or ""
        matches.append(ArtifactIndexMatch(artifact=row, index_row=lookup.get(doc_id)))
    return matches


def live_index_matches(
    artifacts: list[dict[str, Any]],
    *,
    client: Any | None = None,
) -> IndexMatchResult:
    """Resolve artifacts via live index fetch.

    Delegates to fetch_index_rows_for_artifacts.  One network fetch per
    distinct (chamber, filing_year) pair in artifacts.
    """
    return fetch_index_rows_for_artifacts(artifacts, client=client)
