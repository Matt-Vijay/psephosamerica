"""Congress archive manifest validation — file-existence checks only.

No payload parsing is performed here; the sole concern is whether every
path referenced in a CongressArchiveManifest is present on disk.

Public API
----------
validate_congress_archive_manifest(manifest, *, exists=Path.exists)
    -> ArchiveValidationResult

The ``exists`` parameter is an injectable seam (a callable that takes a
Path and returns bool) so callers in tests can substitute a fake without
patching globals or sys.modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.ingest.congress.archive import CongressArchiveManifest


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissingFile:
    """One file that was referenced in the manifest but not found on disk.

    Attributes:
        path:  The absolute (or archive-relative) path that does not exist.
        label: Human-readable identifier for the source group and key,
               e.g. ``"members"``, ``"member_details[P000197]"``,
               ``"house_votes[2025_0042]"``.
    """

    path: Path
    label: str


@dataclass(frozen=True, slots=True)
class ArchiveValidationResult:
    """Outcome of validating a CongressArchiveManifest against the filesystem.

    Attributes:
        valid:   True iff every path in the manifest exists on disk.
        missing: Tuple of MissingFile entries, one per absent path.
                 Empty when valid is True.
    """

    valid: bool
    missing: tuple[MissingFile, ...]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_congress_archive_manifest(
    manifest: CongressArchiveManifest,
    *,
    exists: Callable[[Path], bool] = Path.exists,
) -> ArchiveValidationResult:
    """Check that every path in *manifest* exists on disk.

    Args:
        manifest: A fully-constructed CongressArchiveManifest whose paths
                  point into a local archive directory.
        exists:   Callable used to test path existence.  Defaults to
                  ``Path.exists``; inject a substitute in tests to avoid
                  real filesystem access.

    Returns:
        An ArchiveValidationResult.  ``result.valid`` is True iff no files
        are missing.  ``result.missing`` lists every absent path with a
        label that identifies which source group it belongs to.
    """
    missing: list[MissingFile] = []

    def _check(path: Path, label: str) -> None:
        if not exists(path):
            missing.append(MissingFile(path=path, label=label))

    # Mandatory singleton list payloads
    _check(manifest.members.path, "members")
    _check(manifest.committees.path, "committees")
    _check(manifest.bills.path, "bills")

    # Per-member detail files
    for member_detail in manifest.member_details:
        _check(member_detail.path, f"member_details[{member_detail.bioguide_id}]")

    # Per-bill cosponsor and detail files
    for cosponsor_source in manifest.cosponsors:
        key = (
            f"{cosponsor_source.congress}_"
            f"{cosponsor_source.bill_type}_"
            f"{cosponsor_source.bill_number}"
        )
        _check(cosponsor_source.path, f"cosponsors[{key}]")

    for bill_detail in manifest.bill_details:
        key = f"{bill_detail.congress}_{bill_detail.bill_type}_{bill_detail.bill_number}"
        _check(bill_detail.path, f"bill_details[{key}]")

    # Optional vote files
    for house_vote in manifest.house_votes:
        label = f"house_votes[{house_vote.year}_{house_vote.roll_call_number:04d}]"
        _check(house_vote.path, label)

    for senate_vote in manifest.senate_votes:
        label = (
            f"senate_votes["
            f"{senate_vote.congress}_{senate_vote.session_number}_"
            f"{senate_vote.roll_call_number:05d}]"
        )
        _check(senate_vote.path, label)

    return ArchiveValidationResult(
        valid=len(missing) == 0,
        missing=tuple(missing),
    )
