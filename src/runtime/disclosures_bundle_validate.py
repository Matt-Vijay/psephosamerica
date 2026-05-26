"""Semantic validation helpers for DisclosuresBundle.

These helpers operate on a fully-parsed DisclosuresBundle and check
invariants that span entries (coherence, duplicates) rather than per-field
type checking, which belongs to disclosures_bundle.py.

Public API
----------
validate_disclosures_bundle(bundle) -> None
    Raises ValueError listing all violations if any invariant is broken.

DisclosuresBundleValidationError
    ValueError subclass carrying the exact violation list.

check_source_slug_chamber_coherence(bundle) -> list[str]
    Verifies each entry's source_slug matches its chamber.

check_doc_id_source_record_id_coherence(bundle) -> list[str]
    Verifies index_row.doc_id == source_record_id on every entry.

check_duplicate_entries(bundle) -> list[str]
    Verifies source_record_id is unique across the bundle.

check_index_row_chamber_shape(bundle) -> list[str]
    Verifies each entry's index_row type is consistent with its chamber.

check_source_url_chamber_coherence(bundle) -> list[str]
    Verifies each entry's source_url is an official URL for its chamber.
"""

from __future__ import annotations

from src.parse.disclosures.source_urls import (
    DisclosureSourceChamber,
    parse_official_disclosure_artifact_url,
)
from src.runtime.disclosures_bundle import (
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
)
from src.runtime.sources import DISCLOSURE_SOURCE_SLUG_BY_CHAMBER


class DisclosuresBundleValidationError(ValueError):
    """Raised when a DisclosuresBundle violates one or more invariants."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = tuple(violations)
        bullet_list = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(f"DisclosuresBundle validation failed:\n{bullet_list}")


def check_source_slug_chamber_coherence(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings where source_slug does not match chamber.

    Each entry must carry the canonical source slug for its chamber.
    """
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        expected = DISCLOSURE_SOURCE_SLUG_BY_CHAMBER.get(entry.chamber)
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
            violations.append(f"artifacts[{i}]: chamber='house' but index_row is {row_type}")
        elif entry.chamber == "senate" and not isinstance(entry.index_row, SenateBundledIndexRow):
            violations.append(f"artifacts[{i}]: chamber='senate' but index_row is {row_type}")
    return violations


def check_source_url_chamber_coherence(bundle: DisclosuresBundle) -> list[str]:
    """Return violation strings for non-official or cross-chamber source URLs."""
    violations: list[str] = []
    for i, entry in enumerate(bundle.artifacts):
        if entry.chamber not in ("house", "senate"):
            violations.append(
                f"artifacts[{i}]: chamber={entry.chamber!r} cannot validate source_url"
            )
            continue
        chamber: DisclosureSourceChamber = "house" if entry.chamber == "house" else "senate"
        try:
            parts = parse_official_disclosure_artifact_url(
                entry.source_url,
                chamber=chamber,
                label="source_url",
            )
        except ValueError as exc:
            violations.append(f"artifacts[{i}]: {exc}")
            continue

        if parts.document_id != entry.source_record_id:
            violations.append(
                f"artifacts[{i}]: source_record_id={entry.source_record_id!r} "
                f"but source_url document id={parts.document_id!r}"
            )
        if parts.filing_year is not None and parts.filing_year != entry.filing_year:
            violations.append(
                f"artifacts[{i}]: filing_year={entry.filing_year} "
                f"but source_url year={parts.filing_year}"
            )
        if (
            entry.chamber == "house"
            and isinstance(entry.index_row, HouseBundledIndexRow)
            and parts.house_filing_kind is not None
            and parts.house_filing_kind != entry.index_row.filing_kind
        ):
            violations.append(
                f"artifacts[{i}]: filing_kind={entry.index_row.filing_kind!r} "
                f"but source_url kind={parts.house_filing_kind!r}"
            )
    return violations


def validate_disclosures_bundle(bundle: DisclosuresBundle) -> None:
    """Raise ValueError if any semantic invariant in the bundle is violated.

    Runs all coherence checks and aggregates every violation into a single
    error so callers see the full picture in one pass.
    """
    violations: list[str] = []
    violations.extend(check_source_slug_chamber_coherence(bundle))
    violations.extend(check_doc_id_source_record_id_coherence(bundle))
    violations.extend(check_duplicate_entries(bundle))
    violations.extend(check_index_row_chamber_shape(bundle))
    violations.extend(check_source_url_chamber_coherence(bundle))
    if violations:
        raise DisclosuresBundleValidationError(violations)
