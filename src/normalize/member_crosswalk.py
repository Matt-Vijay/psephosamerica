"""Deterministic member ID crosswalk helpers.

Resolves and joins bioguide_id, lis_member_id, and fec_candidate_id using
in-memory row-shaped dicts or typed records.  No file or network I/O.

Public API
----------
CrosswalkRecord       – typed record for one crosswalk row
CrosswalkIndex        – indexed lookup structure built from a list of records
build_index           – construct a CrosswalkIndex from raw rows
lookup_by_bioguide    – exact lookup, returns record or AmbiguousMatch/NotFound
lookup_by_lis         – exact lookup by lis_member_id
lookup_by_fec         – exact lookup by fec_candidate_id
validate_one_to_one   – check that all non-None ID columns map 1-to-1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Union

# ---------------------------------------------------------------------------
# Typed record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrosswalkRecord:
    """One row in the member crosswalk dataset.

    All three ID fields are optional at the source level; downstream consumers
    must decide whether a missing ID is an error for their context.
    """

    bioguide_id: str  # canonical; must always be present
    lis_member_id: Optional[str] = None
    fec_candidate_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.bioguide_id:
            raise ValueError("bioguide_id must be a non-empty string")


# ---------------------------------------------------------------------------
# Lookup result types
# ---------------------------------------------------------------------------

LookupField = Literal["bioguide_id", "lis_member_id", "fec_candidate_id"]


@dataclass(frozen=True)
class NotFound:
    field: LookupField
    value: str


@dataclass(frozen=True)
class AmbiguousMatch:
    """Returned when more than one record maps to the same non-None ID value.

    This should not happen in a clean dataset, but we surface it explicitly
    rather than silently returning an arbitrary record.
    """

    field: LookupField
    value: str
    matches: List[CrosswalkRecord]


LookupResult = Union[CrosswalkRecord, NotFound, AmbiguousMatch]


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def _index_by(
    records: Sequence[CrosswalkRecord], field: LookupField
) -> Dict[str, List[CrosswalkRecord]]:
    """Build a multi-map from field value -> list of records."""
    idx: Dict[str, List[CrosswalkRecord]] = {}
    for rec in records:
        value: Optional[str] = getattr(rec, field)
        if value is not None:
            idx.setdefault(value, []).append(rec)
    return idx


@dataclass
class CrosswalkIndex:
    """Pre-built lookup indexes over a crosswalk dataset."""

    _by_bioguide: Dict[str, List[CrosswalkRecord]]
    _by_lis: Dict[str, List[CrosswalkRecord]]
    _by_fec: Dict[str, List[CrosswalkRecord]]
    _records: List[CrosswalkRecord]

    # ---- lookup methods ----

    def by_bioguide(self, bioguide_id: str) -> LookupResult:
        return _resolve("bioguide_id", bioguide_id, self._by_bioguide)

    def by_lis(self, lis_member_id: str) -> LookupResult:
        return _resolve("lis_member_id", lis_member_id, self._by_lis)

    def by_fec(self, fec_candidate_id: str) -> LookupResult:
        return _resolve("fec_candidate_id", fec_candidate_id, self._by_fec)

    @property
    def records(self) -> List[CrosswalkRecord]:
        return list(self._records)


def _resolve(
    field: LookupField,
    value: str,
    idx: Dict[str, List[CrosswalkRecord]],
) -> LookupResult:
    hits = idx.get(value)
    if not hits:
        return NotFound(field=field, value=value)
    if len(hits) > 1:
        return AmbiguousMatch(field=field, value=value, matches=list(hits))
    return hits[0]


# ---------------------------------------------------------------------------
# Public constructors
# ---------------------------------------------------------------------------


def build_index(rows: Sequence[Union[CrosswalkRecord, dict]]) -> CrosswalkIndex:
    """Build a CrosswalkIndex from a sequence of CrosswalkRecords or raw dicts.

    Raw dicts are coerced to CrosswalkRecord using the same field names.
    """
    records: List[CrosswalkRecord] = []
    for row in rows:
        if isinstance(row, CrosswalkRecord):
            records.append(row)
        else:
            records.append(
                CrosswalkRecord(
                    bioguide_id=row["bioguide_id"],
                    lis_member_id=row.get("lis_member_id"),
                    fec_candidate_id=row.get("fec_candidate_id"),
                )
            )
    return CrosswalkIndex(
        _by_bioguide=_index_by(records, "bioguide_id"),
        _by_lis=_index_by(records, "lis_member_id"),
        _by_fec=_index_by(records, "fec_candidate_id"),
        _records=records,
    )


# ---------------------------------------------------------------------------
# Module-level convenience wrappers
# ---------------------------------------------------------------------------


def lookup_by_bioguide(index: CrosswalkIndex, bioguide_id: str) -> LookupResult:
    return index.by_bioguide(bioguide_id)


def lookup_by_lis(index: CrosswalkIndex, lis_member_id: str) -> LookupResult:
    return index.by_lis(lis_member_id)


def lookup_by_fec(index: CrosswalkIndex, fec_candidate_id: str) -> LookupResult:
    return index.by_fec(fec_candidate_id)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingConflict:
    """One detected one-to-many or many-to-one mapping violation."""

    field: LookupField
    value: str
    bioguide_ids: List[str]


def validate_one_to_one(records: Sequence[CrosswalkRecord]) -> List[MappingConflict]:
    """Return a list of conflicts where a non-None ID maps to multiple bioguide_ids.

    An empty list means every non-None secondary ID maps to exactly one
    bioguide_id (true one-to-one for lis_member_id and fec_candidate_id).

    Duplicate bioguide_id rows are also detected: if the same bioguide_id
    appears more than once, it is flagged under the 'bioguide_id' field.
    """
    conflicts: List[MappingConflict] = []

    for field in ("bioguide_id", "lis_member_id", "fec_candidate_id"):
        idx = _index_by(records, field)  # type: ignore[arg-type]
        for value, hits in idx.items():
            bioguide_ids = [r.bioguide_id for r in hits]
            # For non-bioguide fields, "many bioguides -> same secondary ID" is
            # the collision.  For bioguide itself, duplicate rows are the issue.
            unique_bioguides = list(dict.fromkeys(bioguide_ids))
            if len(unique_bioguides) > 1 or (
                field == "bioguide_id" and len(hits) > 1
            ):
                conflicts.append(
                    MappingConflict(
                        field=field,  # type: ignore[arg-type]
                        value=value,
                        bioguide_ids=unique_bioguides,
                    )
                )

    return conflicts
