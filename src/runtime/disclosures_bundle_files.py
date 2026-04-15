"""Resolve DisclosureArtifactEntry to ArtifactMeta and local bytes.

No DB writes.  No network calls.  SHA-256 is validated against file contents.

Public surface
--------------
entry_artifact_meta(entry, *, bioguide_id) -> ArtifactMeta
    Build ArtifactMeta from a bundle entry.  Requires a pre-resolved
    bioguide_id because ArtifactMeta.member_bioguide_id is non-optional.
    storage_key is set to entry.storage_uri so callers can locate the file
    via local_root / meta.storage_key without any further path translation.

entry_local_path(entry, local_root) -> Path
    Return the filesystem path where the artifact lives.

read_entry_bytes(entry, local_root) -> bytes
    Read and return the raw bytes for a bundle entry.

verify_entry_sha256(entry, local_root) -> None
    Raise Sha256Mismatch if the on-disk digest does not match the bundle entry.

read_and_verify_entry(entry, local_root) -> bytes
    Read bytes and validate SHA-256 in one call.  Returns verified bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.models import Chamber
from src.runtime.disclosures_bundle import DisclosureArtifactEntry


class Sha256Mismatch(ValueError):
    """On-disk SHA-256 does not match the bundle entry's declared digest."""


def entry_artifact_meta(
    entry: DisclosureArtifactEntry,
    *,
    bioguide_id: str,
) -> ArtifactMeta:
    """Build an ArtifactMeta from a bundle entry and a resolved bioguide_id.

    storage_key is set to entry.storage_uri so the resulting meta is
    consistent with files located at local_root / entry.storage_uri.

    Raises ValueError if entry.artifact_kind is not a recognised ArtifactKind.
    """
    return ArtifactMeta(
        source_slug=entry.source_slug,
        chamber=Chamber(entry.chamber),
        artifact_kind=ArtifactKind(entry.artifact_kind),
        source_url=entry.source_url,
        storage_key=entry.storage_uri,
        member_bioguide_id=bioguide_id,
        filing_year=entry.filing_year,
        sha256=entry.sha256,
        source_record_id=entry.source_record_id,
    )


def entry_local_path(entry: DisclosureArtifactEntry, local_root: Path) -> Path:
    """Return the local filesystem path for a bundle entry."""
    return local_root / entry.storage_uri


def read_entry_bytes(entry: DisclosureArtifactEntry, local_root: Path) -> bytes:
    """Read and return the raw bytes for a bundle entry.

    Raises FileNotFoundError if the file does not exist.
    """
    path = entry_local_path(entry, local_root)
    if not path.exists():
        raise FileNotFoundError(f"Bundle artifact not found: {path}")
    return path.read_bytes()


def verify_entry_sha256(entry: DisclosureArtifactEntry, local_root: Path) -> None:
    """Verify the on-disk SHA-256 matches the bundle entry's declared digest.

    Raises FileNotFoundError if the file is absent.
    Raises Sha256Mismatch if the digest does not match.
    """
    data = read_entry_bytes(entry, local_root)
    actual = hashlib.sha256(data).hexdigest()
    if actual != entry.sha256:
        raise Sha256Mismatch(
            f"SHA-256 mismatch for {entry.storage_uri!r}: "
            f"expected {entry.sha256!r}, got {actual!r}"
        )


def read_and_verify_entry(
    entry: DisclosureArtifactEntry,
    local_root: Path,
) -> bytes:
    """Read bytes and validate SHA-256 in one call.

    Returns verified bytes on success.
    Raises FileNotFoundError if the file is absent.
    Raises Sha256Mismatch if the digest does not match.
    """
    data = read_entry_bytes(entry, local_root)
    actual = hashlib.sha256(data).hexdigest()
    if actual != entry.sha256:
        raise Sha256Mismatch(
            f"SHA-256 mismatch for {entry.storage_uri!r}: "
            f"expected {entry.sha256!r}, got {actual!r}"
        )
    return data
