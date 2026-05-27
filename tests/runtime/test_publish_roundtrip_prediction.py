from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.export.contracts import SourceAnchor
from src.export.local_store import load_latest_manifest
from src.export.writer import prediction_readiness_path
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.contracts import (
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessPayload,
)
from src.runtime.publish_roundtrip_prediction import verify_published_prediction_roundtrip
from tests.support.published_snapshot_fixtures import make_member_profile, make_snapshot

_SNAPSHOT_DATE = date(2026, 1, 1)
_MOD = "src.runtime.publish_roundtrip_prediction"


def _ontology_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-prediction-roundtrip-001",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(
            node_type="member",
            node_id="P000197",
            label="Nancy Pelosi",
        ),
        object=OntologyNodeRef(
            node_type="committee",
            node_id="HSEC",
            label="Energy",
        ),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-prediction-roundtrip-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _committee_sector_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-prediction-roundtrip-002",
        edge_type="committee_sector_jurisdiction",
        subject=OntologyNodeRef(
            node_type="committee",
            node_id="HSEC",
            label="Energy",
        ),
        object=OntologyNodeRef(
            node_type="sector",
            node_id="energy",
            label="Energy",
        ),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-prediction-roundtrip-2",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee jurisdiction",
            )
        ],
    )


def _prediction_readiness() -> PredictionReadinessPayload:
    return PredictionReadinessPayload(
        snapshot_id="2026-01-01",
        snapshot_date=_SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=12,
        vote_cast_count=9,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=9,
            average_ontology_edges_per_member=2,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                vote_count=9,
                yea_count=6,
                nay_count=3,
                yea_rate=2 / 3,
                nay_rate=1 / 3,
                participation_rate=1,
                latest_vote_date=_SNAPSHOT_DATE,
                ontology_readiness_status="ready",
                ontology_edge_count=2,
                readiness_status="ready",
            )
        ],
    )


def _write_prediction_snapshot(tmp_path: Path):
    member_profiles = [make_member_profile(snapshot_date=_SNAPSHOT_DATE)]
    ontology_edges = [_ontology_edge(), _committee_sector_edge()]
    readiness = _prediction_readiness()
    make_snapshot(
        tmp_path,
        member_profiles=member_profiles,
        ontology_edges=ontology_edges,
        prediction_readiness=readiness,
    )
    return member_profiles, ontology_edges, readiness, load_latest_manifest(tmp_path)


def test_verify_published_prediction_roundtrip_passes_matching_payloads(
    tmp_path: Path,
) -> None:
    member_profiles, ontology_edges, readiness, manifest = _write_prediction_snapshot(tmp_path)

    with (
        patch(f"{_MOD}.fetch_current_member_slugs", return_value=["nancy-pelosi"]),
        patch(f"{_MOD}._build_member_profiles", return_value=member_profiles),
        patch(f"{_MOD}._build_ontology_edges", return_value=ontology_edges),
        patch(f"{_MOD}._build_prediction_readiness", return_value=readiness),
    ):
        result = verify_published_prediction_roundtrip(object(), tmp_path, manifest)

    assert result.ok is True
    assert result.checked > 0


def test_verify_published_prediction_roundtrip_reports_stale_readiness(
    tmp_path: Path,
) -> None:
    member_profiles, ontology_edges, readiness, manifest = _write_prediction_snapshot(tmp_path)
    readiness_file = tmp_path / prediction_readiness_path()
    payload = json.loads(readiness_file.read_text(encoding="utf-8"))
    payload["members"][0]["vote_count"] = 999
    readiness_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with (
        patch(f"{_MOD}.fetch_current_member_slugs", return_value=["nancy-pelosi"]),
        patch(f"{_MOD}._build_member_profiles", return_value=member_profiles),
        patch(f"{_MOD}._build_ontology_edges", return_value=ontology_edges),
        patch(f"{_MOD}._build_prediction_readiness", return_value=readiness),
    ):
        result = verify_published_prediction_roundtrip(object(), tmp_path, manifest)

    assert result.ok is False
    assert any("prediction payload mismatch" in issue.message for issue in result.issues)


def test_verify_published_prediction_roundtrip_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    member_profiles, ontology_edges, readiness, manifest = _write_prediction_snapshot(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-readiness.json"
    outside.write_text('{"escaped": true}', encoding="utf-8")
    readiness_file = tmp_path / prediction_readiness_path()
    readiness_file.unlink()
    readiness_file.symlink_to(outside)

    with (
        patch(f"{_MOD}.fetch_current_member_slugs", return_value=["nancy-pelosi"]),
        patch(f"{_MOD}._build_member_profiles", return_value=member_profiles),
        patch(f"{_MOD}._build_ontology_edges", return_value=ontology_edges),
        patch(f"{_MOD}._build_prediction_readiness", return_value=readiness),
    ):
        result = verify_published_prediction_roundtrip(object(), tmp_path, manifest)

    assert result.ok is False
    assert any(
        issue.path == prediction_readiness_path()
        and "cannot load published prediction artifact" in issue.message
        and "confined" in issue.message
        for issue in result.issues
    )


def test_verify_published_prediction_roundtrip_noop_without_prediction_artifacts(
    tmp_path: Path,
) -> None:
    # A snapshot with no prediction artifacts has nothing to verify.
    make_snapshot(tmp_path, member_profiles=[make_member_profile(snapshot_date=_SNAPSHOT_DATE)])
    manifest = load_latest_manifest(tmp_path)
    result = verify_published_prediction_roundtrip(object(), tmp_path, manifest)
    assert result.checked == 0
    assert result.ok is True


def test_verify_published_prediction_roundtrip_reports_db_assembly_failure(
    tmp_path: Path,
) -> None:
    member_profiles, ontology_edges, _readiness, manifest = _write_prediction_snapshot(tmp_path)
    with (
        patch(f"{_MOD}.fetch_current_member_slugs", return_value=["nancy-pelosi"]),
        patch(f"{_MOD}._build_member_profiles", return_value=member_profiles),
        patch(f"{_MOD}._build_ontology_edges", return_value=ontology_edges),
        patch(f"{_MOD}._build_prediction_readiness", side_effect=RuntimeError("db boom")),
    ):
        result = verify_published_prediction_roundtrip(object(), tmp_path, manifest)
    assert result.ok is False
    assert any(
        "failed to assemble prediction artifacts" in issue.message for issue in result.issues
    )
