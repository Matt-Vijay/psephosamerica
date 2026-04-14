"""Local filesystem publisher for planned snapshot files.

Helpers for writing :class:`~src.export.writer.PlannedFile` objects to disk,
reading back a manifest file, and verifying written content against planned
hashes.  No cloud clients.  All paths are kept relative and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from .builders import sha256_hex
from .manifest import SnapshotManifest
from .writer import PlannedFile


def _ensure_parents(target: Path) -> None:
    """Create parent directories for *target* if they do not exist."""
    target.parent.mkdir(parents=True, exist_ok=True)


def write_planned_files(files: list[PlannedFile], target_dir: Path) -> None:
    """Write each :class:`PlannedFile` under *target_dir*.

    The relative ``path`` stored on each file is joined onto *target_dir*.
    Parent directories are created as needed.  Existing files are overwritten.

    Args:
        files:      Planned files produced by :func:`~src.export.writer.plan_snapshot`.
        target_dir: Absolute or relative root directory to write into.
    """
    for planned in files:
        dest = target_dir / planned.path
        _ensure_parents(dest)
        dest.write_bytes(planned.content)


def read_manifest(manifest_file: Path) -> SnapshotManifest:
    """Parse and validate a manifest JSON file from disk.

    Args:
        manifest_file: Path to the ``manifest.json`` file on disk.

    Returns:
        A validated :class:`~src.export.manifest.SnapshotManifest` instance.

    Raises:
        FileNotFoundError: If *manifest_file* does not exist.
        ValueError:        If the JSON does not conform to the manifest schema.
    """
    raw = manifest_file.read_bytes()
    data = json.loads(raw)
    return SnapshotManifest.model_validate(data)


def verify_written_files(
    files: list[PlannedFile],
    target_dir: Path,
) -> list[str]:
    """Verify that files written to *target_dir* match their planned hashes.

    Reads each file from disk and compares the SHA-256 digest against the
    value stored in the corresponding :class:`PlannedFile`.

    Args:
        files:      The original planned files (with expected hashes).
        target_dir: Directory where the files were written.

    Returns:
        A list of relative paths whose on-disk content does not match the
        planned hash.  An empty list means all files verified successfully.
        Missing files are also reported as failures.
    """
    failures: list[str] = []
    for planned in files:
        dest = target_dir / planned.path
        if not dest.exists():
            failures.append(planned.path)
            continue
        actual_hash = sha256_hex(dest.read_bytes())
        if actual_hash != planned.sha256:
            failures.append(planned.path)
    return failures
