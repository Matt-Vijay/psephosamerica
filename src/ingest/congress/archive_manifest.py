"""Load a Congress archive manifest from a JSON file.

Thin I/O layer: reads the file, parses JSON, resolves relative paths
against the manifest file's parent directory, and delegates to
manifest_from_dict() for validation and typed construction.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.ingest.congress.archive import CongressArchiveManifest, manifest_from_dict


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
