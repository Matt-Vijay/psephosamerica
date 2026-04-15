"""Semantic validation helpers for DisclosuresBundle.

These helpers operate on a fully-parsed DisclosuresBundle and check
invariants that span entries (coherence, duplicates) rather than per-field
type checking, which belongs to disclosures_bundle.py.

Public API
----------
validate_disclosures_bundle(bundle) -> None
    Raises ValueError listing all violations if any invariant is broken.

check_source_slug_chamber_coherence(bundle) -> list[str]
    Verifies each entry's source_slug matches its chamber.

check_doc_id_source_record_id_coherence(bundle) -> list[str]
    Verifies index_row.doc_id == source_record_id on every entry.

check_duplicate_entries(bundle) -> list[str]
    Verifies source_record_id is unique across the bundle.

check_index_row_chamber_shape(bundle) -> list[str]
    Verifies each entry's index_row type is consistent with its chamber.
"""

from __future__ import annotations

from src.runtime.disclosures_bundle import (
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
)

# Canonical source_slug expected for each chamber.
_CHAMBER_SLUG: dict[str, str] = {
    "house": "house_disclosures",
    "senate": "senate_disclosures",
}


def check_source_slug_chamber_coherence(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings where source_slug does not match chamber.

    Each house entry must carry source_slug='house_disclosures'; each senate
    entry must carry source_slug='senate_disclosures'.
    """
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        expected = _CHAMBER_SLUG.get(entry.chamber)
        if expected is not None and entry.source_slug != expected:
            violations.append(
                f"artifacts[{i}]: chamber={entry.chamber!r} expects "
                f"source_slug={expected!r}, got {entry.source_slug!r}"
            )
    return violations


def check_doc_id_source_record_id_coherence(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings where index_row.doc_id != source_record_id.

    The bundled index row must carry the same document identifier as the entry
    so that downstream resolution never cross-links records.
    """
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        if entry.index_row.doc_id != entry.source_record_id:
            violations.append(
                f"artifacts[{i}]: source_record_id={entry.source_record_id!r} "
                f"but index_row.doc_id={entry.index_row.doc_id!r}"
            )
    return violations


def check_duplicate_entries(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings for duplicate source_record_id values.

    Each source_record_id must appear exactly once.  The first occurrence is
    treated as canonical; all subsequent occurrences are violations.
    """
    seen: dict[str, int] = {}
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        rid = entry.source_record_id
        if rid in seen:
            violations.append(
                f"artifacts[{i}]: duplicate source_record_id={rid!r} "
                f"(first seen at index {seen[rid]})"
            )
        else:
            seen[rid] = i
    return violations


def check_index_row_chamber_shape(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings where index_row type does not match chamber.

    house entries must carry HouseBundledIndexRow;
    senate entries must carry SenateBundledIndexRow.
    """
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        row_type = type(entry.index_row).__name__
        if entry.chamber == "house" and not isinstance(entry.index_row, HouseBundledIndexRow):
            violations.append(
                f"artifacts[{i}]: chamber='house' but index_row is {row_type}"
            )
        elif entry.chamber == "senate" and not isinstance(
            entry.index_row, SenateBundledIndexRow
        ):
            violations.append(
                f"artifacts[{i}]: chamber='senate' but index_row is {row_type}"
            )
    return violations


def validate_disclosures_bundle(bundle: DisclosuresBundle) -> None:
    """Raise ValueError if any semantic invariant in the bundle is violated.

    Runs all four coherence checks and aggregates every violation into a single
    error so callers see the full picture in one pass.
    """
    violations: list[str] = []
    violations.extend(check_source_slug_chamber_coherence(bundle))
    violations.extend(check_doc_id_source_record_id_coherence(bundle))
    violations.extend(check_duplicate_entries(bundle))
    violations.extend(check_index_row_chamber_shape(bundle))
    if violations:
        bullet_list = "\n".join(f"  - {v}" for v in violations)
        raise ValueError(f"DisclosuresBundle validation failed:\n{bullet_list}")
