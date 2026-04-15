"""Aggregate publish verification.

Entry point: verify_local_publish(root)

Composes four verification stages in the fixed order:
    manifest -> profiles -> evidence -> zip

The manifest is discovered by scanning root/snapshots/*/manifest.json.
When exactly one manifest is found it is loaded and forwarded to the three
downstream stage verifiers.  If none or multiple manifests are found, or if
loading fails, the downstream stages are each returned as an error result.

No CLI here.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_evidence import verify_local_evidence_cards
from src.runtime.publish_verify_manifest import verify_local_manifest
from src.runtime.publish_verify_profiles import verify_local_member_profiles
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)
from src.runtime.publish_verify_zip import verify_local_zip_feeds

_STAGE_MANIFEST = "manifest"
_STAGE_PROFILES = "profiles"
_STAGE_EVIDENCE = "evidence"
_STAGE_ZIP = "zip"


def _find_and_load_manifest(root: Path) -> SnapshotManifest | None:
    """Scan root/snapshots/*/manifest.json and load the single manifest found.

    Returns None if no manifest exists, multiple manifests exist, or loading
    fails.  Errors are silently swallowed here; the manifest stage verifier
    already records them as PublishVerifyIssues.
    """
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.is_dir():
        return None

    candidates = sorted(snapshots_dir.glob("*/manifest.json"))
    if len(candidates) != 1:
        return None

    try:
        raw = json.loads(candidates[0].read_bytes())
        return SnapshotManifest.model_validate(raw)
    except Exception:
        return None


def _unavailable_stage(stage: str, reason: str) -> PublishVerifyStageResult:
    """Return an error stage result when a prerequisite failed."""
    issue = PublishVerifyIssue(
        stage=stage,
        message=f"skipped: {reason}",
        severity="error",
    )
    return PublishVerifyStageResult(stage=stage, checked=0, issues=(issue,))


def verify_local_publish(root: Path) -> PublishVerifyResult:
    """Run all four verification stages against a local publish tree.

    Stage order is fixed: manifest -> profiles -> evidence -> zip.

    If the manifest cannot be loaded after the manifest stage, the
    remaining three stages are each recorded as an error result so that
    the caller always receives a complete four-stage ``PublishVerifyResult``.

    Args:
        root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishVerifyResult` summarising all stage outcomes.
        Inspect ``.ok`` to determine whether the publish tree is sound.
    """
    # Stage 1: manifest
    manifest_stage = verify_local_manifest(root)

    # Load the manifest so subsequent stages can inspect its entries.
    manifest: SnapshotManifest | None = _find_and_load_manifest(root)

    if manifest is None:
        _NO_MANIFEST = "manifest could not be loaded"
        return PublishVerifyResult(
            stages=(
                manifest_stage,
                _unavailable_stage(_STAGE_PROFILES, _NO_MANIFEST),
                _unavailable_stage(_STAGE_EVIDENCE, _NO_MANIFEST),
                _unavailable_stage(_STAGE_ZIP, _NO_MANIFEST),
            )
        )

    # Stage 2: profiles
    profiles_stage = verify_local_member_profiles(root, manifest)

    # Stage 3: evidence
    evidence_stage = verify_local_evidence_cards(root, manifest)

    # Stage 4: zip
    zip_stage = verify_local_zip_feeds(root, manifest)

    return PublishVerifyResult(
        stages=(
            manifest_stage,
            profiles_stage,
            evidence_stage,
            zip_stage,
        )
    )
