"""Canonical lookup-map builders from fetched DB rows.

Pure helpers only — no DB access, no I/O.  Callers fetch rows from the
database and pass them here.  Each builder detects duplicate or ambiguous
input and raises :class:`LookupBuildError` rather than silently picking one.

Maps produced here are consumed directly by :mod:`src.db.foreign_keys`:

    ``bioguide_map``
        ``dict[str, int]`` — bioguide_id → member.id

    ``lis_member_map``
        ``dict[str, int]`` — lis_member_id → member.id

    ``committee_code_map``
        ``dict[tuple[str, int], int]`` — (committee_code, congress) →
        committee.id

    ``raw_id_maps``
        ``dict[str, dict[str, int]]`` — FK column name → {raw external ID →
        integer PK}.  Holds at least:

        - ``"fec_candidate_id"`` → fec_candidate_id → member.id
        - ``"fec_committee_id"`` → fec_committee_id → fec_committee.id

    ``disclosure_natural_key_map``
        ``dict[tuple[int, int, str, int], int]`` — (member_id, filing_year,
        filing_type, amendment_number) → financial_disclosure.id

Collect all maps into a :class:`LookupBundle` and pass ``bundle.to_maps()``
to :func:`src.db.foreign_keys.resolve_foreign_keys`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class LookupBuildError(ValueError):
    """Raised when input rows are ambiguous or missing required fields.

    Attributes
    ----------
    duplicates:
        List of ``(key, [row, ...])`` pairs — each entry is one key that
        appeared more than once in the input, along with all rows that share
        it.
    """

    def __init__(
        self,
        message: str,
        duplicates: list[tuple[Any, list[dict[str, Any]]]] | None = None,
    ) -> None:
        super().__init__(message)
        self.duplicates: list[tuple[Any, list[dict[str, Any]]]] = duplicates or []


# ---------------------------------------------------------------------------
# Result bundle
# ---------------------------------------------------------------------------


@dataclass
class LookupBundle:
    """All FK lookup maps in a single composable object.

    Pass ``bundle.to_maps()`` directly to
    :func:`src.db.foreign_keys.resolve_foreign_keys`.
    """

    bioguide_map: dict[str, int] = field(default_factory=dict)
    lis_member_map: dict[str, int] = field(default_factory=dict)
    committee_code_map: dict[tuple[str, int], int] = field(default_factory=dict)
    # raw_id_maps holds sub-maps for every *_raw hint handled via raw_id_maps:
    #   "fec_candidate_id" → member.id
    #   "fec_committee_id" → fec_committee.id
    fec_candidate_map: dict[str, int] = field(default_factory=dict)
    fec_committee_map: dict[str, int] = field(default_factory=dict)
    disclosure_natural_key_map: dict[tuple[int, int, str, int], int] = field(
        default_factory=dict
    )

    def to_maps(self) -> dict[str, Any]:
        """Return a maps dict compatible with :func:`resolve_foreign_keys`."""
        return {
            "bioguide_map": self.bioguide_map,
            "lis_member_map": self.lis_member_map,
            "committee_code_map": self.committee_code_map,
            "raw_id_maps": {
                "fec_candidate_id": self.fec_candidate_map,
                "fec_committee_id": self.fec_committee_map,
            },
            "disclosure_natural_key_map": self.disclosure_natural_key_map,
        }


# ---------------------------------------------------------------------------
# Internal duplicate-detection helper
# ---------------------------------------------------------------------------


def _detect_duplicates(
    rows: list[dict[str, Any]],
    key_fn: Any,  # Callable[[dict], Any]
    required_fields: list[str],
    map_name: str,
) -> tuple[dict[Any, int], list[tuple[Any, list[dict[str, Any]]]]]:
    """Build a ``{key: pk}`` map from *rows*, detecting duplicates.

    Parameters
    ----------
    rows:
        Source rows, each a plain dict.
    key_fn:
        Callable that extracts the lookup key from one row.
    required_fields:
        Field names that must be present and non-``None`` in every row.
    map_name:
        Human-readable name used in error messages.

    Returns
    -------
    (result_map, duplicates)
        ``result_map`` — ``{key: id}`` for all non-duplicate rows.
        ``duplicates`` — list of ``(key, [rows...])`` for every key that
        appeared more than once.

    Raises
    ------
    LookupBuildError
        When required fields are missing from any row.
    """
    missing: list[str] = []
    for i, row in enumerate(rows):
        for f in required_fields:
            if f not in row or row[f] is None:
                missing.append(f"row {i}: missing required field {f!r} for {map_name}")
    if missing:
        raise LookupBuildError(
            f"{map_name}: {len(missing)} row(s) missing required fields:\n"
            + "\n".join(missing)
        )

    seen: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        k = key_fn(row)
        if k is None:
            # NULL natural-key field — skip (builder callers filter NULLs
            # before calling, but be defensive)
            continue
        seen.setdefault(k, []).append(row)

    duplicates = [(k, v) for k, v in seen.items() if len(v) > 1]
    result_map: dict[Any, int] = {
        k: v[0]["id"] for k, v in seen.items() if len(v) == 1
    }
    return result_map, duplicates


# ---------------------------------------------------------------------------
# Individual builders
# ---------------------------------------------------------------------------


def build_bioguide_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``bioguide_id → member.id`` from *member* table rows.

    Required row fields: ``id``, ``bioguide_id``.

    Only rows where ``bioguide_id`` is non-``None`` are included.  If two rows
    share the same ``bioguide_id``, :class:`LookupBuildError` is raised.
    """
    eligible = [r for r in rows if r.get("bioguide_id") is not None]
    result, duplicates = _detect_duplicates(
        eligible,
        key_fn=lambda r: r["bioguide_id"],
        required_fields=["id", "bioguide_id"],
        map_name="bioguide_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"bioguide_map: {len(duplicates)} duplicate bioguide_id(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


def build_lis_member_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``lis_member_id → member.id`` from *member* table rows.

    Required row fields: ``id``, ``lis_member_id``.

    Rows where ``lis_member_id`` is ``None`` are silently skipped (most House
    members have no LIS ID).  Duplicate LIS IDs raise :class:`LookupBuildError`.
    """
    eligible = [r for r in rows if r.get("lis_member_id") is not None]
    result, duplicates = _detect_duplicates(
        eligible,
        key_fn=lambda r: r["lis_member_id"],
        required_fields=["id", "lis_member_id"],
        map_name="lis_member_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"lis_member_map: {len(duplicates)} duplicate lis_member_id(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


def build_fec_candidate_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``fec_candidate_id → member.id`` from *member* table rows.

    Required row fields: ``id``, ``fec_candidate_id``.

    Rows where ``fec_candidate_id`` is ``None`` are silently skipped.
    Duplicate FEC candidate IDs raise :class:`LookupBuildError`.
    """
    eligible = [r for r in rows if r.get("fec_candidate_id") is not None]
    result, duplicates = _detect_duplicates(
        eligible,
        key_fn=lambda r: r["fec_candidate_id"],
        required_fields=["id", "fec_candidate_id"],
        map_name="fec_candidate_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"fec_candidate_map: {len(duplicates)} duplicate fec_candidate_id(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


def build_committee_code_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, int], int]:
    """Build ``(committee_code, congress) → committee.id`` from *committee* rows.

    Required row fields: ``id``, ``committee_code``, ``congress``.

    Duplicate ``(committee_code, congress)`` pairs raise
    :class:`LookupBuildError`.
    """

    def _key(r: dict[str, Any]) -> tuple[str, int]:
        return (str(r["committee_code"]), int(r["congress"]))

    result, duplicates = _detect_duplicates(
        rows,
        key_fn=_key,
        required_fields=["id", "committee_code", "congress"],
        map_name="committee_code_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"committee_code_map: {len(duplicates)} duplicate (committee_code, congress) pair(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


def build_fec_committee_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``fec_committee_id → fec_committee.id`` from *fec_committee* rows.

    Required row fields: ``id``, ``fec_committee_id``.

    Duplicate ``fec_committee_id`` values raise :class:`LookupBuildError`.
    """
    result, duplicates = _detect_duplicates(
        rows,
        key_fn=lambda r: r["fec_committee_id"],
        required_fields=["id", "fec_committee_id"],
        map_name="fec_committee_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"fec_committee_map: {len(duplicates)} duplicate fec_committee_id(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


def build_disclosure_natural_key_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[int, int, str, int], int]:
    """Build the financial-disclosure natural-key map.

    Required row fields: ``id``, ``member_id``, ``filing_year``,
    ``filing_type``, ``amendment_number``.

    Key: ``(member_id, filing_year, filing_type, amendment_number)``.

    Duplicate natural keys raise :class:`LookupBuildError`.
    """

    def _key(r: dict[str, Any]) -> tuple[int, int, str, int]:
        return (
            int(r["member_id"]),
            int(r["filing_year"]),
            str(r["filing_type"]),
            int(r["amendment_number"]),
        )

    result, duplicates = _detect_duplicates(
        rows,
        key_fn=_key,
        required_fields=["id", "member_id", "filing_year", "filing_type", "amendment_number"],
        map_name="disclosure_natural_key_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"disclosure_natural_key_map: {len(duplicates)} duplicate natural key(s): "
            + ", ".join(str(k) for k, _ in duplicates),
            duplicates=duplicates,
        )
    return result


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------


def build_lookup_bundle(
    *,
    member_rows: list[dict[str, Any]],
    committee_rows: list[dict[str, Any]],
    fec_committee_rows: list[dict[str, Any]],
    financial_disclosure_rows: list[dict[str, Any]],
) -> LookupBundle:
    """Build a complete :class:`LookupBundle` from pre-fetched DB rows.

    All four row sets are required (pass empty lists for tables not yet
    populated).  Raises :class:`LookupBuildError` on the first ambiguous set
    detected.

    Parameters
    ----------
    member_rows:
        Rows from the ``member`` table.  Required fields per row: ``id``,
        ``bioguide_id``.  Optional: ``lis_member_id``, ``fec_candidate_id``.
    committee_rows:
        Rows from the ``committee`` table.  Required: ``id``,
        ``committee_code``, ``congress``.
    fec_committee_rows:
        Rows from the ``fec_committee`` table.  Required: ``id``,
        ``fec_committee_id``.
    financial_disclosure_rows:
        Rows from the ``financial_disclosure`` table.  Required: ``id``,
        ``member_id``, ``filing_year``, ``filing_type``, ``amendment_number``.

    Returns
    -------
    LookupBundle
        Fully populated bundle; call ``.to_maps()`` to get a maps dict for
        :func:`src.db.foreign_keys.resolve_foreign_keys`.
    """
    return LookupBundle(
        bioguide_map=build_bioguide_map(member_rows),
        lis_member_map=build_lis_member_map(member_rows),
        fec_candidate_map=build_fec_candidate_map(member_rows),
        committee_code_map=build_committee_code_map(committee_rows),
        fec_committee_map=build_fec_committee_map(fec_committee_rows),
        disclosure_natural_key_map=build_disclosure_natural_key_map(
            financial_disclosure_rows
        ),
    )
