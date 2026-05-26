"""Load a Congress archive manifest from a JSON file.

Thin I/O layer: reads the file, parses JSON, resolves relative paths
against the manifest file's parent directory, and delegates to
manifest_from_dict() for validation and typed construction.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

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


def _write_text_atomic(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
