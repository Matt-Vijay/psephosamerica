"""Load a Congress archive manifest from a JSON file.

Thin I/O layer: reads the file, parses JSON, resolves relative paths
against the manifest file's parent directory, and delegates to
manifest_from_dict() for validation and typed construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from src.core.files import sha256_file, write_text_atomic as _write_text_atomic
from src.ingest.congress.archive import (
    CongressArchiveManifest,
    congress_archive_manifest_to_dict,
    manifest_from_dict,
)


def load_manifest(manifest_path: Path) -> CongressArchiveManifest:
    """Load a CongressArchiveManifest from a JSON file on disk.

    Relative paths recorded inside the manifest are resolved against the
    directory that contains the manifest file, so the archive directory
    and the manifest file may be moved together without editing any paths.

    Args:
        manifest_path: Absolute or relative path to the JSON manifest file.

    Returns:
        A fully constructed, immutable CongressArchiveManifest.

    Raises:
        FileNotFoundError: If *manifest_path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If required manifest fields are absent or malformed.
        TypeError: If the top-level JSON value is not an object.
    """
    manifest_path = Path(manifest_path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    return manifest_from_dict(raw, root=root)


def write_manifest(
    manifest_path: Path,
    manifest: CongressArchiveManifest,
) -> Path:
    """Serialize *manifest* to JSON, resolving source paths relative to the file."""
    manifest_path = Path(manifest_path)
    raw = congress_archive_manifest_to_dict(manifest, root=manifest_path.parent)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(manifest_path, json.dumps(raw, sort_keys=True))
    return manifest_path


def manifest_file_metadata(value: object) -> dict[str, str] | None:
    """Receipt the exact manifest file, not a reserialized or expanded archive."""
    if value is None:
        return None
    path = Path(str(value))
    return {"path": str(path), "sha256": sha256_file(path)}


def validate_manifest_file_metadata(
    *,
    run_metadata: Mapping[str, object],
    issues: list[str],
    require_manifest: bool,
) -> None:
    """Check the retained Congress manifest receipt without changing its bytes."""
    manifest = run_metadata.get("congress_archive_manifest")
    if manifest is None:
        if require_manifest:
            issues.append("run_metadata congress_archive_manifest missing")
        return
    if not isinstance(manifest, dict):
        issues.append("run_metadata congress_archive_manifest must be an object")
        return
    manifest_path_raw = manifest.get("path")
    expected_sha = manifest.get("sha256")
    if not isinstance(manifest_path_raw, str) or not manifest_path_raw:
        issues.append("run_metadata congress_archive_manifest path missing")
        return
    if (
        not isinstance(expected_sha, str)
        or len(expected_sha) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha)
    ):
        issues.append("run_metadata congress_archive_manifest sha256 invalid")
        return
    manifest_path = Path(manifest_path_raw)
    if not manifest_path.is_file():
        issues.append(f"run_metadata congress_archive_manifest file not found: {manifest_path}")
        return
    actual_sha = sha256_file(manifest_path)
    if actual_sha != expected_sha:
        issues.append(
            "run_metadata congress_archive_manifest sha256 mismatch: "
            f"expected {expected_sha}, got {actual_sha}"
        )
