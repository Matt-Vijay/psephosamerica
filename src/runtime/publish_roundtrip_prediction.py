"""Roundtrip verification for published prediction/simulation artifacts."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src.core.path_safety import safe_join_confined
from src.export.local_store import load_prediction_bootstrap
from src.export.manifest import SnapshotManifest
from src.export.writer import plan_snapshot
from src.pipeline.publish_snapshot_run import (
    _build_member_profiles,
    _build_ontology_edges,
    _build_prediction_readiness,
)
from src.query.published_rows import fetch_current_member_slugs
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "prediction"


def _issue(
    message: str,
    *,
    path: str | None = None,
    severity: IssueSeverity = "error",
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(
        stage=_STAGE,
        message=message,
        severity=severity,
        path=path,
    )


def verify_published_prediction_roundtrip(
    conn: Any,
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishRoundtripStageResult:
    """Verify published prediction artifacts against DB-derived inputs."""
    manifest_paths = _manifest_prediction_paths(manifest_payload)
    if not manifest_paths:
        return PublishRoundtripStageResult(stage=_STAGE, checked=0, issues=())

    try:
        expected_by_path = _expected_prediction_payloads(conn, manifest_payload)
    except Exception as exc:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=0,
            issues=(
                _issue(
                    f"failed to assemble prediction artifacts from DB rows: {exc}",
                ),
            ),
        )

    paths = sorted(set(manifest_paths) | set(expected_by_path))
    issues: list[PublishRoundtripIssue] = []
    checked = 0
    manifest_path_set = set(manifest_paths)

    for path in paths:
        expected = expected_by_path.get(path)
        if expected is None:
            issues.append(_issue("unexpected prediction artifact in manifest", path=path))
            checked += 1
            continue
        if path not in manifest_path_set:
            issues.append(_issue("expected prediction artifact missing from manifest", path=path))
            checked += 1
            continue

        try:
            published = _load_prediction_payload(root, path)
        except Exception as exc:
            issues.append(_issue(f"cannot load published prediction artifact: {exc}", path=path))
            checked += 1
            continue

        checked += 1
        if published != expected:
            issues.append(
                _issue("prediction payload mismatch between published artifact and DB", path=path)
            )

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=checked,
        issues=tuple(issues),
    )


def _manifest_prediction_paths(manifest_payload: SnapshotManifest) -> list[str]:
    return [
        entry.path
        for entry in manifest_payload.entries
        if entry.path == "prediction/bootstrap.json"
        or entry.path == "prediction/topology.json"
        or entry.path == "prediction/readiness.json"
        or entry.path == "prediction/index.json"
        or entry.path == "prediction/sources.json"
        or entry.path == "prediction/sectors.json"
        or entry.path == "prediction/committees.json"
        or entry.path.startswith("prediction/member-context/")
        or entry.path.startswith("prediction/members/")
        or entry.path.startswith("prediction/source-context/")
        or entry.path.startswith("prediction/sector-context/")
        or entry.path.startswith("prediction/committee-context/")
    ]


def _expected_prediction_payloads(
    conn: Any,
    manifest_payload: SnapshotManifest,
) -> dict[str, dict[str, Any]]:
    snapshot_date = _snapshot_date(root_payload=manifest_payload)
    member_slugs = fetch_current_member_slugs(conn)
    member_profiles = _build_member_profiles(conn, member_slugs)
    ontology_edges = _build_ontology_edges(conn)
    prediction_readiness = _build_prediction_readiness(
        conn,
        snapshot_id=manifest_payload.snapshot_id,
        snapshot_date=snapshot_date,
        member_profiles=member_profiles,
        ontology_edges=ontology_edges,
    )
    expected_files = plan_snapshot(
        snapshot_id=manifest_payload.snapshot_id,
        member_profiles=member_profiles,
        zip_feeds=[],
        evidence_cards=[],
        ontology_edges=ontology_edges,
        prediction_readiness=prediction_readiness,
        snapshot_date=snapshot_date,
    )
    payloads: dict[str, dict[str, Any]] = {}
    for planned in expected_files:
        if planned.path.startswith("prediction/"):
            payloads[planned.path] = _json_bytes_to_dict(planned.content)
    return payloads


def _snapshot_date(*, root_payload: SnapshotManifest) -> date:
    try:
        return date.fromisoformat(root_payload.snapshot_id)
    except ValueError:
        return root_payload.created_at.date()


def _json_bytes_to_dict(content: bytes) -> dict[str, Any]:
    import json

    raw = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError("expected prediction payload object")
    return raw


def _load_prediction_payload(root: Path, path: str) -> dict[str, Any]:
    if path == "prediction/bootstrap.json":
        return load_prediction_bootstrap(root).model_dump(mode="json")
    artifact = safe_join_confined(root, path, label="prediction artifact path")
    return _json_bytes_to_dict(artifact.read_bytes())
