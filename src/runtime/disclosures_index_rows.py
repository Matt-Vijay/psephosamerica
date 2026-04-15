"""Map stored source_artifact rows to live disclosure index rows.

Fetches the live index once per (chamber, filing_year) group and resolves
each artifact by source_record_id == doc_id.  No member matching is performed
here; that belongs to the normalization pipeline.

Public API
----------
group_artifacts_by_chamber_year(artifacts)
    -> dict[tuple[str, int], list[dict]]

fetch_index_rows_for_artifacts(artifacts, *, client=None)
    -> list[ArtifactIndexMatch]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from src.parse.disclosures.house_index import HouseIndexRow
from src.parse.disclosures.index_lookup import fetch_disclosure_rows_by_doc_id
from src.parse.disclosures.senate_index import SenateIndexRow

IndexRow = Union[HouseIndexRow, SenateIndexRow]


@dataclass(frozen=True)
class ArtifactIndexMatch:
    """Pairs a stored artifact row with its resolved live index row.

    index_row is None when the artifact's source_record_id is absent from
    the live index for that chamber and year.
    """

    artifact: dict[str, Any]
    index_row: Optional[IndexRow]


def group_artifacts_by_chamber_year(
    artifacts: list[dict[str, Any]],
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Group artifact rows by (chamber, filing_year).

    Rows with a None chamber or filing_year are excluded; they cannot be
    resolved against a year-scoped index.
    """
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in artifacts:
        chamber = row.get("chamber")
        year = row.get("filing_year")
        if chamber is None or year is None:
            continue
        key = (chamber, int(year))
        groups.setdefault(key, []).append(row)
    return groups


def fetch_index_rows_for_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    client: Optional[Any] = None,
) -> list[ArtifactIndexMatch]:
    """Return index rows matched to stored artifact rows by source_record_id.

    Fetches the live index exactly once per distinct (chamber, filing_year)
    pair.  Artifacts with a missing or unresolvable source_record_id get
    index_row=None.
    """
    groups = group_artifacts_by_chamber_year(artifacts)

    # One live fetch per (chamber, year); filing_kind=None fetches all House kinds.
    lookups: dict[tuple[str, int], dict[str, IndexRow]] = {}
    for chamber, year in groups:
        lookups[(chamber, year)] = fetch_disclosure_rows_by_doc_id(
            chamber, year, client=client
        )

    matches: list[ArtifactIndexMatch] = []
    for row in artifacts:
        chamber = row.get("chamber")
        year = row.get("filing_year")
        if chamber is None or year is None:
            matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
            continue
        lookup = lookups.get((chamber, int(year)), {})
        doc_id = row.get("source_record_id") or ""
        matches.append(ArtifactIndexMatch(artifact=row, index_row=lookup.get(doc_id)))
    return matches
