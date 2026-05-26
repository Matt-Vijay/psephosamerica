"""Verify zip-feed artifacts in a published snapshot tree.

Entry point: verify_local_zip_feeds(root, manifest_payload)

ZIP feeds are optional.  When the manifest contains no zip entries the
function returns an ok stage result with zero items checked.

For each zip entry that *is* present the verifier checks:
  - the entry path is confined to the publish root
  - the file loads as valid JSON and parses as ZipFeedPayload
  - zip_code in the payload matches the filename
  - members field is a list (schema guarantees this; the check is explicit)
"""

from __future__ import annotations

from pathlib import Path

from src.export.manifest import SnapshotManifest
from src.runtime.inspect import load_local_zip_feed
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_STAGE = "zip"
_ZIP_PREFIX = "zip/"
_ZIP_SUFFIX = ".json"


def _issue(
    message: str, *, path: str | None = None, severity: IssueSeverity = "error"
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def _zip_code_from_path(path: str) -> str | None:
    """Extract the zip code from a manifest path like ``zip/90210.json``."""
    if path.startswith(_ZIP_PREFIX) and path.endswith(_ZIP_SUFFIX):
        name = path[len(_ZIP_PREFIX) : -len(_ZIP_SUFFIX)]
        if name:
            return name
    return None


def verify_local_zip_feeds(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishVerifyStageResult:
    """Verify zip-feed files referenced in *manifest_payload* under *root*.

    Parameters
    ----------
    root:
        Publish snapshot root directory.
    manifest_payload:
        Parsed manifest for the snapshot being verified.

    Returns
    -------
    PublishVerifyStageResult
        Stage name is ``"zip"``.  ``checked`` counts files that were
        attempted.  When no zip entries exist, returns ok with ``checked=0``.
    """
    zip_entries = [
        (e.path, code)
        for e in manifest_payload.entries
        if (code := _zip_code_from_path(e.path)) is not None
    ]

    if not zip_entries:
        return PublishVerifyStageResult(stage=_STAGE, checked=0, issues=())

    issues: list[PublishVerifyIssue] = []

    for path, zip_code in zip_entries:
        if not path_is_confined(path):
            issues.append(_issue("entry path escapes publish root", path=path))
            continue

        try:
            feed = load_local_zip_feed(zip_code, snapshot_root=root)
        except FileNotFoundError:
            issues.append(_issue(f"zip feed file missing: {path}", path=path))
            continue
        except Exception as exc:
            issues.append(_issue(f"zip feed failed to load ({path}): {exc}", path=path))
            continue

        if feed.zip_code != zip_code:
            issues.append(
                _issue(
                    f"zip_code mismatch in {path}: "
                    f"filename={zip_code!r}, payload={feed.zip_code!r}",
                    path=path,
                )
            )

        if not isinstance(feed.members, list):
            issues.append(_issue(f"members field is not a list in {path}", path=path))

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=len(zip_entries),
        issues=tuple(issues),
    )
