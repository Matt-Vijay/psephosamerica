"""Local published-artifact reader for precomputed JSON snapshots.

Reads exported artifacts from a local snapshot root directory produced by
:func:`~src.export.filesystem.write_planned_files`.  Validates loaded JSON
against the existing payload models.  No web framework, no cloud clients.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from .manifest import SnapshotManifest
from .writer import evidence_path, manifest_path, member_path, zip_path


# ── Internal helper ────────────────────────────────────────────────


def _load_json(file: Path) -> object:
    """Read and parse JSON from *file*.

    Raises:
        FileNotFoundError: If *file* does not exist.
        ValueError:        If *file* contains invalid JSON.
    """
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    try:
        return json.loads(file.read_bytes())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file}: {exc}") from exc


# ── Public helpers ─────────────────────────────────────────────────


def load_member_profile(snapshot_root: Path, slug: str) -> MemberProfilePayload:
    """Load and validate a member profile artifact by member slug.

    Args:
        snapshot_root: Root directory of the local snapshot.
        slug:          Member slug (e.g. ``'charles-schumer'``).

    Returns:
        Validated :class:`~src.export.contracts.MemberProfilePayload`.

    Raises:
        FileNotFoundError: If the artifact file does not exist.
        ValueError:        If the JSON is invalid or fails model validation.
    """
    file = snapshot_root / member_path(slug)
    data = _load_json(file)
    return MemberProfilePayload.model_validate(data)


def load_evidence_card(snapshot_root: Path, evidence_card_id: str) -> EvidenceCardPayload:
    """Load and validate an evidence card artifact by its public ID.

    Args:
        snapshot_root:   Root directory of the local snapshot.
        evidence_card_id: Public ID of the evidence card (e.g. ``'ec-001'``).

    Returns:
        Validated :class:`~src.export.contracts.EvidenceCardPayload`.

    Raises:
        FileNotFoundError: If the artifact file does not exist.
        ValueError:        If the JSON is invalid or fails model validation.
    """
    file = snapshot_root / evidence_path(evidence_card_id)
    data = _load_json(file)
    return EvidenceCardPayload.model_validate(data)


def load_zip_feed(snapshot_root: Path, zip_code: str) -> ZipFeedPayload:
    """Load and validate a ZIP feed artifact by 5-digit ZIP code.

    Args:
        snapshot_root: Root directory of the local snapshot.
        zip_code:      5-digit ZIP code string (e.g. ``'10001'``).

    Returns:
        Validated :class:`~src.export.contracts.ZipFeedPayload`.

    Raises:
        FileNotFoundError: If the artifact file does not exist.
        ValueError:        If the JSON is invalid or fails model validation.
    """
    file = snapshot_root / zip_path(zip_code)
    data = _load_json(file)
    return ZipFeedPayload.model_validate(data)


def load_manifest(snapshot_root: Path, snapshot_id: str) -> SnapshotManifest:
    """Load and validate the snapshot manifest by snapshot ID.

    Args:
        snapshot_root: Root directory of the local snapshot.
        snapshot_id:   Snapshot identifier (e.g. ``'2026-04-13'``).

    Returns:
        Validated :class:`~src.export.manifest.SnapshotManifest`.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        ValueError:        If the JSON is invalid or fails model validation.
    """
    file = snapshot_root / manifest_path(snapshot_id)
    data = _load_json(file)
    return SnapshotManifest.model_validate(data)


def list_artifact_paths(manifest: SnapshotManifest) -> list[str]:
    """Return the relative paths of all artifacts listed in a manifest.

    Args:
        manifest: A validated :class:`~src.export.manifest.SnapshotManifest`.

    Returns:
        List of relative path strings, one per manifest entry, in order.
    """
    return [entry.path for entry in manifest.entries]
