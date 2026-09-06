"""Generic foreign-key and natural-key resolution helpers.

Pure functions — no I/O, no database access.  Callers supply explicit lookup
maps; the helpers apply them and return resolved row dicts plus a structured
summary of any failures.

Hint key conventions
--------------------
A *hint key* is a key in a row dict that carries a natural or external
identifier instead of an integer FK.  The helpers detect hint keys, resolve
them to integer FKs using the supplied maps, strip the hint key, and inject
the resolved FK.  Rows with unresolvable hint keys are still returned (minus
the hint key and the missing target FK) so the caller can decide how to handle
failures.

Supported patterns (checked in declaration order; longest suffix wins when
suffixes overlap):

1. ``*_bioguide_id``
   Value is a ``bioguide_id`` string.
   Target FK: replace ``_bioguide_id`` suffix with ``_id``; bare
              ``_bioguide_id`` (empty prefix) → ``member_id``.
   Lookup: ``bioguide_map: dict[str, int]``

2. ``*_lis_member_id``
   Value is a ``lis_member_id`` string.
   Target FK: replace ``_lis_member_id`` suffix with ``_id``; bare
              ``_lis_member_id`` → ``member_id``.
   Lookup: ``lis_member_map: dict[str, int]``

3. ``*_committee_code``
   Value is a committee_code string.  Also requires a ``congress`` integer
   field present in the *same* row (read as context, not consumed).
   Target FK: replace ``_committee_code`` suffix with ``_id``; bare
              ``_committee_code`` → ``committee_id``.
   Lookup: ``committee_code_map: dict[tuple[str, int], int]``
           keyed by ``(committee_code, congress)``.

4. ``*_raw``
   Value is a raw external ID string (e.g. FEC committee ID ``"C00123456"``).
   Target FK: strip the ``_raw`` suffix; the remainder is the FK column name.
   Lookup: ``raw_id_maps: dict[str, dict[str, int]]``
           keyed by the *target* FK column name.

5. ``_financial_disclosure_natural_key`` (exact key name)
   Value must be a dict with keys ``member_id``, ``filing_year``,
   ``filing_type``, ``amendment_number``.
   Target FK: ``financial_disclosure_id``.
   Lookup: ``disclosure_natural_key_map: dict[tuple[int, int, str, int], int]``
           keyed by ``(member_id, filing_year, filing_type, amendment_number)``.

Failure reasons
---------------
``missing``              The hint value was not found in the lookup map.
``ambiguous``            Reserved for callers who pre-filter ambiguous entries
                         into the map (maps cannot have duplicate keys, so this
                         is set externally).
``lookup_not_provided``  The required lookup map was absent from *maps*.
``missing_context``      A required context field (e.g. ``congress``) was
                         absent from the row.
``invalid_natural_key``  The natural-key dict was malformed or had wrong types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Public result types


@dataclass(frozen=True)
class ResolutionFailure:
    """One failed hint-key resolution."""

    row_index: int
    hint_key: str
    hint_value: Any
    reason: str  # see module docstring for valid values

    def __str__(self) -> str:
        return (
            f"row {self.row_index}: hint_key={self.hint_key!r} "
            f"value={self.hint_value!r} reason={self.reason}"
        )


@dataclass
class ResolutionSummary:
    """Aggregate stats for a :func:`resolve_foreign_keys` call."""

    total_rows: int
    resolved_count: int  # number of hint keys successfully resolved
    failure_count: int  # number of hint keys that could not be resolved
    failures: list[ResolutionFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every hint key was resolved without failures."""
        return self.failure_count == 0


@dataclass
class ResolutionResult:
    rows: list[dict[str, Any]]
    summary: ResolutionSummary


# Suffix / key constants

_BIOGUIDE_SUFFIX = "_bioguide_id"
_LIS_SUFFIX = "_lis_member_id"
_COMMITTEE_CODE_SUFFIX = "_committee_code"
_RAW_SUFFIX = "_raw"
_DISCLOSURE_NK_KEY = "_financial_disclosure_natural_key"


# Internal helpers


def _derive_fk_col(hint_key: str, suffix: str, fallback: str) -> str:
    """Return the FK column name for a hint key.

    Strips *suffix* from *hint_key* and appends ``_id``.  If the result would
    be a bare ``_id`` (empty prefix), return *fallback* instead.

    Examples::

        _derive_fk_col("member_bioguide_id", "_bioguide_id", "member_id")
        → "member_id"

        _derive_fk_col("_bioguide_id", "_bioguide_id", "member_id")
        → "member_id"  (empty prefix → fallback)

        _derive_fk_col("subject_member_bioguide_id", "_bioguide_id", "member_id")
        → "subject_member_id"
    """
    prefix = hint_key[: -len(suffix)]
    if not prefix:
        return fallback
    return prefix + "_id"


def _is_hint_key(key: str) -> bool:
    return (
        key.endswith(_BIOGUIDE_SUFFIX)
        or key.endswith(_LIS_SUFFIX)
        or key.endswith(_COMMITTEE_CODE_SUFFIX)
        or key.endswith(_RAW_SUFFIX)
        or key == _DISCLOSURE_NK_KEY
    )


def _resolve_row(
    row: dict[str, Any],
    row_index: int,
    maps: dict[str, Any],
    failures: list[ResolutionFailure],
) -> dict[str, Any]:
    """Resolve all hint keys in one row; returns a new dict (original not mutated)."""
    out: dict[str, Any] = {}

    for key, value in row.items():
        # 1. bioguide_id  →  member_id (or *_id)
        if key.endswith(_BIOGUIDE_SUFFIX):
            fk_col = _derive_fk_col(key, _BIOGUIDE_SUFFIX, "member_id")
            bio_map: dict[str, int] | None = maps.get("bioguide_map")
            if bio_map is None:
                failures.append(ResolutionFailure(row_index, key, value, "lookup_not_provided"))
            elif value not in bio_map:
                failures.append(ResolutionFailure(row_index, key, value, "missing"))
            else:
                out[fk_col] = bio_map[value]

        # 2. lis_member_id  →  member_id (or *_id)
        elif key.endswith(_LIS_SUFFIX):
            fk_col = _derive_fk_col(key, _LIS_SUFFIX, "member_id")
            lis_map: dict[str, int] | None = maps.get("lis_member_map")
            if lis_map is None:
                failures.append(ResolutionFailure(row_index, key, value, "lookup_not_provided"))
            elif value not in lis_map:
                failures.append(ResolutionFailure(row_index, key, value, "missing"))
            else:
                out[fk_col] = lis_map[value]

        # 3. committee_code  →  committee_id (or *_id); needs congress ctx
        elif key.endswith(_COMMITTEE_CODE_SUFFIX):
            fk_col = _derive_fk_col(key, _COMMITTEE_CODE_SUFFIX, "committee_id")
            cc_map: dict[tuple[str, int], int] | None = maps.get("committee_code_map")
            if cc_map is None:
                failures.append(ResolutionFailure(row_index, key, value, "lookup_not_provided"))
            else:
                congress = row.get("congress")
                if congress is None:
                    failures.append(ResolutionFailure(row_index, key, value, "missing_context"))
                else:
                    try:
                        lookup_key = (str(value), _coerce_key_int(congress, "congress"))
                    except (TypeError, ValueError) as exc:
                        failures.append(
                            ResolutionFailure(row_index, key, value, f"invalid_context: {exc}")
                        )
                    else:
                        if lookup_key not in cc_map:
                            failures.append(ResolutionFailure(row_index, key, value, "missing"))
                        else:
                            out[fk_col] = cc_map[lookup_key]

        # 4. *_raw  →  strip suffix; look up in raw_id_maps[fk_col]
        elif key.endswith(_RAW_SUFFIX):
            fk_col = key[: -len(_RAW_SUFFIX)]
            raw_maps: dict[str, dict[str, int]] = maps.get("raw_id_maps") or {}
            sub_map = raw_maps.get(fk_col)
            if sub_map is None:
                failures.append(ResolutionFailure(row_index, key, value, "lookup_not_provided"))
            elif value not in sub_map:
                failures.append(ResolutionFailure(row_index, key, value, "missing"))
            else:
                out[fk_col] = sub_map[value]

        # 5. _financial_disclosure_natural_key  →  financial_disclosure_id
        elif key == _DISCLOSURE_NK_KEY:
            nk_map: dict[tuple[int, int, str, int], int] | None = maps.get(
                "disclosure_natural_key_map"
            )
            if nk_map is None:
                failures.append(ResolutionFailure(row_index, key, value, "lookup_not_provided"))
            else:
                try:
                    nk_tuple = (
                        _coerce_key_int(value["member_id"], "member_id"),
                        _coerce_key_int(value["filing_year"], "filing_year"),
                        str(value["filing_type"]),
                        _coerce_key_int(value["amendment_number"], "amendment_number"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    failures.append(
                        ResolutionFailure(row_index, key, value, f"invalid_natural_key: {exc}")
                    )
                else:
                    if nk_tuple not in nk_map:
                        failures.append(ResolutionFailure(row_index, key, value, "missing"))
                    else:
                        out["financial_disclosure_id"] = nk_map[nk_tuple]

        else:
            out[key] = value

    return out


def _count_hint_keys(row: dict[str, Any]) -> int:
    return sum(1 for k in row if _is_hint_key(k))


def _coerce_key_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, got bool")
    return int(value)


# Public API


def resolve_foreign_keys(
    rows: list[dict[str, Any]],
    maps: dict[str, Any] | None = None,
) -> ResolutionResult:
    """Resolve FK hint keys in *rows* using explicit lookup *maps*.

    Parameters
    ----------
    rows:
        Input row dicts.  May contain any mix of regular columns and hint
        keys.  Rows are not mutated.
    maps:
        A plain dict containing zero or more named lookup tables:

        ``bioguide_map``
            ``dict[str, int]`` — bioguide_id → member.id

        ``lis_member_map``
            ``dict[str, int]`` — lis_member_id → member.id

        ``committee_code_map``
            ``dict[tuple[str, int], int]`` — (committee_code, congress) →
            committee.id

        ``raw_id_maps``
            ``dict[str, dict[str, int]]`` — target FK column name →
            {raw external ID → integer PK}

        ``disclosure_natural_key_map``
            ``dict[tuple[int, int, str, int], int]`` — (member_id,
            filing_year, filing_type, amendment_number) →
            financial_disclosure.id

    """
    if maps is None:
        maps = {}

    failures: list[ResolutionFailure] = []
    resolved_rows: list[dict[str, Any]] = []
    total_hint_keys = 0

    for idx, row in enumerate(rows):
        total_hint_keys += _count_hint_keys(row)
        resolved_rows.append(_resolve_row(row, idx, maps, failures))

    summary = ResolutionSummary(
        total_rows=len(rows),
        resolved_count=total_hint_keys - len(failures),
        failure_count=len(failures),
        failures=failures,
    )
    return ResolutionResult(rows=resolved_rows, summary=summary)
