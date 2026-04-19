"""Aggregate local publish verification.

Entry point: verify_local_publish(root)

Composes four verification stages in the fixed order:
    manifest -> profiles -> evidence -> zip

The manifest is discovered by scanning root/snapshots/*/manifest.json.
When exactly one manifest is found it is loaded and forwarded to the three
downstream stage verifiers.  If none or multiple manifests are found, or if
loading fails, the downstream stages are each returned as an error result
with an explicit reason.

This local verifier establishes manifest-backed self-consistency for the
published snapshot artifacts it knows how to check. Files outside that local
coverage may be surfaced as warnings rather than claimed as verified.

No CLI here.
"""
from __future__ import annotations

from pathlib import Path

from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_evidence import verify_local_evidence_cards
from src.runtime.publish_verify_manifest import inspect_local_manifest, verify_local_manifest
from src.runtime.publish_verify_profiles import verify_local_member_profiles
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)
from src.runtime.publish_verify_zip import verify_local_zip_feeds


def _find_and_load_manifest(root: Path) -> tuple[SnapshotManifest | None, str]:
    inspection = inspect_local_manifest(root)
    return inspection.manifest, inspection.reason


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
        Inspect ``.ok`` to determine whether the manifest-backed local publish
        set is self-consistent.
    """
    # Stage 1: manifest
    manifest_stage = verify_local_manifest(root)

    # Load the manifest so subsequent stages can inspect its entries.
    manifest, skip_reason = _find_and_load_manifest(root)

    if manifest is None:
        return PublishVerifyResult(
            stages=(
                manifest_stage,
                _unavailable_stage("profiles", skip_reason),
                _unavailable_stage("evidence", skip_reason),
                _unavailable_stage("zip", skip_reason),
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
