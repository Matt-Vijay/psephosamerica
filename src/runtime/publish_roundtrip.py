"""Aggregate publish roundtrip verification.

Entry point: verify_publish_roundtrip(conn, root)

Composes eight verification stages in the fixed order:
    snapshot -> profiles -> evidence -> ontology -> prediction -> zip -> homepage -> lookup

The snapshot stage is implemented locally: it scans for the manifest,
validates its internal consistency, and loads it for the downstream stages.
The remaining seven stages delegate to their dedicated modules, each
receiving the live DB connection, the publish root, and the loaded
manifest.

The snapshot stage is limited to the manifest-backed artifact set. Homepage
coverage is established separately by the dedicated homepage stage.

If the manifest cannot be loaded after the snapshot stage, the seven
downstream stages are each returned as an error result so the caller
always receives a complete eight-stage PublishRoundtripResult.

No CLI here.  No lazy imports.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src.export.local_store import list_artifact_paths
from src.export.manifest import SnapshotManifest
from src.runtime.publish_roundtrip_evidence import verify_published_evidence_roundtrip
from src.runtime.publish_roundtrip_homepage import verify_published_homepage_roundtrip
from src.runtime.publish_roundtrip_lookup import (
    verify_published_current_member_lookup_roundtrip,
)
from src.runtime.publish_roundtrip_ontology import verify_published_ontology_roundtrip
from src.runtime.publish_roundtrip_prediction import verify_published_prediction_roundtrip
from src.runtime.publish_roundtrip_profiles import (
    verify_published_member_profiles_roundtrip,
)
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_roundtrip_zip import verify_published_zip_roundtrip
from src.runtime.publish_verify_manifest import inspect_local_manifest

_STAGE_SNAPSHOT = "snapshot"
_STAGE_PROFILES = "profiles"
_STAGE_EVIDENCE = "evidence"
_STAGE_ONTOLOGY = "ontology"
_STAGE_PREDICTION = "prediction"
_STAGE_ZIP = "zip"
_STAGE_HOMEPAGE = "homepage"
_STAGE_LOOKUP = "lookup"


# Internal helpers


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
    inspection = inspect_local_manifest(root)
    if inspection.manifest is None:
        if inspection.stage_result.issues:
            issues = tuple(
                PublishRoundtripIssue(
                    stage=_STAGE_SNAPSHOT,
                    message=issue.message,
                    severity=issue.severity,
                    path=issue.path,
                )
                for issue in inspection.stage_result.issues
            )
        else:
            issues = (
                PublishRoundtripIssue(
                    stage=_STAGE_SNAPSHOT,
                    message=inspection.reason,
                    severity="error",
                ),
            )
        return (
            PublishRoundtripStageResult(
                stage=_STAGE_SNAPSHOT,
                checked=inspection.stage_result.checked,
                issues=issues,
            ),
            None,
        )

    artifact_paths = list_artifact_paths(inspection.manifest)
    return (
        PublishRoundtripStageResult(
            stage=_STAGE_SNAPSHOT,
            checked=len(artifact_paths),
            issues=(),
        ),
        inspection.manifest,
    )


def _snapshot_date_from_manifest(manifest: SnapshotManifest) -> date:
    """Resolve the snapshot date used for DB-backed publish payload assembly."""
    try:
        return date.fromisoformat(manifest.snapshot_id)
    except ValueError:
        return manifest.created_at.date()


# Public entry point


def verify_publish_roundtrip(conn: Any, root: Path) -> PublishRoundtripResult:
    """Run all eight roundtrip stages against a local publish tree.

    Stage order is fixed:
    snapshot -> profiles -> evidence -> ontology -> prediction -> zip -> homepage -> lookup.

    If the manifest cannot be loaded after the snapshot stage the seven
    downstream stages are each returned as an error result so the caller
    always receives a complete eight-stage :class:`PublishRoundtripResult`.

    Args:
        conn: Live database connection forwarded to each stage verifier.
        root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishRoundtripResult` summarising all stage outcomes.
        Inspect ``.ok`` to determine whether the checked roundtrip stages are
        self-consistent against the live DB inputs.
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
                _unavailable_stage(_STAGE_ONTOLOGY, _NO_MANIFEST),
                _unavailable_stage(_STAGE_PREDICTION, _NO_MANIFEST),
                _unavailable_stage(_STAGE_ZIP, _NO_MANIFEST),
                _unavailable_stage(_STAGE_HOMEPAGE, _NO_MANIFEST),
                _unavailable_stage(_STAGE_LOOKUP, _NO_MANIFEST),
            )
        )

    # Stage 2: profiles
    profiles_stage = verify_published_member_profiles_roundtrip(conn, root, manifest)

    # Stage 3: evidence
    evidence_stage = verify_published_evidence_roundtrip(conn, root, manifest)

    # Stage 4: ontology
    ontology_stage = verify_published_ontology_roundtrip(conn, root, manifest)

    # Stage 5: prediction
    prediction_stage = verify_published_prediction_roundtrip(conn, root, manifest)

    # Stage 6: zip
    snapshot_date = _snapshot_date_from_manifest(manifest)

    zip_stage = verify_published_zip_roundtrip(conn, root, manifest, snapshot_date)

    # Stage 7: homepage
    homepage_stage = verify_published_homepage_roundtrip(conn, root, snapshot_date)

    # Stage 8: current-member lookup
    lookup_stage = verify_published_current_member_lookup_roundtrip(root, manifest)

    return PublishRoundtripResult(
        stages=(
            snapshot_stage,
            profiles_stage,
            evidence_stage,
            ontology_stage,
            prediction_stage,
            zip_stage,
            homepage_stage,
            lookup_stage,
        )
    )


# Stable alias consumed by commands.py.
verify_roundtrip = verify_publish_roundtrip
