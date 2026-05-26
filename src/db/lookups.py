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

    ``disclosure_source_record_id_map``
        ``dict[str, int]`` — financial_disclosure.source_record_id →
        financial_disclosure.id. Used to resolve amendment supersession links.

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
    disclosure_natural_key_map: dict[tuple[int, int, str, int], int] = field(default_factory=dict)
    disclosure_source_record_id_map: dict[str, int] = field(default_factory=dict)

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
            "disclosure_source_record_id_map": self.disclosure_source_record_id_map,
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

    Returns ``(result_map, duplicates)`` where ``result_map`` contains only
    unambiguous rows and ``duplicates`` lists every key that appeared more than
    once.  Raises :class:`LookupBuildError` when required fields are missing.
    """
    missing: list[str] = []
    for i, row in enumerate(rows):
        for f in required_fields:
            if f not in row or row[f] is None:
                missing.append(f"row {i}: missing required field {f!r} for {map_name}")
        if "id" in row and isinstance(row["id"], bool):
            missing.append(f"row {i}: field 'id' for {map_name} must be an integer, got bool")
    if missing:
        raise LookupBuildError(
            f"{map_name}: {len(missing)} row(s) missing required fields:\n" + "\n".join(missing)
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
        k: _coerce_key_int(v[0]["id"], "id", map_name) for k, v in seen.items() if len(v) == 1
    }
    return result_map, duplicates


def _coerce_key_int(value: Any, field_name: str, map_name: str) -> int:
    if isinstance(value, bool):
        raise LookupBuildError(f"{map_name}: {field_name} must be an integer, got bool")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LookupBuildError(
            f"{map_name}: {field_name} must be an integer, got {type(value).__name__}"
        ) from exc


# ---------------------------------------------------------------------------
# Individual builders
# ---------------------------------------------------------------------------


def build_bioguide_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``bioguide_id → member.id`` from *member* table rows.

    Required row fields: ``id``, ``bioguide_id``.
    Rows where ``bioguide_id`` is ``None`` are skipped.
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
    Rows where ``lis_member_id`` is ``None`` are skipped (most House members
    have no LIS ID).
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
    Rows where ``fec_candidate_id`` is ``None`` are skipped.
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
    """

    def _key(r: dict[str, Any]) -> tuple[str, int]:
        return (
            str(r["committee_code"]),
            _coerce_key_int(r["congress"], "congress", "committee_code_map"),
        )

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
    """Build ``(member_id, filing_year, filing_type, amendment_number) → financial_disclosure.id``.

    Required row fields: ``id``, ``member_id``, ``filing_year``,
    ``filing_type``, ``amendment_number``.
    """

    def _key(r: dict[str, Any]) -> tuple[int, int, str, int]:
        return (
            _coerce_key_int(r["member_id"], "member_id", "disclosure_natural_key_map"),
            _coerce_key_int(r["filing_year"], "filing_year", "disclosure_natural_key_map"),
            str(r["filing_type"]),
            _coerce_key_int(
                r["amendment_number"], "amendment_number", "disclosure_natural_key_map"
            ),
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


def build_disclosure_source_record_id_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Build ``financial_disclosure.source_record_id → financial_disclosure.id``.

    Rows where ``source_record_id`` is ``None`` are skipped. Duplicates are
    rejected so amendment supersession never silently points at an arbitrary
    filing.
    """
    eligible = [r for r in rows if r.get("source_record_id") is not None]
    result, duplicates = _detect_duplicates(
        eligible,
        key_fn=lambda r: str(r["source_record_id"]),
        required_fields=["id", "source_record_id"],
        map_name="disclosure_source_record_id_map",
    )
    if duplicates:
        raise LookupBuildError(
            f"disclosure_source_record_id_map: {len(duplicates)} duplicate source_record_id(s): "
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

    Pass empty lists for tables not yet populated.

    member_rows:               ``member`` — required fields: ``id``, ``bioguide_id``;
                               optional: ``lis_member_id``, ``fec_candidate_id``.
    committee_rows:            ``committee`` — required: ``id``, ``committee_code``, ``congress``.
    fec_committee_rows:        ``fec_committee`` — required: ``id``, ``fec_committee_id``.
    financial_disclosure_rows: ``financial_disclosure`` — required: ``id``, ``member_id``,
                               ``filing_year``, ``filing_type``, ``amendment_number``;
                               optional: ``source_record_id``.
    """
    return LookupBundle(
        bioguide_map=build_bioguide_map(member_rows),
        lis_member_map=build_lis_member_map(member_rows),
        fec_candidate_map=build_fec_candidate_map(member_rows),
        committee_code_map=build_committee_code_map(committee_rows),
        fec_committee_map=build_fec_committee_map(fec_committee_rows),
        disclosure_natural_key_map=build_disclosure_natural_key_map(financial_disclosure_rows),
        disclosure_source_record_id_map=build_disclosure_source_record_id_map(
            financial_disclosure_rows
        ),
    )
