"""Manifest verification for a local publish tree.

Entry point: verify_local_manifest(root)

Scans root/snapshots/*/manifest.json and verifies each file found.

Checks performed per manifest:
  1. File loads as valid JSON.
  2. Required top-level keys are all present.
  3. snapshot_id is a non-empty string.
  4. created_at is a non-empty string.
  5. entries is a list; each entry has path, sha256, and size_bytes.
  6. Pydantic schema validation passes.
  7. total_files and total_bytes match the entries list.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_STAGE = "manifest"
_REQUIRED_KEYS = frozenset({"snapshot_id", "created_at", "entries", "total_files", "total_bytes"})
_REQUIRED_ENTRY_KEYS = frozenset({"path", "sha256", "size_bytes"})


def _issue(
    message: str, *, path: str | None = None, severity: IssueSeverity = "error"
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def _check_manifest_file(
    manifest_file: Path,
) -> tuple[list[PublishVerifyIssue], SnapshotManifest | None]:
    """Run all checks against one manifest file.

    Returns a (issues, manifest_or_None) pair.  manifest_or_None is set only
    when all checks pass.
    """
    issues: list[PublishVerifyIssue] = []

    # 1. Read and parse JSON.
    try:
        raw_bytes = manifest_file.read_bytes()
    except OSError as exc:
        issues.append(_issue(f"cannot read manifest file: {exc}"))
        return issues, None

    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        issues.append(_issue(f"manifest is not valid JSON: {exc}"))
        return issues, None

    if not isinstance(raw, dict):
        issues.append(_issue("manifest root is not a JSON object"))
        return issues, None

    # 2. Required top-level keys.
    missing = _REQUIRED_KEYS - raw.keys()
    if missing:
        issues.append(_issue(f"missing required keys: {sorted(missing)}"))
        return issues, None

    # 3. snapshot_id non-empty.
    sid = raw.get("snapshot_id", "")
    if not sid or not str(sid).strip():
        issues.append(_issue("snapshot_id is empty"))

    # 4. created_at non-empty.
    cat = raw.get("created_at", "")
    if not cat or not str(cat).strip():
        issues.append(_issue("created_at is empty"))

    # 5. entries broad shape.
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        issues.append(_issue("entries is not a list"))
    else:
        for i, entry in enumerate(entries_raw):
            if not isinstance(entry, dict):
                issues.append(_issue(f"entry[{i}] is not an object"))
                continue
            for key in sorted(_REQUIRED_ENTRY_KEYS):
                if key not in entry:
                    issues.append(_issue(f"entry[{i}] missing key: {key!r}"))
            if "path" in entry and isinstance(entry["path"], str):
                if not path_is_confined(entry["path"]):
                    issues.append(
                        _issue(f"entry[{i}] path escapes publish root: {entry['path']!r}")
                    )

    if issues:
        return issues, None

    # 6. Pydantic schema validation.
    try:
        manifest = SnapshotManifest.model_validate(raw)
    except Exception as exc:
        issues.append(_issue(f"manifest schema validation failed: {exc}"))
        return issues, None

    # 7. Count consistency.
    if not manifest.verify_counts():
        computed = sum(e.size_bytes for e in manifest.entries)
        issues.append(
            _issue(
                f"count mismatch: total_files={manifest.total_files} "
                f"entries={len(manifest.entries)} "
                f"total_bytes={manifest.total_bytes} "
                f"sum_size_bytes={computed}"
            )
        )

    return issues, (manifest if not issues else None)


def verify_local_manifest(root: Path) -> PublishVerifyStageResult:
    """Verify all snapshot manifests found under *root/snapshots/*/manifest.json*.

    Args:
        root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishVerifyStageResult` for the ``"manifest"`` stage.
        ``checked`` counts the number of manifest files attempted.
        Returns ok with ``checked=0`` when no snapshots directory or no
        manifest files are found.
    """
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.is_dir():
        return PublishVerifyStageResult(stage=_STAGE, checked=0, issues=())

    manifest_files = sorted(snapshots_dir.glob("*/manifest.json"))
    if not manifest_files:
        return PublishVerifyStageResult(stage=_STAGE, checked=0, issues=())

    all_issues: list[PublishVerifyIssue] = []
    for mf in manifest_files:
        file_issues, _ = _check_manifest_file(mf)
        all_issues.extend(file_issues)

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=len(manifest_files),
        issues=tuple(all_issues),
    )
