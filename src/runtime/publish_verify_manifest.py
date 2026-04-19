"""Manifest verification for a local publish tree.

Entry point: verify_local_manifest(root)

Scans root/snapshots/*/manifest.json and verifies the single manifest that
anchors the publish tree.

Checks performed per manifest:
  1. File loads as valid JSON.
  2. Required top-level keys are all present.
  3. snapshot_id is a non-empty string.
  4. created_at is a non-empty string.
  5. entries is a list; each entry has path, sha256, and size_bytes.
  6. Pydantic schema validation passes.
  7. total_files and total_bytes match the entries list.
  8. Every locally managed snapshot artifact under members/, evidence/, and zip/
     is listed exactly once.
  9. Every manifest-listed file matches its recorded size/hash.
 10. root_sha256 matches the observed digest of the manifest-listed artifact set.
 11. Other files under the publish root are reported as outside local
     verification coverage unless the manifest explicitly lists them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.export.builders import sha256_hex
from src.export.manifest import SnapshotManifest, manifest_root_sha256
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_STAGE = "manifest"
_REQUIRED_KEYS = frozenset(
    {"snapshot_id", "created_at", "entries", "total_files", "total_bytes", "root_sha256"}
)
_REQUIRED_ENTRY_KEYS = frozenset({"path", "sha256", "size_bytes"})


@dataclass(frozen=True)
class ManifestInspection:
    stage_result: PublishVerifyStageResult
    manifest: SnapshotManifest | None
    reason: str


def _issue(
    message: str, *, path: str | None = None, severity: IssueSeverity = "error"
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def _is_manifest_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return len(pure.parts) == 3 and pure.parts[0] == "snapshots" and pure.parts[2] == "manifest.json"


def _is_locally_managed_artifact_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(pure.parts) and pure.parts[0] in {"members", "evidence", "zip"}


def _actual_manifest_records(
    root: Path,
    manifest: SnapshotManifest,
) -> tuple[dict[str, dict[str, int | str]], list[PublishVerifyIssue]]:
    records: dict[str, dict[str, int | str]] = {}
    issues: list[PublishVerifyIssue] = []

    for entry in manifest.entries:
        if not path_is_confined(entry.path):
            continue
        file = root / entry.path
        if not file.is_file():
            continue
        try:
            data = file.read_bytes()
        except OSError as exc:
            issues.append(_issue(f"cannot read manifest-listed file: {exc}", path=entry.path))
            continue
        records[entry.path] = {
            "path": entry.path,
            "sha256": sha256_hex(data),
            "size_bytes": len(data),
        }

    return records, issues


def _scan_unclaimed_files(
    root: Path,
    manifest_paths: set[str],
) -> list[PublishVerifyIssue]:
    issues: list[PublishVerifyIssue] = []

    for file in root.rglob("*"):
        if not file.is_file():
            continue
        rel = file.relative_to(root).as_posix()
        if not path_is_confined(rel) or _is_manifest_path(rel) or rel in manifest_paths:
            continue
        if _is_locally_managed_artifact_path(rel):
            issues.append(_issue("managed file not listed in manifest", path=rel))
            continue
        issues.append(
            _issue(
                "file exists outside local verification coverage; not claimed as verified",
                path=rel,
                severity="warning",
            )
        )

    return issues


def _check_manifest_file(
    manifest_file: Path,
) -> tuple[list[PublishVerifyIssue], SnapshotManifest | None]:
    """Run all checks against one manifest file.

    Returns a (issues, manifest_or_None) pair.  manifest_or_None is set only
    when all checks pass.
    """
    issues: list[PublishVerifyIssue] = []

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

    missing = _REQUIRED_KEYS - raw.keys()
    if missing:
        issues.append(_issue(f"missing required keys: {sorted(missing)}"))
        return issues, None

    sid = raw.get("snapshot_id", "")
    if not sid or not str(sid).strip():
        issues.append(_issue("snapshot_id is empty"))

    cat = raw.get("created_at", "")
    if not cat or not str(cat).strip():
        issues.append(_issue("created_at is empty"))

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
            if "path" in entry and isinstance(entry["path"], str) and not path_is_confined(entry["path"]):
                issues.append(_issue(f"entry[{i}] path escapes publish root: {entry['path']!r}"))

    if issues:
        return issues, None

    try:
        manifest = SnapshotManifest.model_validate(raw)
    except Exception as exc:
        issues.append(_issue(f"manifest schema validation failed: {exc}"))
        return issues, None

    if not manifest.verify_counts():
        computed = sum(entry.size_bytes for entry in manifest.entries)
        issues.append(
            _issue(
                f"count mismatch: total_files={manifest.total_files} "
                f"entries={len(manifest.entries)} "
                f"total_bytes={manifest.total_bytes} "
                f"sum_size_bytes={computed}"
            )
        )

    seen_paths: set[str] = set()
    duplicate_paths: set[str] = set()
    manifest_paths: set[str] = set()

    for entry in manifest.entries:
        if entry.path in seen_paths:
            duplicate_paths.add(entry.path)
        seen_paths.add(entry.path)
        manifest_paths.add(entry.path)

    root = manifest_file.parents[2]
    actual_records, record_issues = _actual_manifest_records(root, manifest)
    issues.extend(record_issues)

    complete_manifest_records = len(actual_records) == len(manifest_paths)

    for entry in manifest.entries:
        actual_record = actual_records.get(entry.path)
        if actual_record is None:
            issues.append(_issue("manifest entry file missing", path=entry.path))
            continue

        actual_size = int(actual_record["size_bytes"])
        if actual_size != entry.size_bytes:
            issues.append(
                _issue(
                    f"size mismatch: manifest={entry.size_bytes} actual={actual_size}",
                    path=entry.path,
                )
            )

        actual_hash = str(actual_record["sha256"])
        if actual_hash != entry.sha256:
            issues.append(
                _issue(
                    f"sha256 mismatch: manifest={entry.sha256} actual={actual_hash}",
                    path=entry.path,
                )
            )

    for duplicate_path in sorted(duplicate_paths):
        issues.append(_issue("duplicate manifest entry path", path=duplicate_path))

    issues.extend(_scan_unclaimed_files(root, manifest_paths))

    if complete_manifest_records:
        actual_root_sha256 = manifest_root_sha256(list(actual_records.values()))
        if actual_root_sha256 != manifest.root_sha256:
            issues.append(
                _issue(
                    f"root_sha256 mismatch: manifest={manifest.root_sha256} actual={actual_root_sha256}"
                )
            )

    has_errors = any(issue.severity == "error" for issue in issues)
    return issues, (manifest if not has_errors else None)


def inspect_local_manifest(root: Path) -> ManifestInspection:
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.is_dir():
        return ManifestInspection(
            stage_result=PublishVerifyStageResult(stage=_STAGE, checked=0, issues=()),
            manifest=None,
            reason="no snapshots directory",
        )

    manifest_files = sorted(snapshots_dir.glob("*/manifest.json"))
    if not manifest_files:
        return ManifestInspection(
            stage_result=PublishVerifyStageResult(stage=_STAGE, checked=0, issues=()),
            manifest=None,
            reason="no manifest files found",
        )

    if len(manifest_files) != 1:
        issue = _issue(f"expected exactly one manifest, found {len(manifest_files)}")
        return ManifestInspection(
            stage_result=PublishVerifyStageResult(
                stage=_STAGE,
                checked=len(manifest_files),
                issues=(issue,),
            ),
            manifest=None,
            reason=issue.message,
        )

    file_issues, manifest = _check_manifest_file(manifest_files[0])
    stage_result = PublishVerifyStageResult(
        stage=_STAGE,
        checked=1,
        issues=tuple(file_issues),
    )
    reason = file_issues[0].message if file_issues else ""
    return ManifestInspection(stage_result=stage_result, manifest=manifest, reason=reason)


def verify_local_manifest(root: Path) -> PublishVerifyStageResult:
    """Verify the manifest anchoring a local publish tree."""
    return inspect_local_manifest(root).stage_result
