"""Aggregate publish roundtrip verification.

Entry point: verify_publish_roundtrip(conn, root)

Composes five verification stages in the fixed order:
    snapshot -> profiles -> evidence -> zip -> homepage

The snapshot stage is implemented locally: it scans for the manifest,
validates its internal consistency, and loads it for the downstream stages.
The remaining four stages delegate to their dedicated modules, each
receiving the live DB connection, the publish root, and the loaded
manifest.

If the manifest cannot be loaded after the snapshot stage, the four
downstream stages are each returned as an error result so the caller
always receives a complete five-stage PublishRoundtripResult.

No CLI here.  No lazy imports.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.export.local_store import list_artifact_paths
from src.export.manifest import SnapshotManifest
from src.runtime.publish_roundtrip_evidence import verify_published_evidence_roundtrip
from src.runtime.publish_roundtrip_homepage import verify_published_homepage_roundtrip
from src.runtime.publish_roundtrip_profiles import (
    verify_published_member_profiles_roundtrip,
)
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_roundtrip_zip import verify_published_zip_roundtrip

_STAGE_SNAPSHOT = "snapshot"
_STAGE_PROFILES = "profiles"
_STAGE_EVIDENCE = "evidence"
_STAGE_ZIP = "zip"
_STAGE_HOMEPAGE = "homepage"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _unavailable_stage(stage: str, reason: str) -> PublishRoundtripStageResult:
    """Return an error stage result when a prerequisite could not be met."""
    return PublishRoundtripStageResult(
        stage=stage,
        checked=0,
        issues=(
            PublishRoundtripIssue(
                stage=stage,
                message=f"skipped: {reason}",
                severity="error",
            ),
        ),
    )


def _verify_snapshot(root: Path) -> tuple[PublishRoundtripStageResult, SnapshotManifest | None]:
    """Verify that exactly one manifest exists and is internally consistent.

    Uses ``list_artifact_paths`` to enumerate published artifacts for the
    checked count.  Returns both the stage result and the loaded manifest
    (``None`` when loading fails) so the caller can forward it to
    downstream stages.

    Args:
        root: Root directory of the published snapshot tree.

    Returns:
        A ``(stage_result, manifest_or_none)`` pair.
    """
    snapshots_dir = root / "snapshots"

    if not snapshots_dir.is_dir():
        return (
            PublishRoundtripStageResult(
                stage=_STAGE_SNAPSHOT,
                checked=0,
                issues=(
                    PublishRoundtripIssue(
                        stage=_STAGE_SNAPSHOT,
                        message="snapshots/ directory is missing",
                        severity="error",
                    ),
                ),
            ),
            None,
        )

    candidates = sorted(snapshots_dir.glob("*/manifest.json"))

    if not candidates:
        return (
            PublishRoundtripStageResult(
                stage=_STAGE_SNAPSHOT,
                checked=0,
                issues=(
                    PublishRoundtripIssue(
                        stage=_STAGE_SNAPSHOT,
                        message="no manifest.json found under snapshots/",
                        severity="error",
                    ),
                ),
            ),
            None,
        )

    if len(candidates) > 1:
        paths_str = ", ".join(str(c.relative_to(root)) for c in candidates)
        return (
            PublishRoundtripStageResult(
                stage=_STAGE_SNAPSHOT,
                checked=0,
                issues=(
                    PublishRoundtripIssue(
                        stage=_STAGE_SNAPSHOT,
                        message=f"expected exactly one manifest, found {len(candidates)}: {paths_str}",
                        severity="error",
                    ),
                ),
            ),
            None,
        )

    manifest_file = candidates[0]
    try:
        raw = json.loads(manifest_file.read_bytes())
        manifest = SnapshotManifest.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return (
            PublishRoundtripStageResult(
                stage=_STAGE_SNAPSHOT,
                checked=1,
                issues=(
                    PublishRoundtripIssue(
                        stage=_STAGE_SNAPSHOT,
                        message=f"manifest failed to load: {exc}",
                        severity="error",
                        path=str(manifest_file.relative_to(root)),
                    ),
                ),
            ),
            None,
        )

    artifact_paths = list_artifact_paths(manifest)
    return (
        PublishRoundtripStageResult(
            stage=_STAGE_SNAPSHOT,
            checked=len(artifact_paths),
            issues=(),
        ),
        manifest,
    )


def _snapshot_date_from_manifest(manifest: SnapshotManifest) -> date:
    """Resolve the snapshot date used for DB-backed publish payload assembly."""
    try:
        return date.fromisoformat(manifest.snapshot_id)
    except ValueError:
        return manifest.created_at.date()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_publish_roundtrip(conn: Any, root: Path) -> PublishRoundtripResult:
    """Run all five roundtrip stages against a local publish tree.

    Stage order is fixed: snapshot -> profiles -> evidence -> zip -> homepage.

    If the manifest cannot be loaded after the snapshot stage the four
    downstream stages are each returned as an error result so the caller
    always receives a complete five-stage :class:`PublishRoundtripResult`.

    Args:
        conn: Live database connection forwarded to each stage verifier.
        root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishRoundtripResult` summarising all stage outcomes.
        Inspect ``.ok`` to determine whether the roundtrip is sound.
    """
    # Stage 1: snapshot
    snapshot_stage, manifest = _verify_snapshot(root)

    if manifest is None:
        _NO_MANIFEST = "manifest could not be loaded"
        return PublishRoundtripResult(
            stages=(
                snapshot_stage,
                _unavailable_stage(_STAGE_PROFILES, _NO_MANIFEST),
                _unavailable_stage(_STAGE_EVIDENCE, _NO_MANIFEST),
                _unavailable_stage(_STAGE_ZIP, _NO_MANIFEST),
                _unavailable_stage(_STAGE_HOMEPAGE, _NO_MANIFEST),
            )
        )

    # Stage 2: profiles
    profiles_stage = verify_published_member_profiles_roundtrip(conn, root, manifest)

    # Stage 3: evidence
    evidence_stage = verify_published_evidence_roundtrip(conn, root, manifest)

    # Stage 4: zip
    snapshot_date = _snapshot_date_from_manifest(manifest)

    zip_stage = verify_published_zip_roundtrip(conn, root, manifest, snapshot_date)

    # Stage 5: homepage
    homepage_stage = verify_published_homepage_roundtrip(conn, root, snapshot_date)

    return PublishRoundtripResult(
        stages=(
            snapshot_stage,
            profiles_stage,
            evidence_stage,
            zip_stage,
            homepage_stage,
        )
    )


# Alias used by commands.py — keep until that import is updated.
verify_roundtrip = verify_publish_roundtrip
