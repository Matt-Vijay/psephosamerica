from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from src.export.contracts import SourceAnchor
from src.export.local_store import load_latest_manifest
from src.export.manifest import ManifestEntry
from src.export.writer import (
    prediction_bootstrap_path,
    prediction_committee_context_path,
    prediction_committee_readiness_path,
    prediction_member_context_path,
    prediction_readiness_index_path,
    prediction_member_readiness_path,
    prediction_readiness_path,
    prediction_sector_readiness_path,
    prediction_sector_context_path,
    prediction_source_index_path,
    prediction_topology_path,
)
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.contracts import (
    PredictionJurisdictionCapabilityPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessPayload,
)
from src.runtime.publish_verify_prediction import verify_local_prediction_artifacts
from tests.support.published_snapshot_fixtures import make_snapshot

_SNAPSHOT_DATE = date(2026, 1, 1)


def _ontology_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-prediction-verify-001",
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
                source_id="cm-prediction-verify-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _committee_sector_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-prediction-verify-002",
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
                source_id="cm-prediction-verify-2",
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
        jurisdictions=[
            PredictionJurisdictionCapabilityPayload(
                jurisdiction_id="us_congress",
                implementation_status="implemented",
                legislative_body_ids=["us_congress_house"],
                required_source_roles=["bills", "members", "source_anchors", "votes"],
            )
        ],
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


def test_verify_local_prediction_artifacts_passes_valid_prediction_tree(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is True
    assert result.checked > 0


def test_verify_local_prediction_artifacts_is_ok_when_artifacts_absent(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    manifest = load_latest_manifest(tmp_path)

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 0


def test_verify_local_prediction_artifacts_reports_topology_count_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["source_count"] = 999
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "source_count does not match source index" in issue.message for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_topology_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["snapshot_id"] = "stale-snapshot"
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction topology snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_topology_jurisdiction_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["jurisdiction_count"] = 999
    topology["implemented_jurisdiction_count"] = 999
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "jurisdiction_count does not match readiness" in issue.message for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_topology_jurisdiction_id_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["legislative_body_ids"] = ["us_congress:us_congress_senate"]
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "legislative_body_ids does not match readiness" in issue.message for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_bootstrap_jurisdiction_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    bootstrap_file = tmp_path / prediction_bootstrap_path()
    bootstrap = json.loads(bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["jurisdictions"][0]["legislative_body_ids"] = ["us_congress_senate"]
    bootstrap_file.write_text(json.dumps(bootstrap, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction bootstrap jurisdictions do not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_bootstrap_summary_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    bootstrap_file = tmp_path / prediction_bootstrap_path()
    bootstrap = json.loads(bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["ready_member_count"] = 0
    bootstrap_file.write_text(json.dumps(bootstrap, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction bootstrap ready_member_count does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_bootstrap_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    bootstrap_file = tmp_path / prediction_bootstrap_path()
    bootstrap = json.loads(bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["snapshot_id"] = "stale-snapshot"
    bootstrap_file.write_text(json.dumps(bootstrap, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction bootstrap snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_requires_bootstrap_topology_path_when_published(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    bootstrap_file = tmp_path / prediction_bootstrap_path()
    bootstrap = json.loads(bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["topology_path"] = None
    bootstrap_file.write_text(json.dumps(bootstrap, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction bootstrap topology_path missing while topology artifact is published"
        in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_bootstrap_noncanonical_role_path(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    bootstrap_file = tmp_path / prediction_bootstrap_path()
    bootstrap = json.loads(bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["readiness_path"] = prediction_readiness_index_path()
    bootstrap_file.write_text(json.dumps(bootstrap, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction bootstrap readiness_path is not canonical" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_requires_readiness_jurisdictions(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    readiness_file = tmp_path / prediction_readiness_path()
    readiness = json.loads(readiness_file.read_text(encoding="utf-8"))
    readiness["jurisdictions"] = []
    readiness_file.write_text(json.dumps(readiness, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction readiness must declare jurisdiction capabilities" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_context_missing_from_manifest(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_context_path = next(
        entry.path
        for entry in manifest.entries
        if entry.path.startswith("prediction/source-context/")
    )
    stale_manifest = manifest.model_copy(
        update={
            "entries": [entry for entry in manifest.entries if entry.path != source_context_path],
            "total_files": manifest.total_files - 1,
        }
    )

    result = verify_local_prediction_artifacts(tmp_path, stale_manifest)

    assert result.ok is False
    assert any(
        "source index context path is not listed in manifest" in issue.message
        and source_context_path in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_ref_missing_from_manifest(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_profile_path = "members/nancy-pelosi.json"
    stale_manifest = manifest.model_copy(
        update={
            "entries": [entry for entry in manifest.entries if entry.path != member_profile_path],
            "total_files": manifest.total_files - 1,
        }
    )

    result = verify_local_prediction_artifacts(tmp_path, stale_manifest)

    assert result.ok is False
    assert any(
        "member context artifact ref is not listed in manifest" in issue.message
        and member_profile_path in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_source_backlink_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["member_count"] = 0
    source_index["sources"][0]["member_bioguide_ids"] = []
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context["members"] = []
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context source context does not include member" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_source_missing_from_index(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    omitted_source_key = source_index["sources"][0]["source_key"]
    source_index["sources"] = source_index["sources"][1:]
    source_index["source_count"] = len(source_index["sources"])
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["source_count"] = source_index["source_count"]
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context source is not listed in source index" in issue.message
        and omitted_source_key in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_canonical_ref_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_context_file = tmp_path / prediction_member_context_path("P000197")
    member_context = json.loads(member_context_file.read_text(encoding="utf-8"))
    member_context["artifact_refs"]["prediction_member_readiness_path"] = (
        prediction_member_context_path("P000197")
    )
    member_context_file.write_text(json.dumps(member_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context artifact_refs prediction_member_readiness_path is not canonical"
        in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_readiness_index_member_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    readiness_index_file = tmp_path / prediction_readiness_index_path()
    readiness_index = json.loads(readiness_index_file.read_text(encoding="utf-8"))
    readiness_index["members_by_bioguide"]["P000197"]["vote_count"] = 8
    readiness_index["members_by_bioguide"]["P000197"]["yea_count"] = 5
    readiness_index["members_by_bioguide"]["P000197"]["yea_rate"] = 5 / 8
    readiness_index["members_by_bioguide"]["P000197"]["nay_rate"] = 3 / 8
    readiness_index_file.write_text(json.dumps(readiness_index, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction readiness index member does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_readiness_file_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_readiness_file = tmp_path / prediction_member_readiness_path("P000197")
    member_readiness = json.loads(member_readiness_file.read_text(encoding="utf-8"))
    member_readiness["vote_count"] = 8
    member_readiness["yea_count"] = 5
    member_readiness["yea_rate"] = 5 / 8
    member_readiness["nay_rate"] = 3 / 8
    member_readiness_file.write_text(json.dumps(member_readiness, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member readiness file does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_readiness_path_id_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_readiness_file = tmp_path / prediction_member_readiness_path("P000197")
    member_readiness = json.loads(member_readiness_file.read_text(encoding="utf-8"))
    member_readiness["bioguide_id"] = "A000001"
    member_readiness_file.write_text(json.dumps(member_readiness, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member readiness path does not match embedded bioguide_id" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_readiness_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_context_file = tmp_path / prediction_member_context_path("P000197")
    member_context = json.loads(member_context_file.read_text(encoding="utf-8"))
    member_context["member_readiness"]["vote_count"] = 8
    member_context["member_readiness"]["yea_count"] = 5
    member_context["member_readiness"]["yea_rate"] = 5 / 8
    member_context["member_readiness"]["nay_rate"] = 3 / 8
    member_context_file.write_text(json.dumps(member_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context readiness does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_path_id_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_context_file = tmp_path / prediction_member_context_path("P000197")
    member_context = json.loads(member_context_file.read_text(encoding="utf-8"))
    member_context["member_bioguide_id"] = "A000001"
    member_context["member_readiness"]["bioguide_id"] = "A000001"
    if member_context.get("ontology_features") is not None:
        member_context["ontology_features"]["member_bioguide_id"] = "A000001"
    member_context_file.write_text(json.dumps(member_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context path does not match embedded bioguide_id" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_member_context_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_context_file = tmp_path / prediction_member_context_path("P000197")
    member_context = json.loads(member_context_file.read_text(encoding="utf-8"))
    member_context["snapshot_id"] = "stale-snapshot"
    member_context_file.write_text(json.dumps(member_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction member context snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_readiness_index_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    readiness_index_file = tmp_path / prediction_readiness_index_path()
    readiness_index = json.loads(readiness_index_file.read_text(encoding="utf-8"))
    readiness_index["snapshot_id"] = "stale-snapshot"
    readiness_index_file.write_text(json.dumps(readiness_index, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction readiness index snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_context_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_index["sources"][0]["sector_ids"] = []
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index row does not match source context" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_index["snapshot_id"] = "stale-snapshot"
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_context_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_context_path = next(
        entry.path
        for entry in manifest.entries
        if entry.path.startswith("prediction/source-context/")
    )
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["snapshot_id"] = "stale-snapshot"
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source context snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_misnamed_source_context_path(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_context_path = next(
        entry.path
        for entry in manifest.entries
        if entry.path.startswith("prediction/source-context/")
    )
    wrong_path = "prediction/source-context/abcdefabcdefabcdefabcdef.json"
    wrong_file = tmp_path / wrong_path
    wrong_file.parent.mkdir(parents=True, exist_ok=True)
    wrong_file.write_text(
        (tmp_path / source_context_path).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    wrong_entry = ManifestEntry(
        path=wrong_path,
        sha256=hashlib.sha256(wrong_file.read_bytes()).hexdigest(),
        size_bytes=wrong_file.stat().st_size,
    )
    stale_manifest = manifest.model_copy(
        update={
            "entries": [*manifest.entries, wrong_entry],
            "total_files": manifest.total_files + 1,
            "total_bytes": manifest.total_bytes + wrong_entry.size_bytes,
        }
    )

    result = verify_local_prediction_artifacts(tmp_path, stale_manifest)

    assert result.ok is False
    assert any(
        "prediction source context path does not match embedded source_key" in issue.message
        and issue.path == wrong_path
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_context_member_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_context_path = next(
        entry.path
        for entry in manifest.entries
        if entry.path.startswith("prediction/source-context/")
    )
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["members"][0]["context_reasons"] = ["stale_context"]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source context member does not match member context" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_sector_not_in_readiness(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["sector_ids"] = ["energy", "unregistered-sector"]
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index sector is not listed in sector readiness" in issue.message
        and "unregistered-sector" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_committee_not_in_readiness(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["committee_ids"] = ["HSEC", "UNREGISTERED"]
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index committee is not listed in committee readiness" in issue.message
        and "UNREGISTERED" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_member_not_in_readiness(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["member_bioguide_ids"] = ["P000197", "Z999999"]
    source_index["sources"][0]["member_count"] = 2
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index member is not listed in readiness" in issue.message
        and "Z999999" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_source_index_member_without_source(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    member_context_file = tmp_path / prediction_member_context_path("P000197")
    member_context = json.loads(member_context_file.read_text(encoding="utf-8"))
    removed_source_key = member_context["source_keys"].pop()
    member_context["source_anchors"].pop()
    member_context["source_context_paths"].pop()
    member_context_file.write_text(json.dumps(member_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction source index member does not reference source" in issue.message
        and "P000197" in issue.message
        for issue in result.issues
    )
    assert removed_source_key


def test_verify_local_prediction_artifacts_reports_sector_readiness_context_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    sector_readiness_file = tmp_path / prediction_sector_readiness_path()
    sector_readiness = json.loads(sector_readiness_file.read_text(encoding="utf-8"))
    sector_readiness["sectors"][0]["source_count"] = 0
    sector_readiness["sectors"][0]["source_keys"] = []
    sector_readiness["sectors"][0]["source_context_paths"] = []
    sector_readiness_file.write_text(json.dumps(sector_readiness, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector readiness row does not match sector context" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_source_backlink_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["sector_ids"] = []
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector readiness source context does not include sector" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_source_missing_from_index(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    omitted_source_key = source_index["sources"][0]["source_key"]
    source_index["sources"] = source_index["sources"][1:]
    source_index["source_count"] = len(source_index["sources"])
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["source_count"] = source_index["source_count"]
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector readiness source is not listed in source index" in issue.message
        and omitted_source_key in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_readiness_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    sector_readiness_file = tmp_path / prediction_sector_readiness_path()
    sector_readiness = json.loads(sector_readiness_file.read_text(encoding="utf-8"))
    sector_readiness["snapshot_id"] = "stale-snapshot"
    sector_readiness_file.write_text(json.dumps(sector_readiness, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector readiness snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_context_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    sector_context_file = tmp_path / prediction_sector_context_path("energy")
    sector_context = json.loads(sector_context_file.read_text(encoding="utf-8"))
    sector_context["snapshot_id"] = "stale-snapshot"
    sector_context_file.write_text(json.dumps(sector_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector context snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_context_path_id_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    sector_context_file = tmp_path / prediction_sector_context_path("energy")
    sector_context = json.loads(sector_context_file.read_text(encoding="utf-8"))
    sector_context["sector"]["sector_id"] = "health"
    sector_context["members"][0]["ontology_features"]["sector_exposures"][0]["sector_id"] = "health"
    sector_context_file.write_text(json.dumps(sector_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector context path does not match embedded sector_id" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_sector_context_member_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    sector_context_file = tmp_path / prediction_sector_context_path("energy")
    sector_context = json.loads(sector_context_file.read_text(encoding="utf-8"))
    sector_context["members"][0]["context_reasons"] = ["stale_context"]
    sector_context_file.write_text(json.dumps(sector_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction sector context member does not match member context" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_readiness_context_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    committee_readiness_file = tmp_path / prediction_committee_readiness_path()
    committee_readiness = json.loads(committee_readiness_file.read_text(encoding="utf-8"))
    committee_readiness["committees"][0]["source_count"] = 0
    committee_readiness["committees"][0]["source_keys"] = []
    committee_readiness["committees"][0]["source_context_paths"] = []
    committee_readiness_file.write_text(
        json.dumps(committee_readiness, sort_keys=True), encoding="utf-8"
    )

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee readiness row does not match committee context" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_source_backlink_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    source_context_path = source_index["sources"][0]["source_context_path"]
    source_index["sources"][0]["committee_ids"] = []
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    source_context_file = tmp_path / source_context_path
    source_context = json.loads(source_context_file.read_text(encoding="utf-8"))
    source_context["source"] = source_index["sources"][0]
    source_context_file.write_text(json.dumps(source_context, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee readiness source context does not include committee" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_source_missing_from_index(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    source_index_file = tmp_path / prediction_source_index_path()
    source_index = json.loads(source_index_file.read_text(encoding="utf-8"))
    omitted_source_key = source_index["sources"][0]["source_key"]
    source_index["sources"] = source_index["sources"][1:]
    source_index["source_count"] = len(source_index["sources"])
    source_index_file.write_text(json.dumps(source_index, sort_keys=True), encoding="utf-8")
    topology_file = tmp_path / prediction_topology_path()
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    topology["source_count"] = source_index["source_count"]
    topology_file.write_text(json.dumps(topology, sort_keys=True), encoding="utf-8")

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee readiness source is not listed in source index" in issue.message
        and omitted_source_key in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_readiness_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    committee_readiness_file = tmp_path / prediction_committee_readiness_path()
    committee_readiness = json.loads(committee_readiness_file.read_text(encoding="utf-8"))
    committee_readiness["snapshot_id"] = "stale-snapshot"
    committee_readiness_file.write_text(
        json.dumps(committee_readiness, sort_keys=True), encoding="utf-8"
    )

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee readiness snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_context_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    committee_context_file = tmp_path / prediction_committee_context_path("HSEC")
    committee_context = json.loads(committee_context_file.read_text(encoding="utf-8"))
    committee_context["snapshot_id"] = "stale-snapshot"
    committee_context_file.write_text(
        json.dumps(committee_context, sort_keys=True), encoding="utf-8"
    )

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee context snapshot does not match readiness" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_context_path_id_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    committee_context_file = tmp_path / prediction_committee_context_path("HSEC")
    committee_context = json.loads(committee_context_file.read_text(encoding="utf-8"))
    committee_context["committee"]["committee_id"] = "HSBK"
    committee_context["members"][0]["ontology_features"]["committees"][0]["node_id"] = "HSBK"
    committee_context_file.write_text(
        json.dumps(committee_context, sort_keys=True), encoding="utf-8"
    )

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee context path does not match embedded committee_id" in issue.message
        for issue in result.issues
    )


def test_verify_local_prediction_artifacts_reports_committee_context_member_mismatch(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness(),
    )
    manifest = load_latest_manifest(tmp_path)
    committee_context_file = tmp_path / prediction_committee_context_path("HSEC")
    committee_context = json.loads(committee_context_file.read_text(encoding="utf-8"))
    committee_context["members"][0]["context_reasons"] = ["stale_context"]
    committee_context_file.write_text(
        json.dumps(committee_context, sort_keys=True), encoding="utf-8"
    )

    result = verify_local_prediction_artifacts(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "prediction committee context member does not match member context" in issue.message
        for issue in result.issues
    )
