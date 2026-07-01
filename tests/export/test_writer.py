from __future__ import annotations

import json
import math
from datetime import date, datetime

import pytest
from pydantic import BaseModel

from src.export.builders import sha256_hex
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    HistoricalCommitteeMembership,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberProfilePayload,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.export.writer import (
    PlannedFile,
    current_member_lookup_path,
    evidence_path,
    finalize_publish_plan,
    homepage_bootstrap_path,
    manifest_path,
    member_history_path,
    member_page_payload_path,
    member_path,
    ontology_agent_tools_path,
    ontology_edges_path,
    ontology_frontend_client_path,
    ontology_frontend_contract_path,
    ontology_frontend_types_path,
    ontology_frontend_index_path,
    ontology_index_path,
    ontology_member_features_path,
    ontology_member_edges_path,
    ontology_schema_path,
    plan_snapshot,
    prediction_bootstrap_path,
    prediction_topology_path,
    prediction_committee_context_path,
    prediction_committee_readiness_path,
    prediction_member_readiness_path,
    prediction_member_context_path,
    prediction_readiness_index_path,
    prediction_readiness_path,
    prediction_sector_context_path,
    prediction_sector_readiness_path,
    prediction_source_context_path,
    prediction_source_index_path,
    serialize_payload,
    zip_entry_path,
    zip_path,
)
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.contracts import (
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessPayload,
)

SNAPSHOT_DATE = date(2026, 4, 13)
SNAPSHOT_ID = "2026-04-13"


# ── Fixtures ────────────────────────────────────────────────────────────────────


def _make_member_profile() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        chamber="senate",
        party="Democrat",
        scores=[
            ScoreSummary(
                dimension="conflict_of_interest_risk",
                current_score=72.0,
                rule_fire_count=3,
            )
        ],
        recent_rule_fires=[],
        top_evidence_card_ids=["ec-001"],
        committees=[],
        total_evidence_cards=1,
        snapshot_date=SNAPSHOT_DATE,
    )


def _make_evidence_card() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-001",
        member_bioguide_id="S000148",
        member_name="Charles Schumer",
        member_slug="charles-schumer",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-5.0,
        short_explanation="Trade in sector overlapping with committee jurisdiction.",
        blocks=[
            EvidenceBlock(section=EvidenceSection.FACT, text="Purchased AAPL on 2026-01-15."),
        ],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-99",
                url="https://efdsearch.senate.gov/search/view/paper/99/",
                label="2025 Annual Disclosure",
            )
        ],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 13, 12, 0, 0),
    )


def _make_zip_feed() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="10001",
        members=[
            ZipMemberSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                scores=[],
            )
        ],
        snapshot_date=SNAPSHOT_DATE,
    )


def _make_member_history() -> MemberHistoryPayload:
    return MemberHistoryPayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        snapshots=[
            MemberHistorySnapshot(
                snapshot_date=SNAPSHOT_DATE,
                score_total=72.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 72.0},
                published_at=datetime(2026, 4, 13, 12, 0, 0),
            )
        ],
        events=[
            MemberHistoryEvent(
                rule_id="committee_sector_trade",
                dimension="conflict_of_interest_risk",
                severity="high",
                evidence_card_id="ec-001",
                short_explanation="Trade in sector overlapping with committee jurisdiction.",
                score_delta=-5.0,
                snapshot_date=SNAPSHOT_DATE,
                fired_at=datetime(2026, 4, 13, 12, 0, 0),
            )
        ],
        committee_history=[
            HistoricalCommitteeMembership(
                committee_name="Finance",
                role="Member",
                start_date=SNAPSHOT_DATE,
                end_date=None,
                is_current=True,
                chamber="senate",
                committee_type="standing",
            )
        ],
    )


def _make_ontology_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-001",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(
            node_type="member",
            node_id="S000148",
            label="Charles Schumer",
        ),
        object=OntologyNodeRef(
            node_type="committee",
            node_id="SSFI",
            label="Finance",
        ),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-99",
                url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
                label="Committee membership",
            )
        ],
        attributes={"role": "Member"},
    )


def _make_committee_sector_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-committee-sector-001",
        edge_type="committee_sector_jurisdiction",
        subject=OntologyNodeRef(
            node_type="committee",
            node_id="SSFI",
            label="Finance",
        ),
        object=OntologyNodeRef(
            node_type="sector",
            node_id="finance",
            label="Finance",
        ),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-sector-99",
                url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
                label="Committee jurisdiction",
            )
        ],
    )


# ── serialize_payload ────────────────────────────────────────────────────────────


def test_serialize_payload_returns_bytes():
    result = serialize_payload(_make_member_profile())
    assert isinstance(result, bytes)


def test_serialize_payload_valid_json():
    data = json.loads(serialize_payload(_make_member_profile()))
    assert data["bioguide_id"] == "S000148"
    assert data["slug"] == "charles-schumer"
    assert data["top_evidence_card_ids"] == ["ec-001"]


def test_serialize_payload_sorted_keys():
    raw = serialize_payload(_make_member_profile()).decode("utf-8")
    top_keys = list(json.loads(raw).keys())
    assert top_keys == sorted(top_keys)


def test_serialize_payload_deterministic():
    profile = _make_member_profile()
    assert serialize_payload(profile) == serialize_payload(profile)


def test_serialize_payload_dates_as_strings():
    data = json.loads(serialize_payload(_make_member_profile()))
    assert isinstance(data["snapshot_date"], str)
    assert data["snapshot_date"] == "2026-04-13"


def test_serialize_payload_evidence_card_roundtrip():
    card = _make_evidence_card()
    data = json.loads(serialize_payload(card))
    assert data["evidence_card_id"] == "ec-001"
    assert data["score_delta"] == -5.0
    assert data["confidence"] == "high"
    assert data["source_count"] == 1
    assert data["official_source_count"] == 1
    assert data["primary_source_url"] == "https://efdsearch.senate.gov/search/view/paper/99/"
    assert data["primary_source_label"] == "2025 Annual Disclosure"


def test_serialize_payload_rejects_non_finite_numbers():
    class PayloadWithScore(BaseModel):
        score: float

    with pytest.raises(ValueError, match="JSON compliant"):
        serialize_payload(PayloadWithScore(score=math.nan))


# ── path helpers ─────────────────────────────────────────────────────────────────


def test_member_path():
    assert member_path("charles-schumer") == "members/charles-schumer.json"


def test_member_path_ends_with_json():
    assert member_path("any-slug").endswith(".json")


def test_zip_path():
    assert zip_path("10001") == "zip/10001.json"


def test_evidence_path():
    assert evidence_path("ec-001") == "evidence/ec-001.json"


def test_current_member_lookup_path():
    assert current_member_lookup_path() == "identity/current-member-lookup.json"


def test_homepage_bootstrap_path():
    assert homepage_bootstrap_path() == "homepage/bootstrap.json"


def test_ontology_edges_path():
    assert ontology_edges_path() == "ontology/edges.json"


def test_ontology_agent_tools_path():
    assert ontology_agent_tools_path() == "ontology/agent-tools.json"


def test_ontology_schema_path():
    assert ontology_schema_path() == "ontology/schema.json"


def test_ontology_frontend_index_path():
    assert ontology_frontend_index_path() == "ontology/frontend-index.json"


def test_ontology_frontend_contract_path():
    assert ontology_frontend_contract_path() == "ontology/contracts.json"


def test_ontology_frontend_types_path():
    assert ontology_frontend_types_path() == "ontology/contracts.d.ts"


def test_ontology_frontend_client_path():
    assert ontology_frontend_client_path() == "ontology/client.ts"


def test_ontology_member_edges_path():
    assert ontology_member_edges_path("S000148") == "ontology/members/S000148.json"


def test_ontology_member_edges_path_rejects_unsafe_member_id():
    with pytest.raises(ValueError, match="Invalid ontology member id"):
        ontology_member_edges_path("../S000148")


def test_zip_entry_path():
    assert zip_entry_path("94102") == "zip-entry/94102.json"


def test_member_history_path():
    assert member_history_path("charles-schumer") == "history/members/charles-schumer.json"


def test_member_page_payload_path():
    assert member_page_payload_path("charles-schumer") == "member-pages/charles-schumer.json"


def test_manifest_path():
    assert manifest_path("2026-04-13") == "snapshots/2026-04-13/manifest.json"


def test_manifest_path_contains_snapshot_id():
    sid = "2025-12-31"
    assert sid in manifest_path(sid)


# ── PlannedFile ────────────────────────────────────────────────────────────────────


def test_planned_file_from_bytes_fields():
    content = b"hello world"
    pf = PlannedFile.from_bytes("test/path.json", content)
    assert pf.path == "test/path.json"
    assert pf.content == content
    assert pf.size_bytes == len(content)
    assert len(pf.sha256) == 64


def test_planned_file_sha256_matches():
    content = b"test data for hashing"
    pf = PlannedFile.from_bytes("x.json", content)
    assert pf.sha256 == sha256_hex(content)


def test_planned_file_size_matches():
    content = b"abc"
    pf = PlannedFile.from_bytes("x.json", content)
    assert pf.size_bytes == 3


def test_planned_file_frozen():
    pf = PlannedFile.from_bytes("x.json", b"data")
    with pytest.raises((AttributeError, TypeError)):
        pf.path = "changed"  # type: ignore[misc]


def test_planned_file_empty_content():
    pf = PlannedFile.from_bytes("empty.json", b"")
    assert pf.size_bytes == 0
    assert len(pf.sha256) == 64


# ── plan_snapshot ──────────────────────────────────────────────────────────────────


def test_plan_snapshot_file_count():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    # 1 member + 1 member page + 1 zip + 1 evidence + 1 lookup + 1 manifest
    assert len(plan) == 6


def test_plan_snapshot_empty_inputs():
    plan = plan_snapshot(SNAPSHOT_ID, [], [], [])
    assert [file.path for file in plan] == [
        current_member_lookup_path(),
        manifest_path(SNAPSHOT_ID),
    ]


def test_plan_snapshot_manifest_is_last():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    assert plan[-1].path == manifest_path(SNAPSHOT_ID)


def test_plan_snapshot_member_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    paths = [f.path for f in plan]
    assert "members/charles-schumer.json" in paths


def test_plan_snapshot_member_page_payload_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [_make_evidence_card()])
    paths = [f.path for f in plan]
    assert member_page_payload_path("charles-schumer") in paths


def test_plan_snapshot_member_page_includes_prediction_readiness_when_available():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=10,
        vote_cast_count=8,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=8,
            average_ontology_edges_per_member=3,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                vote_count=8,
                yea_count=5,
                nay_count=3,
                yea_rate=5 / 8,
                nay_rate=3 / 8,
                participation_rate=1,
                latest_vote_date=SNAPSHOT_DATE,
                ontology_readiness_status="ready",
                ontology_edge_count=3,
                readiness_status="ready",
            )
        ],
    )

    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [_make_evidence_card()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == member_page_payload_path("charles-schumer"))
    )

    assert payload["prediction_readiness"]["bioguide_id"] == "S000148"
    assert payload["prediction_readiness"]["yea_rate"] == 5 / 8
    assert payload["prediction_readiness"]["readiness_status"] == "ready"


def test_plan_snapshot_zip_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [], [_make_zip_feed()], [])
    paths = [f.path for f in plan]
    assert "zip/10001.json" in paths


def test_plan_snapshot_evidence_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [], [], [_make_evidence_card()])
    paths = [f.path for f in plan]
    assert "evidence/ec-001.json" in paths


def test_plan_snapshot_ontology_edges_path_present():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge()],
    )
    paths = [f.path for f in plan]
    assert ontology_edges_path() in paths


def test_plan_snapshot_ontology_index_path_present():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge()],
    )
    paths = [f.path for f in plan]
    assert ontology_index_path() in paths


def test_plan_snapshot_ontology_index_payload_is_fast_summary():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
    )
    payload = json.loads(next(f.content for f in plan if f.path == ontology_index_path()))

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["edge_count"] == 2
    assert payload["member_count"] == 1
    assert payload["available_member_graphs"] == ["S000148"]
    assert payload["edge_type_counts"] == {
        "committee_sector_jurisdiction": 1,
        "member_committee_assignment": 1,
    }


def test_plan_snapshot_emits_ontology_static_schema_and_frontend_index():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
    )
    schema_payload = json.loads(next(f.content for f in plan if f.path == ontology_schema_path()))
    frontend_index_payload = json.loads(
        next(f.content for f in plan if f.path == ontology_frontend_index_path())
    )

    assert schema_payload["schema_version"] == "psephosamerica-ontology-v1"
    assert "member" in {item["object_type"] for item in schema_payload["object_types"]}
    assert "predict_member_vote" in {item["action_type"] for item in schema_payload["action_types"]}
    assert frontend_index_payload["snapshot_id"] == SNAPSHOT_ID
    assert frontend_index_payload["schema_version"] == "psephosamerica-ontology-frontend-index-v1"
    assert frontend_index_payload["edge_count"] == 2
    assert frontend_index_payload["member_ids"] == ["S000148"]
    assert frontend_index_payload["by_member"]["S000148"]["linked_object_ids_by_type"] == {
        "committee": ["SSFI"]
    }
    assert frontend_index_payload["by_committee"]["SSFI"]["linked_object_ids_by_type"] == {
        "member": ["S000148"],
        "sector": ["finance"],
    }


def test_plan_snapshot_emits_ontology_frontend_contract_artifacts():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge()],
    )
    contract_payload = json.loads(
        next(f.content for f in plan if f.path == ontology_frontend_contract_path())
    )
    agent_tools_payload = json.loads(
        next(f.content for f in plan if f.path == ontology_agent_tools_path())
    )
    types_payload = next(f.content for f in plan if f.path == ontology_frontend_types_path())

    assert contract_payload["schema_version"] == "psephosamerica-ontology-contract-v1"
    assert contract_payload["artifact_paths"]["agent_tools"] == "ontology/agent-tools.json"
    assert contract_payload["artifact_paths"]["frontend_client"] == "ontology/client.ts"
    assert contract_payload["artifact_paths"]["frontend_contract"] == ("ontology/contracts.json")
    assert contract_payload["artifact_paths"]["frontend_types"] == ("ontology/contracts.d.ts")
    assert "OntologyFrontendIndexPayload" in contract_payload["model_names"]
    assert "OntologyStaticSchemaPayload" in contract_payload["json_schemas"]
    assert b"ONTOLOGY_FRONTEND_CONTRACT_VERSION" in types_payload
    assert b"export interface OntologyFrontendContractSchemas" in types_payload
    assert b"OntologyFrontendIndexPayload: JsonSchema;" in types_payload
    client_payload = next(f.content for f in plan if f.path == ontology_frontend_client_path())
    assert b"ONTOLOGY_FRONTEND_CLIENT_VERSION" in client_payload
    assert b"export class PsephosAmericaOntologyClient" in client_payload
    assert b'agent_tools: "ontology/agent-tools.json"' in client_payload
    assert b'frontend_contract: "ontology/contracts.json"' in client_payload
    assert agent_tools_payload["schema_version"] == "psephosamerica-ontology-agent-tools-v1"
    assert agent_tools_payload["tools"][0]["tool_name"] == "audit_ontology_claim_sources"


def test_prediction_readiness_path():
    assert prediction_readiness_path() == "prediction/readiness.json"


def test_prediction_bootstrap_path():
    assert prediction_bootstrap_path() == "prediction/bootstrap.json"


def test_prediction_topology_path():
    assert prediction_topology_path() == "prediction/topology.json"


def test_prediction_sector_readiness_path():
    assert prediction_sector_readiness_path() == "prediction/sectors.json"


def test_prediction_sector_context_path():
    assert prediction_sector_context_path("finance") == "prediction/sector-context/finance.json"


def test_prediction_committee_readiness_path():
    assert prediction_committee_readiness_path() == "prediction/committees.json"


def test_prediction_committee_context_path():
    assert prediction_committee_context_path("SSFI") == "prediction/committee-context/SSFI.json"


def test_prediction_readiness_index_path():
    assert prediction_readiness_index_path() == "prediction/index.json"


def test_prediction_source_index_path():
    assert prediction_source_index_path() == "prediction/sources.json"


def test_prediction_source_context_path():
    assert prediction_source_context_path("abc123") == "prediction/source-context/abc123.json"


def test_prediction_member_readiness_path():
    assert prediction_member_readiness_path("S000148") == "prediction/members/S000148.json"


def test_prediction_member_context_path():
    assert prediction_member_context_path("S000148") == "prediction/member-context/S000148.json"


def test_prediction_member_readiness_path_rejects_unsafe_id():
    with pytest.raises(ValueError):
        prediction_member_readiness_path("../S000148")


def test_plan_snapshot_prediction_readiness_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=0,
        vote_cast_count=0,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=0,
            vote_coverage_rate=0,
            ontology_coverage_rate=0,
            average_votes_per_member=0,
            average_ontology_edges_per_member=0,
        ),
        members=[],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        prediction_readiness=readiness,
    )
    payload = json.loads(next(f.content for f in plan if f.path == prediction_readiness_path()))

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["member_count"] == 0


def test_plan_snapshot_prediction_bootstrap_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=0,
        vote_cast_count=0,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=0,
            vote_coverage_rate=0,
            ontology_coverage_rate=0,
            average_votes_per_member=0,
            average_ontology_edges_per_member=0,
        ),
        members=[],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        prediction_readiness=readiness,
    )
    payload = json.loads(next(f.content for f in plan if f.path == prediction_bootstrap_path()))

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["readiness_path"] == "prediction/readiness.json"
    assert payload["index_path"] == "prediction/index.json"
    assert payload["topology_path"] == "prediction/topology.json"
    assert payload["source_index_path"] == "prediction/sources.json"
    assert payload["member_context_path_template"] == "prediction/member-context/{bioguide}.json"


def test_plan_snapshot_prediction_topology_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=2,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=2,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(next(f.content for f in plan if f.path == prediction_topology_path()))

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["member_count"] == 1
    assert payload["source_count"] == 2
    assert payload["sector_count"] == 1
    assert payload["committee_count"] == 1
    assert payload["readiness_path"] == "prediction/readiness.json"
    assert payload["source_context_path_template"] == "prediction/source-context/{source_key}.json"
    assert payload["member_context_path_template"] == "prediction/member-context/{bioguide}.json"


def test_plan_snapshot_prediction_readiness_index_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_readiness_index_path())
    )

    assert payload["slug_to_bioguide"] == {"charles-schumer": "S000148"}
    assert payload["members_by_bioguide"]["S000148"]["participation_rate"] == 1


def test_plan_snapshot_prediction_member_readiness_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_member_readiness_path("S000148"))
    )

    assert payload["bioguide_id"] == "S000148"
    assert payload["slug"] == "charles-schumer"
    assert payload["readiness_status"] == "ready"


def test_plan_snapshot_prediction_member_context_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_member_context_path("S000148"))
    )

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["member_bioguide_id"] == "S000148"
    assert payload["member_readiness"]["readiness_status"] == "ready"
    assert payload["ontology_features"]["member_bioguide_id"] == "S000148"
    assert payload["artifact_refs"] == {
        "member_page_path": "member-pages/charles-schumer.json",
        "member_profile_path": "members/charles-schumer.json",
        "ontology_member_features_path": "ontology/member-features/S000148.json",
        "ontology_member_graph_path": "ontology/members/S000148.json",
        "prediction_member_context_path": "prediction/member-context/S000148.json",
        "prediction_member_readiness_path": "prediction/members/S000148.json",
    }
    assert [anchor["source_id"] for anchor in payload["source_anchors"]] == [
        "cm-99",
        "cm-sector-99",
    ]
    assert len(payload["source_keys"]) == 2
    assert payload["source_context_paths"] == [
        f"prediction/source-context/{source_key}.json" for source_key in payload["source_keys"]
    ]
    assert {anchor["url"] for anchor in payload["source_anchors"]} == {
        "https://api.congress.gov/v3/committee/senate/SSFI?format=json"
    }


def test_plan_snapshot_prediction_sector_readiness_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_sector_readiness_path())
    )

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["sector_count"] == 1
    assert payload["sectors"][0]["sector_id"] == "finance"
    assert payload["sectors"][0]["source_count"] == 2
    assert len(payload["sectors"][0]["source_keys"]) == 2
    assert payload["sectors"][0]["source_context_paths"] == [
        f"prediction/source-context/{source_key}.json"
        for source_key in payload["sectors"][0]["source_keys"]
    ]


def test_plan_snapshot_prediction_source_index_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(next(f.content for f in plan if f.path == prediction_source_index_path()))

    assert payload["source_count"] == 2
    assert {source["source_id"] for source in payload["sources"]} == {"cm-99", "cm-sector-99"}
    assert [source["source_key"] for source in payload["sources"]] == sorted(
        source["source_key"] for source in payload["sources"]
    )
    assert len(payload["sources"][0]["source_key"]) == 24
    assert payload["sources"][0]["source_context_path"] == (
        f"prediction/source-context/{payload['sources'][0]['source_key']}.json"
    )
    for source in payload["sources"]:
        assert source["member_bioguide_ids"] == ["S000148"]
        assert source["committee_ids"] == ["SSFI"]
    sector_source = next(source for source in payload["sources"] if source["source_id"] == "cm-99")
    assert sector_source["sector_ids"] == ["finance"]


def test_plan_snapshot_prediction_source_context_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    source_index = json.loads(
        next(f.content for f in plan if f.path == prediction_source_index_path())
    )
    source_key = source_index["sources"][0]["source_key"]
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_source_context_path(source_key))
    )

    assert payload["source"]["source_key"] == source_key
    assert payload["members"][0]["member_bioguide_id"] == "S000148"


def test_plan_snapshot_prediction_sector_context_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_sector_context_path("finance"))
    )

    assert payload["sector"]["sector_id"] == "finance"
    assert payload["members"][0]["member_bioguide_id"] == "S000148"


def test_plan_snapshot_prediction_committee_readiness_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_committee_readiness_path())
    )

    assert payload["committee_count"] == 1
    assert payload["committees"][0]["committee_id"] == "SSFI"
    assert payload["committees"][0]["sector_ids"] == ["finance"]
    assert payload["committees"][0]["source_count"] == 2
    assert len(payload["committees"][0]["source_keys"]) == 2
    assert payload["committees"][0]["source_context_paths"] == [
        f"prediction/source-context/{source_key}.json"
        for source_key in payload["committees"][0]["source_keys"]
    ]


def test_plan_snapshot_prediction_committee_context_path_present():
    readiness = PredictionReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=1,
        vote_cast_count=1,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=1,
            vote_coverage_rate=1,
            ontology_coverage_rate=1,
            average_votes_per_member=1,
            average_ontology_edges_per_member=1,
        ),
        members=[
            PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                ontology_edge_count=1,
                readiness_status="ready",
            )
        ],
    )
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
        prediction_readiness=readiness,
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == prediction_committee_context_path("SSFI"))
    )

    assert payload["committee"]["committee_id"] == "SSFI"
    assert payload["members"][0]["member_bioguide_id"] == "S000148"


def test_plan_snapshot_ontology_edges_payload_is_frontend_fast():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge()],
    )
    payload = json.loads(next(f.content for f in plan if f.path == ontology_edges_path()))

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["edge_count"] == 1
    assert payload["edges"][0]["edge_id"] == "ont-edge-001"
    assert payload["edges"][0]["subject"]["node_id"] == "S000148"


def test_plan_snapshot_explicit_empty_ontology_edges_emit_empty_graph():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[],
    )
    payload = json.loads(next(f.content for f in plan if f.path == ontology_edges_path()))
    paths = [f.path for f in plan]

    assert payload == {"edge_count": 0, "edges": [], "snapshot_id": SNAPSHOT_ID}
    assert ontology_index_path() in paths
    assert all(not path.startswith("ontology/members/") for path in paths)


def test_plan_snapshot_emits_member_scoped_ontology_edges_payload():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge()],
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == ontology_member_edges_path("S000148"))
    )

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["member_bioguide_id"] == "S000148"
    assert payload["edge_count"] == 1
    assert payload["edges"][0]["edge_id"] == "ont-edge-001"


def test_plan_snapshot_emits_member_ontology_features_payload():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == ontology_member_features_path("S000148"))
    )

    assert payload["snapshot_id"] == SNAPSHOT_ID
    assert payload["member_bioguide_id"] == "S000148"
    assert payload["readiness_status"] == "partial"
    assert payload["readiness_reasons"] == ["missing_member_financial_exposure"]
    assert payload["committees"][0]["node_id"] == "SSFI"
    assert payload["sector_exposures"][0]["sector_id"] == "finance"


def test_plan_snapshot_member_scoped_ontology_includes_committee_sector_context():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [],
        [],
        [],
        ontology_edges=[_make_ontology_edge(), _make_committee_sector_edge()],
    )
    payload = json.loads(
        next(f.content for f in plan if f.path == ontology_member_edges_path("S000148"))
    )

    assert payload["member_bioguide_id"] == "S000148"
    assert payload["edge_count"] == 2
    assert [edge["edge_id"] for edge in payload["edges"]] == [
        "ont-edge-001",
        "ont-edge-committee-sector-001",
    ]
    assert payload["edges"][1]["object"]["node_id"] == "finance"


@pytest.mark.parametrize(
    "source_type",
    ["financial_disclosure", "fec_contribution", "vote_event"],
)
def test_plan_snapshot_rejects_nonzero_claim_bearing_anchor_without_https(source_type):
    invalid_card = _make_evidence_card().model_copy(
        update={
            "source_anchors": [
                SourceAnchor(
                    source_type=source_type,
                    source_id="source-1",
                    url=None,
                    label="Claim-bearing source",
                )
            ]
        }
    )

    with pytest.raises(ValueError, match=rf"{source_type}.*source-1"):
        plan_snapshot(SNAPSHOT_ID, [], [], [invalid_card])


def test_plan_snapshot_rejects_nonzero_card_with_only_metadata_https_anchor():
    invalid_card = _make_evidence_card().model_copy(
        update={
            "source_anchors": [
                SourceAnchor(
                    source_type="rule_context",
                    source_id="context-1",
                    url="https://example.com/context",
                    label="Rule context",
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="official source"):
        plan_snapshot(SNAPSHOT_ID, [], [], [invalid_card])


def test_plan_snapshot_member_history_path_present() -> None:
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [],
        [],
        member_histories=[_make_member_history()],
    )
    paths = [f.path for f in plan]
    assert "history/members/charles-schumer.json" in paths


def test_plan_snapshot_lookup_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    paths = [f.path for f in plan]
    assert current_member_lookup_path() in paths


def test_plan_snapshot_manifest_covers_data_files():
    """Manifest entries must exactly cover all non-manifest planned files."""
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    manifest_content = json.loads(plan[-1].content)
    manifest_paths = {e["path"] for e in manifest_content["entries"]}
    data_paths = {f.path for f in plan[:-1]}
    assert data_paths == manifest_paths


def test_finalize_publish_plan_manifest_covers_root_public_files():
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    homepage = PlannedFile.from_bytes("homepage/feed.json", b'{"items":[]}')
    robots = PlannedFile.from_bytes("robots.txt", b"User-agent: *\nAllow: /\n")

    finalized = finalize_publish_plan(SNAPSHOT_ID, plan + [homepage, robots])

    manifest_file = next(f for f in finalized if f.path == manifest_path(SNAPSHOT_ID))
    manifest_content = json.loads(manifest_file.content)
    manifest_paths = {entry["path"] for entry in manifest_content["entries"]}

    assert manifest_paths == {f.path for f in finalized if f.path != manifest_path(SNAPSHOT_ID)}


def test_plan_snapshot_manifest_sha256_correct():
    """SHA-256 recorded in the manifest matches actual file content."""
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    manifest_data = json.loads(plan[-1].content)
    for entry in manifest_data["entries"]:
        matching = next(f for f in plan if f.path == entry["path"])
        assert entry["sha256"] == matching.sha256


def test_plan_snapshot_manifest_size_correct():
    """size_bytes recorded in the manifest matches actual file content."""
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    manifest_data = json.loads(plan[-1].content)
    for entry in manifest_data["entries"]:
        matching = next(f for f in plan if f.path == entry["path"])
        assert entry["size_bytes"] == matching.size_bytes


def test_plan_snapshot_deterministic():
    """Data files are content-deterministic; all paths are stable across calls.

    The manifest's ``created_at`` is wall-clock-stamped by ``build_manifest``,
    so manifest *content* may differ between calls — but all paths and every
    data-file content must be identical.
    """
    plan1 = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [_make_zip_feed()], [])
    plan2 = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [_make_zip_feed()], [])
    assert len(plan1) == len(plan2)
    mpath = manifest_path(SNAPSHOT_ID)
    for f1, f2 in zip(plan1, plan2):
        assert f1.path == f2.path  # path-stable always
        if f1.path != mpath:
            assert f1.content == f2.content  # data files are content-deterministic
            assert f1.sha256 == f2.sha256


def test_plan_snapshot_all_files_have_valid_sha256():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    for f in plan:
        assert f.sha256 == sha256_hex(f.content)
        assert len(f.sha256) == 64


def test_plan_snapshot_no_duplicate_paths():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    paths = [f.path for f in plan]
    assert len(paths) == len(set(paths))


def test_serialize_payload_uses_aliases_for_compact_lookup_payload():
    from src.identity.current_member_lookup import build_current_member_lookup

    payload = build_current_member_lookup([_make_member_profile()], snapshot_date=SNAPSHOT_DATE)

    data = json.loads(serialize_payload(payload))
    assert sorted(data.keys()) == ["m", "sd", "v"]
    assert data["m"][0]["q"] == "charles schumer"
