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
from typing import Any, cast

from src.parse.disclosures.house_index import HouseIndexRow
from src.parse.disclosures.index_lookup import (
    Chamber as DisclosureChamber,
    fetch_disclosure_rows_by_doc_id,
)
from src.parse.disclosures.senate_index import SenateIndexRow

IndexRow = HouseIndexRow | SenateIndexRow


@dataclass(frozen=True)
class ArtifactIndexMatch:
    """Pairs a stored artifact row with its resolved live index row.

    index_row is None when the artifact's source_record_id is absent from
    the live index for that chamber and year.
    """

    artifact: dict[str, Any]
    index_row: IndexRow | None


def group_artifacts_by_chamber_year(
    artifacts: list[dict[str, Any]],
) -> dict[tuple[DisclosureChamber, int], list[dict[str, Any]]]:
    """Group artifact rows by (chamber, filing_year).

    Rows with a None chamber or filing_year are excluded; they cannot be
    resolved against a year-scoped index.
    """
    groups: dict[tuple[DisclosureChamber, int], list[dict[str, Any]]] = {}
    for row in artifacts:
        chamber_value = row.get("chamber")
        year_value = row.get("filing_year")
        if chamber_value not in ("house", "senate") or year_value is None:
            continue
        year = filing_year_or_none(year_value)
        if year is None:
            continue
        chamber: DisclosureChamber = chamber_value
        key = (chamber, year)
        groups.setdefault(key, []).append(row)
    return groups


def fetch_index_rows_for_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    client: Any | None = None,
) -> list[ArtifactIndexMatch]:
    """Return index rows matched to stored artifact rows by source_record_id.

    Fetches the live index exactly once per distinct (chamber, filing_year)
    pair.  Artifacts with a missing or unresolvable source_record_id get
    index_row=None.
    """
    groups = group_artifacts_by_chamber_year(artifacts)

    # One live fetch per (chamber, year); filing_kind=None fetches all House kinds.
    lookups: dict[tuple[DisclosureChamber, int], dict[str, IndexRow]] = {}
    for chamber, year in groups:
        lookups[(chamber, year)] = cast(
            dict[str, IndexRow],
            fetch_disclosure_rows_by_doc_id(chamber, year, client=client),
        )

    matches: list[ArtifactIndexMatch] = []
    for row in artifacts:
        chamber_value = row.get("chamber")
        year_value = row.get("filing_year")
        if chamber_value not in ("house", "senate") or year_value is None:
            matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
            continue
        artifact_chamber: DisclosureChamber = chamber_value
        artifact_year = filing_year_or_none(year_value)
        if artifact_year is None:
            matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
            continue
        lookup = lookups.get((artifact_chamber, artifact_year), {})
        source_record_id = row.get("source_record_id")
        doc_id = "" if source_record_id is None else str(source_record_id)
        matches.append(ArtifactIndexMatch(artifact=row, index_row=lookup.get(doc_id)))
    return matches


def filing_year_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
