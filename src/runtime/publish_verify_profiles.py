"""Verification pass for published member-profile artifacts.

Entry point: verify_local_member_profiles(root, manifest_payload)

Checks that every member profile referenced in the snapshot manifest:
  - exists on disk
  - parses as a valid MemberProfilePayload
  - has coherent identity fields (slug, bioguide_id, name)
"""

from __future__ import annotations

import re
from pathlib import Path

from src.export.local_store import load_member_profile
from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
)

_STAGE = "profiles"

# Matches paths written by src/export/writer.member_path()
_MEMBER_PATH_RE = re.compile(r"^members/(?P<slug>[^/]+)\.json$")


def _issue(
    message: str, *, path: str | None = None, severity: IssueSeverity = "error"
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def verify_local_member_profiles(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishVerifyStageResult:
    """Verify all member-profile artifacts referenced in *manifest_payload*.

    Args:
        root:             Root directory of the publish tree.
        manifest_payload: Parsed snapshot manifest whose entries are inspected.

    Returns:
        A :class:`PublishVerifyStageResult` for the ``"profiles"`` stage.
        ``ok`` is ``True`` when every referenced profile passes all checks.
    """
    issues: list[PublishVerifyIssue] = []
    checked = 0

    for entry in manifest_payload.entries:
        m = _MEMBER_PATH_RE.match(entry.path)
        if m is None:
            continue

        checked += 1
        slug_from_path = m.group("slug")
        artifact_path = root / entry.path

        # 1. File must exist.
        if not artifact_path.exists():
            issues.append(_issue(f"profile file missing: {entry.path}", path=entry.path))
            continue

        # 2. File must load and parse as a valid MemberProfilePayload.
        try:
            profile = load_member_profile(root, slug_from_path)
        except (FileNotFoundError, ValueError) as exc:
            issues.append(_issue(f"profile load failed: {exc}", path=entry.path))
            continue

        # 3. slug in the payload must match the slug encoded in the path.
        if profile.slug != slug_from_path:
            issues.append(
                _issue(
                    f"slug mismatch: path encodes '{slug_from_path}' but payload has '{profile.slug}'",
                    path=entry.path,
                )
            )

        # 4. bioguide_id must be a non-empty string.
        if not profile.bioguide_id or not profile.bioguide_id.strip():
            issues.append(_issue("bioguide_id is empty", path=entry.path))

        # 5. name must be a non-empty string with at least two whitespace-separated tokens
        #    (first-name-level and last-name-level shape check).
        name_parts = profile.name.split() if profile.name else []
        if len(name_parts) < 2:
            issues.append(
                _issue(
                    f"name '{profile.name}' does not have at least two parts",
                    path=entry.path,
                )
            )

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=checked,
        issues=tuple(issues),
    )
