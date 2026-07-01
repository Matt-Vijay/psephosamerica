from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.api.contracts import (
    HistoryBootstrapPayload,
    HistoryPresetRangePayload,
    HomepageBootstrapPayload,
    MemberHistoryPagePayload,
    MemberPagePayload,
)
from src.export.contracts import (
    ConfidenceLabel,
    DimensionChangeSummary,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    HistoricalCommitteeMembership,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryChartPoint,
    MemberHistoryComparePreset,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberProfilePayload,
    SnapshotComparePresetPayload,
    SourceAnchor,
    MemberTrendSummaryPayload,
    MemberTrendWindowPayload,
    ZipFeedPayload,
)
from src.export.filesystem import write_planned_files
from src.export.local_store import (
    HOMEPAGE_FEED_PATH,
    _safe_subpath,
    load_current_member_lookup,
    load_history_bootstrap,
    load_history_preset_range,
    load_homepage_bootstrap,
    load_member_page,
    load_member_change_summary,
    load_member_history_chart,
    load_member_history_page,
    load_member_preset_compare,
    load_latest_snapshot_metadata,
    load_member_history,
    load_snapshot_preset_compare,
    load_member_trend_summary,
    load_movement_window,
    latest_snapshot_id,
    list_snapshot_ids,
    list_artifact_paths,
    manifest_published_at,
    manifest_snapshot_date,
    load_evidence_card,
    load_homepage_feed,
    load_latest_manifest,
    load_manifest,
    load_member_profile,
    load_ontology_agent_tools,
    load_ontology_edges,
    load_ontology_frontend_client,
    load_ontology_frontend_contract,
    load_ontology_frontend_types,
    load_ontology_frontend_index,
    load_ontology_index,
    load_ontology_member_features,
    load_ontology_member_edges,
    load_ontology_static_schema,
    load_prediction_bootstrap,
    load_prediction_committee_context,
    load_prediction_committee_readiness,
    load_prediction_member_context,
    load_prediction_member_readiness,
    load_prediction_readiness,
    load_prediction_readiness_index,
    load_prediction_sector_context,
    load_prediction_sector_readiness,
    load_prediction_source_context,
    load_prediction_source_index,
    load_prediction_topology,
    load_snapshot_index,
    load_zip_entry,
    load_zip_feed,
)
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.homepage.contracts import MovementWindowPayload, SnapshotComparePayload
from src.identity.current_member_lookup import CurrentMemberLookupEntry, CurrentMemberLookupPayload
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import (
    PlannedFile,
    current_member_lookup_path,
    evidence_path,
    history_bootstrap_path,
    history_preset_range_path,
    homepage_bootstrap_path,
    manifest_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_page_path,
    member_preset_compare_path,
    member_history_path,
    member_page_payload_path,
    member_path,
    member_trend_summary_path,
    movement_window_path,
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
    prediction_committee_context_path,
    prediction_committee_readiness_path,
    prediction_bootstrap_path,
    prediction_topology_path,
    prediction_member_context_path,
    prediction_member_readiness_path,
    prediction_readiness_index_path,
    prediction_readiness_path,
    prediction_sector_context_path,
    prediction_sector_readiness_path,
    prediction_source_context_path,
    prediction_source_index_path,
    serialize_payload,
    snapshot_preset_compare_path,
    snapshot_index_path,
    zip_entry_path,
    zip_path,
)
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
    OntologyNodeRef,
    OntologySectorExposurePayload,
)
from src.ontology.static_schema import (
    OntologyFrontendIndexPayload,
    build_ontology_frontend_index,
    build_ontology_static_schema,
)
from src.ontology.agent_tools import build_ontology_agent_tool_manifest
from src.ontology.frontend_contracts import (
    build_ontology_frontend_contract,
    build_ontology_typescript_client,
)
from src.prediction.contracts import (
    PredictionBootstrapPayload,
    PredictionCommitteeContextPayload,
    PredictionCommitteeReadinessPayload,
    PredictionCommitteeReadinessRowPayload,
    PredictionMemberContextPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessIndexPayload,
    PredictionReadinessPayload,
    PredictionSectorContextPayload,
    PredictionSectorReadinessPayload,
    PredictionSectorReadinessRowPayload,
    PredictionSourceContextPayload,
    PredictionSourceIndexPayload,
    PredictionSourceIndexRowPayload,
    PredictionTopologyPayload,
    prediction_source_context_path as prediction_model_source_context_path,
    prediction_source_key,
)


# ── Fixture payloads ───────────────────────────────────────────────


SNAPSHOT_DATE = date(2026, 4, 13)
SNAPSHOT_ID = "2026-04-13"


def _prediction_source_anchor() -> SourceAnchor:
    return SourceAnchor(
        source_type="committee_membership",
        source_id="cm-99",
        url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
        label="Committee membership",
    )


def _prediction_source_key() -> str:
    anchor = _prediction_source_anchor()
    return prediction_source_key(
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label=anchor.label,
    )


def _prediction_source_context_path() -> str:
    return prediction_model_source_context_path(_prediction_source_key())


def _prediction_ontology_features() -> OntologyMemberFeaturesPayload:
    return OntologyMemberFeaturesPayload(
        snapshot_id=SNAPSHOT_ID,
        member_bioguide_id="S000148",
        edge_count=2,
        source_count=1,
        edge_type_counts={
            "committee_sector_jurisdiction": 1,
            "member_committee_assignment": 1,
        },
        source_type_counts={"committee_membership": 1},
        committees=[OntologyNodeRef(node_type="committee", node_id="SSFI", label="Finance")],
        sector_exposures=[
            OntologySectorExposurePayload(
                sector_id="finance",
                label="Finance",
                committee_jurisdiction_edge_count=1,
                source_count=1,
            )
        ],
        readiness_status="ready",
    )


def _member_payload() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=SNAPSHOT_DATE,
    )


def _evidence_payload() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-001",
        member_bioguide_id="S000148",
        member_name="Charles Schumer",
        member_slug="charles-schumer",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-5.0,
        short_explanation="Trade overlapping committee jurisdiction.",
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


def _ontology_graph_payload() -> OntologyGraphPayload:
    edge = OntologyEdgePayload(
        edge_id="ont-edge-local-001",
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
                source_id="cm-local-1",
                url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
                label="Committee membership",
            )
        ],
    )
    return OntologyGraphPayload(
        snapshot_id=SNAPSHOT_ID,
        edge_count=1,
        edges=[edge],
    )


def _zip_payload() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="10001",
        congressional_district="NY-12",
        ambiguity_note=None,
        members=[],
        snapshot_date=SNAPSHOT_DATE,
    )


def _member_history_payload() -> MemberHistoryPayload:
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
                short_explanation="Trade overlapping committee jurisdiction.",
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


def _member_change_summary_payload() -> MemberChangeSummaryPayload:
    return MemberChangeSummaryPayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        latest_snapshot_date=SNAPSHOT_DATE,
        previous_snapshot_date=date(2026, 4, 6),
        latest_score_total=72.0,
        previous_score_total=60.0,
        score_total_delta=12.0,
        top_dimension_changes=[
            DimensionChangeSummary(
                dimension="conflict_of_interest_risk",
                current_score=72.0,
                previous_score=60.0,
                score_delta=12.0,
                abs_delta=12.0,
                event_count=1,
            )
        ],
        recent_events=_member_history_payload().events,
        top_evidence_card_ids=["ec-001"],
    )


def _movement_window_payload() -> MovementWindowPayload:
    return MovementWindowPayload(
        latest_snapshot_id=SNAPSHOT_ID,
        latest_snapshot_date=SNAPSHOT_DATE,
        previous_snapshot_id="2026-04-06",
        previous_snapshot_date=date(2026, 4, 6),
        top_changes=[
            MemberMovementSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                dimension="conflict_of_interest_risk",
                score_delta=-12.0,
                abs_delta=12.0,
                event_count=1,
                top_evidence_card_ids=["ec-001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="fe-001",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                member_slug="charles-schumer",
                dimension="conflict_of_interest_risk",
                score_delta=-12.0,
                short_explanation="Trade overlapping committee jurisdiction.",
                evidence_card_id="ec-001",
                occurred_at=SNAPSHOT_DATE,
            )
        ],
        recent_evidence_card_ids=["ec-001"],
    )


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    entries = [ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes) for f in files]
    return SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 13, 0, 0, 0),
        entries=entries,
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
        root_sha256=manifest_root_sha256(entries),
    )


def _current_member_lookup_payload() -> CurrentMemberLookupPayload:
    return CurrentMemberLookupPayload(
        snapshot_date=SNAPSHOT_DATE,
        members=[
            CurrentMemberLookupEntry(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                search_name="charles schumer",
                state="NY",
                district=None,
                chamber="senate",
            )
        ],
    )


# ── Helpers ────────────────────────────────────────────────────────


def _write_json(root: Path, rel_path: str, data: object) -> None:
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, default=str), encoding="utf-8")


def _serialise(model: object) -> bytes:
    """Serialise a Pydantic model to JSON bytes (mirrors writer.serialize_payload)."""
    import json as _json
    from pydantic import BaseModel as _BM

    assert isinstance(model, _BM)
    return _json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )


# ── load_member_profile ────────────────────────────────────────────


def test_load_member_profile_roundtrip(tmp_path: Path) -> None:
    payload = _member_payload()
    pf = PlannedFile.from_bytes(member_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_profile(tmp_path, payload.slug)
    assert result.bioguide_id == "S000148"
    assert result.slug == "charles-schumer"
    assert result.chamber == "senate"
    assert result.snapshot_date == SNAPSHOT_DATE


def test_load_member_page_roundtrip(tmp_path: Path) -> None:
    payload = MemberPagePayload(
        profile=_member_payload().model_copy(
            update={
                "top_evidence_card_ids": ["ec-001"],
                "total_evidence_cards": 1,
            }
        ),
        top_evidence_cards=[_evidence_payload()],
        recent_evidence_cards=[_evidence_payload()],
    )
    pf = PlannedFile.from_bytes(
        member_page_payload_path("charles-schumer"),
        json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
    )
    write_planned_files([pf], tmp_path)

    result = load_member_page(tmp_path, "charles-schumer")
    assert result.profile.slug == "charles-schumer"
    assert result.top_evidence_cards[0].evidence_card_id == "ec-001"


def test_load_member_page_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_page(tmp_path, "ghost-member")


def test_load_member_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_profile(tmp_path, "nonexistent-member")


def test_load_member_profile_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / member_path("bad-member")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not valid json {{{")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_member_profile(tmp_path, "bad-member")


def test_load_member_profile_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, member_path("bad-schema"), {"completely": "wrong"})
    with pytest.raises(Exception):
        load_member_profile(tmp_path, "bad-schema")


# ── load_evidence_card ─────────────────────────────────────────────


def test_load_evidence_card_roundtrip(tmp_path: Path) -> None:
    payload = _evidence_payload()
    pf = PlannedFile.from_bytes(evidence_path(payload.evidence_card_id), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_evidence_card(tmp_path, "ec-001")
    assert result.evidence_card_id == "ec-001"
    assert result.member_bioguide_id == "S000148"
    assert result.confidence == ConfidenceLabel.HIGH
    assert result.score_delta == -5.0


def test_load_evidence_card_rejects_nonzero_claim_bearing_anchor_without_https(
    tmp_path: Path,
) -> None:
    payload = _evidence_payload().model_copy(
        update={
            "source_anchors": [
                SourceAnchor(
                    source_type="financial_disclosure",
                    source_id="fd-99",
                    url=None,
                    label="2025 Annual Disclosure",
                )
            ]
        }
    )
    pf = PlannedFile.from_bytes(evidence_path(payload.evidence_card_id), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="financial_disclosure.*fd-99"):
        load_evidence_card(tmp_path, "ec-001")


def test_load_evidence_card_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_evidence_card(tmp_path, "ec-999")


def test_load_evidence_card_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / evidence_path("ec-bad")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"[[[")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_evidence_card(tmp_path, "ec-bad")


# ── load_ontology_edges ────────────────────────────────────────────


def test_load_ontology_agent_tools_roundtrip(tmp_path: Path) -> None:
    payload = build_ontology_agent_tool_manifest()
    pf = PlannedFile.from_bytes(ontology_agent_tools_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_agent_tools(tmp_path)

    assert result.schema_version == "psephosamerica-ontology-agent-tools-v1"
    assert result.tools[0].tool_name == "audit_ontology_claim_sources"


def test_load_ontology_agent_tools_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_agent_tools(tmp_path)


def test_load_ontology_edges_roundtrip(tmp_path: Path) -> None:
    payload = _ontology_graph_payload()
    pf = PlannedFile.from_bytes(ontology_edges_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_edges(tmp_path)

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.edge_count == 1
    assert result.edges[0].edge_id == "ont-edge-local-001"


def test_load_ontology_edges_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_edges(tmp_path)


def test_load_ontology_static_schema_roundtrip(tmp_path: Path) -> None:
    payload = build_ontology_static_schema()
    pf = PlannedFile.from_bytes(ontology_schema_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_static_schema(tmp_path)

    assert result.schema_version == "psephosamerica-ontology-v1"
    assert "member" in {item.object_type for item in result.object_types}


def test_load_ontology_static_schema_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_static_schema(tmp_path)


def test_load_ontology_frontend_contract_roundtrip(tmp_path: Path) -> None:
    payload = build_ontology_frontend_contract()
    pf = PlannedFile.from_bytes(ontology_frontend_contract_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_frontend_contract(tmp_path)

    assert result.schema_version == "psephosamerica-ontology-contract-v1"
    assert "OntologyFrontendIndexPayload" in result.model_names


def test_load_ontology_frontend_contract_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_frontend_contract(tmp_path)


def test_load_ontology_frontend_types_roundtrip(tmp_path: Path) -> None:
    content = "export interface OntologyFrontendContractSchemas {}\n"
    pf = PlannedFile.from_bytes(ontology_frontend_types_path(), content.encode("utf-8"))
    write_planned_files([pf], tmp_path)

    result = load_ontology_frontend_types(tmp_path)

    assert result == content


def test_load_ontology_frontend_types_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_frontend_types(tmp_path)


def test_load_ontology_frontend_client_roundtrip(tmp_path: Path) -> None:
    content = build_ontology_typescript_client()
    pf = PlannedFile.from_bytes(ontology_frontend_client_path(), content.encode("utf-8"))
    write_planned_files([pf], tmp_path)

    result = load_ontology_frontend_client(tmp_path)

    assert result == content
    assert "PsephosAmericaOntologyClient" in result


def test_load_ontology_frontend_client_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_frontend_client(tmp_path)


def test_load_ontology_frontend_index_roundtrip(tmp_path: Path) -> None:
    graph = _ontology_graph_payload()
    payload = build_ontology_frontend_index(
        snapshot_id=SNAPSHOT_ID,
        edges=graph.edges,
    )
    pf = PlannedFile.from_bytes(ontology_frontend_index_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_frontend_index(tmp_path)

    assert isinstance(result, OntologyFrontendIndexPayload)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.schema_version == "psephosamerica-ontology-frontend-index-v1"
    assert result.member_ids == ["S000148"]
    assert result.by_member["S000148"].edge_ids == ["ont-edge-local-001"]


def test_load_ontology_frontend_index_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_frontend_index(tmp_path)


def test_load_ontology_index_roundtrip(tmp_path: Path) -> None:
    payload = OntologyIndexPayload(
        snapshot_id=SNAPSHOT_ID,
        edge_count=1,
        node_count=2,
        member_count=1,
        edge_type_counts={"member_committee_assignment": 1},
        node_type_counts={"member": 1, "committee": 1},
        source_type_counts={"committee_membership": 1},
        member_edge_counts={"S000148": 1},
        available_member_graphs=["S000148"],
    )
    pf = PlannedFile.from_bytes(ontology_index_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_index(tmp_path)

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.edge_count == 1
    assert result.available_member_graphs == ["S000148"]


def test_load_ontology_index_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_index(tmp_path)


def test_load_ontology_member_edges_roundtrip(tmp_path: Path) -> None:
    graph = _ontology_graph_payload()
    payload = OntologyMemberGraphPayload(
        snapshot_id=SNAPSHOT_ID,
        member_bioguide_id="S000148",
        edge_count=1,
        edges=graph.edges,
    )
    pf = PlannedFile.from_bytes(ontology_member_edges_path("S000148"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_member_edges(tmp_path, "S000148")

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.member_bioguide_id == "S000148"
    assert result.edge_count == 1
    assert result.edges[0].edge_id == "ont-edge-local-001"


def test_load_ontology_member_edges_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_member_edges(tmp_path, "S000148")


def test_load_ontology_member_features_roundtrip(tmp_path: Path) -> None:
    payload = OntologyMemberFeaturesPayload(
        snapshot_id=SNAPSHOT_ID,
        member_bioguide_id="S000148",
        edge_count=1,
        source_count=1,
        edge_type_counts={"member_committee_assignment": 1},
        source_type_counts={"committee_membership": 1},
        committees=[],
        sector_exposures=[],
        readiness_status="partial",
        readiness_reasons=["missing_committee_sector_context", "missing_member_financial_exposure"],
    )
    pf = PlannedFile.from_bytes(ontology_member_features_path("S000148"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_ontology_member_features(tmp_path, "S000148")

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.member_bioguide_id == "S000148"
    assert result.readiness_status == "partial"


def test_load_ontology_member_features_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ontology_member_features(tmp_path, "S000148")


def test_load_prediction_readiness_roundtrip(tmp_path: Path) -> None:
    payload = PredictionReadinessPayload(
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
    pf = PlannedFile.from_bytes(prediction_readiness_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_readiness(tmp_path)

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.member_count == 0


def test_load_prediction_bootstrap_roundtrip(tmp_path: Path) -> None:
    payload = PredictionBootstrapPayload(
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
        readiness_path="prediction/readiness.json",
        index_path="prediction/index.json",
        source_index_path="prediction/sources.json",
        source_context_path_template="prediction/source-context/{source_key}.json",
        sector_readiness_path="prediction/sectors.json",
        sector_context_path_template="prediction/sector-context/{sector}.json",
        committee_readiness_path="prediction/committees.json",
        committee_context_path_template="prediction/committee-context/{committee}.json",
        member_readiness_path_template="prediction/members/{bioguide}.json",
        member_context_path_template="prediction/member-context/{bioguide}.json",
    )
    pf = PlannedFile.from_bytes(prediction_bootstrap_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_bootstrap(tmp_path)

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.index_path == "prediction/index.json"


def test_load_prediction_topology_roundtrip(tmp_path: Path) -> None:
    payload = PredictionTopologyPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        sector_count=2,
        committee_count=3,
        source_count=4,
        readiness_path="prediction/readiness.json",
        index_path="prediction/index.json",
        source_index_path="prediction/sources.json",
        sector_readiness_path="prediction/sectors.json",
        committee_readiness_path="prediction/committees.json",
        member_readiness_path_template="prediction/members/{bioguide}.json",
        member_context_path_template="prediction/member-context/{bioguide}.json",
        source_context_path_template="prediction/source-context/{source_key}.json",
        sector_context_path_template="prediction/sector-context/{sector}.json",
        committee_context_path_template="prediction/committee-context/{committee}.json",
    )
    pf = PlannedFile.from_bytes(prediction_topology_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_topology(tmp_path)

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.member_count == 1
    assert result.source_count == 4
    assert result.source_context_path_template == "prediction/source-context/{source_key}.json"


def test_load_prediction_sector_readiness_roundtrip(tmp_path: Path) -> None:
    payload = PredictionSectorReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        sector_count=1,
        sectors=[
            PredictionSectorReadinessRowPayload(
                sector_id="finance",
                label="Finance",
                member_count=1,
                ready_member_count=1,
                partial_member_count=0,
                blocked_member_count=0,
                readiness_rate=1,
                vote_coverage_rate=1,
                committee_jurisdiction_edge_count=1,
                holding_edge_count=1,
                transaction_edge_count=0,
                member_bioguide_ids=["S000148"],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_sector_readiness_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_sector_readiness(tmp_path)

    assert result.sector_count == 1
    assert result.sectors[0].sector_id == "finance"


def test_load_prediction_source_index_roundtrip(tmp_path: Path) -> None:
    payload = PredictionSourceIndexPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        source_count=1,
        sources=[
            PredictionSourceIndexRowPayload(
                source_key=_prediction_source_key(),
                source_type="committee_membership",
                source_id="cm-99",
                url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
                label="Committee membership",
                source_context_path=_prediction_source_context_path(),
                member_count=1,
                member_bioguide_ids=["S000148"],
                sector_ids=["finance"],
                committee_ids=["SSFI"],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_source_index_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_source_index(tmp_path)

    assert result.source_count == 1
    assert result.sources[0].source_id == "cm-99"


def test_load_prediction_source_context_roundtrip(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionSourceContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        source=PredictionSourceIndexRowPayload(
            source_key=_prediction_source_key(),
            source_type="committee_membership",
            source_id="cm-99",
            url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
            label="Committee membership",
            source_context_path=_prediction_source_context_path(),
            member_count=1,
            member_bioguide_ids=["S000148"],
            sector_ids=["finance"],
            committee_ids=["SSFI"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    pf = PlannedFile.from_bytes(
        prediction_source_context_path(_prediction_source_key()), _serialise(payload)
    )
    write_planned_files([pf], tmp_path)

    result = load_prediction_source_context(tmp_path, _prediction_source_key())

    assert result.source.source_key == _prediction_source_key()
    assert result.members[0].member_bioguide_id == "S000148"


def test_load_prediction_source_context_rejects_misnamed_source_key(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionSourceContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        source=PredictionSourceIndexRowPayload(
            source_key=_prediction_source_key(),
            source_type="committee_membership",
            source_id="cm-99",
            url="https://api.congress.gov/v3/committee/senate/SSFI?format=json",
            label="Committee membership",
            source_context_path=_prediction_source_context_path(),
            member_count=1,
            member_bioguide_ids=["S000148"],
            sector_ids=["finance"],
            committee_ids=["SSFI"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    wrong_key = "abcdefabcdefabcdefabcdef"
    pf = PlannedFile.from_bytes(prediction_source_context_path(wrong_key), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="source_key does not match requested key"):
        load_prediction_source_context(tmp_path, wrong_key)


def test_load_prediction_sector_context_roundtrip(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionSectorContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        sector=PredictionSectorReadinessRowPayload(
            sector_id="finance",
            label="Finance",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=0,
            transaction_edge_count=0,
            member_bioguide_ids=["S000148"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_sector_context_path("finance"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_sector_context(tmp_path, "finance")

    assert result.sector.sector_id == "finance"
    assert result.members[0].member_bioguide_id == "S000148"


def test_load_prediction_sector_context_rejects_misnamed_sector_id(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionSectorContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        sector=PredictionSectorReadinessRowPayload(
            sector_id="finance",
            label="Finance",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=0,
            transaction_edge_count=0,
            member_bioguide_ids=["S000148"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_sector_context_path("energy"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="sector_id does not match requested id"):
        load_prediction_sector_context(tmp_path, "energy")


def test_load_prediction_committee_readiness_roundtrip(tmp_path: Path) -> None:
    payload = PredictionCommitteeReadinessPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        committee_count=1,
        committees=[
            PredictionCommitteeReadinessRowPayload(
                committee_id="SSFI",
                label="Finance",
                member_count=1,
                ready_member_count=1,
                partial_member_count=0,
                blocked_member_count=0,
                readiness_rate=1,
                vote_coverage_rate=1,
                sector_ids=["finance"],
                member_bioguide_ids=["S000148"],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_committee_readiness_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_committee_readiness(tmp_path)

    assert result.committee_count == 1
    assert result.committees[0].committee_id == "SSFI"


def test_load_prediction_committee_context_roundtrip(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionCommitteeContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        committee=PredictionCommitteeReadinessRowPayload(
            committee_id="SSFI",
            label="Finance",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            sector_ids=["finance"],
            member_bioguide_ids=["S000148"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_committee_context_path("SSFI"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_committee_context(tmp_path, "SSFI")

    assert result.committee.committee_id == "SSFI"
    assert result.members[0].member_bioguide_id == "S000148"


def test_load_prediction_committee_context_rejects_misnamed_committee_id(
    tmp_path: Path,
) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionCommitteeContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        committee=PredictionCommitteeReadinessRowPayload(
            committee_id="SSFI",
            label="Finance",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            sector_ids=["finance"],
            member_bioguide_ids=["S000148"],
        ),
        members=[
            PredictionMemberContextPayload(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                member_bioguide_id="S000148",
                member_readiness=readiness,
                ontology_features=_prediction_ontology_features(),
                context_status="ready",
                source_anchors=[_prediction_source_anchor()],
                source_keys=[_prediction_source_key()],
                source_context_paths=[_prediction_source_context_path()],
            )
        ],
    )
    pf = PlannedFile.from_bytes(prediction_committee_context_path("SSBK"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="committee_id does not match requested id"):
        load_prediction_committee_context(tmp_path, "SSBK")


def test_load_prediction_readiness_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prediction_readiness(tmp_path)


def test_load_prediction_readiness_index_roundtrip(tmp_path: Path) -> None:
    payload = PredictionReadinessIndexPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_count=1,
        slug_to_bioguide={"charles-schumer": "S000148"},
        members_by_bioguide={
            "S000148": PredictionMemberReadinessPayload(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                chamber="senate",
                vote_count=1,
                yea_count=1,
                yea_rate=1,
                participation_rate=1,
                ontology_readiness_status="ready",
                readiness_status="ready",
            )
        },
    )
    pf = PlannedFile.from_bytes(prediction_readiness_index_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_readiness_index(tmp_path)

    assert result.slug_to_bioguide == {"charles-schumer": "S000148"}
    assert result.members_by_bioguide["S000148"].readiness_status == "ready"


def test_load_prediction_member_readiness_roundtrip(tmp_path: Path) -> None:
    payload = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    pf = PlannedFile.from_bytes(prediction_member_readiness_path("S000148"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_member_readiness(tmp_path, "S000148")

    assert result.bioguide_id == "S000148"
    assert result.participation_rate == 1


def test_load_prediction_member_readiness_rejects_misnamed_bioguide_id(
    tmp_path: Path,
) -> None:
    payload = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    pf = PlannedFile.from_bytes(prediction_member_readiness_path("A000001"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="bioguide_id does not match requested id"):
        load_prediction_member_readiness(tmp_path, "A000001")


def test_load_prediction_member_context_roundtrip(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionMemberContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_bioguide_id="S000148",
        member_readiness=readiness,
        context_status="ready",
        source_anchors=[_prediction_source_anchor()],
        source_keys=[_prediction_source_key()],
        source_context_paths=[_prediction_source_context_path()],
    )
    pf = PlannedFile.from_bytes(prediction_member_context_path("S000148"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_prediction_member_context(tmp_path, "S000148")

    assert result.member_bioguide_id == "S000148"
    assert result.member_readiness.participation_rate == 1


def test_load_prediction_member_context_rejects_misnamed_bioguide_id(tmp_path: Path) -> None:
    readiness = PredictionMemberReadinessPayload(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )
    payload = PredictionMemberContextPayload(
        snapshot_id=SNAPSHOT_ID,
        snapshot_date=SNAPSHOT_DATE,
        member_bioguide_id="S000148",
        member_readiness=readiness,
        context_status="ready",
        source_anchors=[_prediction_source_anchor()],
        source_keys=[_prediction_source_key()],
        source_context_paths=[_prediction_source_context_path()],
    )
    pf = PlannedFile.from_bytes(prediction_member_context_path("A000001"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    with pytest.raises(ValueError, match="bioguide_id does not match requested id"):
        load_prediction_member_context(tmp_path, "A000001")


# ── load_member_history ────────────────────────────────────────────


def test_load_member_history_roundtrip(tmp_path: Path) -> None:
    payload = _member_history_payload()
    pf = PlannedFile.from_bytes(member_history_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_history(tmp_path, payload.slug)
    assert result.slug == "charles-schumer"
    assert result.snapshots[0].score_total == 72.0
    assert result.events[0].evidence_card_id == "ec-001"


def test_load_member_history_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_history(tmp_path, "ghost-member")


def test_load_member_change_summary_roundtrip(tmp_path: Path) -> None:
    payload = _member_change_summary_payload()
    pf = PlannedFile.from_bytes(member_change_summary_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_change_summary(tmp_path, payload.slug)
    assert result.slug == "charles-schumer"
    assert result.latest_score_total == 72.0
    assert result.top_dimension_changes[0].dimension == "conflict_of_interest_risk"


def test_load_member_change_summary_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_change_summary(tmp_path, "ghost-member")


def test_load_member_trend_summary_roundtrip(tmp_path: Path) -> None:
    payload = MemberTrendSummaryPayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        latest_snapshot_date=SNAPSHOT_DATE,
        windows=[
            MemberTrendWindowPayload(
                window_key="12w",
                label="Last 12 weeks",
                requested_days=84,
                has_full_window=False,
                start_snapshot_date=SNAPSHOT_DATE,
                end_snapshot_date=SNAPSHOT_DATE,
                current_score_total=72.0,
                previous_score_total=None,
                score_total_delta=72.0,
                top_dimension_changes=[],
                recent_event_count=1,
                top_evidence_card_ids=["ec-001"],
            )
        ],
    )
    pf = PlannedFile.from_bytes(member_trend_summary_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_trend_summary(tmp_path, payload.slug)
    assert result.slug == "charles-schumer"
    assert result.windows[0].window_key == "12w"
    assert result.windows[0].top_evidence_card_ids == ["ec-001"]


def test_load_member_trend_summary_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_trend_summary(tmp_path, "ghost-member")


def test_load_member_history_chart_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryChartPayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        latest_snapshot_id=SNAPSHOT_ID,
        latest_snapshot_date=SNAPSHOT_DATE,
        default_preset_key="latest",
        points=[
            MemberHistoryChartPoint(
                snapshot_id=SNAPSHOT_ID,
                snapshot_date=SNAPSHOT_DATE,
                score_total=72.0,
                score_total_delta=-5.0,
                event_count=1,
            )
        ],
        compare_presets=[
            MemberHistoryComparePreset(
                preset_key="latest",
                label="Latest change",
                start_snapshot_id="2026-04-06",
                start_snapshot_date=date(2026, 4, 6),
                end_snapshot_id=SNAPSHOT_ID,
                end_snapshot_date=SNAPSHOT_DATE,
                has_full_window=True,
                score_total_delta=-5.0,
                top_evidence_card_ids=["ec-001"],
            )
        ],
    )
    pf = PlannedFile.from_bytes(member_history_chart_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_history_chart(tmp_path, payload.slug)
    assert result.slug == "charles-schumer"
    assert result.default_preset_key == "latest"
    assert result.points[0].snapshot_id == SNAPSHOT_ID
    assert result.compare_presets[0].preset_key == "latest"


def test_load_member_history_chart_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_history_chart(tmp_path, "ghost-member")


def test_load_member_preset_compare_roundtrip(tmp_path: Path) -> None:
    payload = {
        "bioguide_id": "S000148",
        "name": "Charles Schumer",
        "slug": "charles-schumer",
        "state": "NY",
        "district": None,
        "chamber": "senate",
        "party": "Democrat",
        "start_snapshot_id": "2026-04-06",
        "start_snapshot_date": "2026-04-06",
        "end_snapshot_id": SNAPSHOT_ID,
        "end_snapshot_date": SNAPSHOT_DATE.isoformat(),
        "summary": _member_change_summary_payload().model_dump(mode="json"),
        "evidence_cards": [_evidence_payload().model_dump(mode="json")],
    }
    pf = PlannedFile.from_bytes(
        member_preset_compare_path("charles-schumer", "latest"),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([pf], tmp_path)

    result = load_member_preset_compare(tmp_path, "charles-schumer", "latest")
    assert result.slug == "charles-schumer"
    assert result.start_snapshot_id == "2026-04-06"
    assert result.evidence_cards[0].evidence_card_id == "ec-001"


def test_load_member_preset_compare_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_preset_compare(tmp_path, "ghost-member", "latest")


def test_load_member_history_page_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryPagePayload(
        member_page=MemberPagePayload(
            profile=_member_payload(),
            top_evidence_cards=[_evidence_payload()],
            recent_evidence_cards=[_evidence_payload()],
        ),
        history=_member_history_payload(),
        recent_change=_member_change_summary_payload(),
        chart=MemberHistoryChartPayload(
            bioguide_id="S000148",
            name="Charles Schumer",
            slug="charles-schumer",
            state="NY",
            district=None,
            chamber="senate",
            party="Democrat",
            latest_snapshot_id=SNAPSHOT_ID,
            latest_snapshot_date=SNAPSHOT_DATE,
            default_preset_key="latest",
            points=[],
            compare_presets=[],
        ),
        default_window_compare={
            "bioguide_id": "S000148",
            "name": "Charles Schumer",
            "slug": "charles-schumer",
            "state": "NY",
            "district": None,
            "chamber": "senate",
            "party": "Democrat",
            "start_snapshot_id": "2026-04-06",
            "start_snapshot_date": "2026-04-06",
            "end_snapshot_id": SNAPSHOT_ID,
            "end_snapshot_date": SNAPSHOT_DATE.isoformat(),
            "summary": _member_change_summary_payload().model_dump(mode="json"),
            "evidence_cards": [_evidence_payload().model_dump(mode="json")],
        },
        trend_summary=MemberTrendSummaryPayload(
            bioguide_id="S000148",
            name="Charles Schumer",
            slug="charles-schumer",
            state="NY",
            district=None,
            chamber="senate",
            party="Democrat",
            latest_snapshot_date=SNAPSHOT_DATE,
            windows=[
                MemberTrendWindowPayload(
                    window_key="4w",
                    label="Last 4 weeks",
                    requested_days=28,
                    has_full_window=True,
                    start_snapshot_date=date(2026, 4, 6),
                    end_snapshot_date=SNAPSHOT_DATE,
                    current_score_total=72.0,
                    previous_score_total=60.0,
                    score_total_delta=12.0,
                    top_dimension_changes=[],
                    recent_event_count=1,
                    top_evidence_card_ids=["ec-001"],
                )
            ],
        ),
        history_evidence_cards=[_evidence_payload()],
        snapshot_index={
            "latest_snapshot_id": SNAPSHOT_ID,
            "snapshots": [
                {
                    "snapshot_id": SNAPSHOT_ID,
                    "snapshot_date": SNAPSHOT_DATE.isoformat(),
                    "published_at": "2026-04-13T00:00:00Z",
                    "root_sha256": "a" * 64,
                    "total_files": 4,
                    "total_bytes": 1200,
                }
            ],
        },
    )
    pf = PlannedFile.from_bytes(
        member_history_page_path("charles-schumer"),
        json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
    )
    write_planned_files([pf], tmp_path)

    result = load_member_history_page(tmp_path, "charles-schumer")
    assert result.member_page.profile.slug == "charles-schumer"
    assert result.default_window_compare.start_snapshot_id == "2026-04-06"
    assert result.history_evidence_cards[0].evidence_card_id == "ec-001"


def test_load_member_history_page_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_history_page(tmp_path, "ghost-member")


def test_load_snapshot_preset_compare_roundtrip(tmp_path: Path) -> None:
    payload = {
        "start_snapshot_id": "2026-04-06",
        "start_snapshot_date": "2026-04-06",
        "end_snapshot_id": SNAPSHOT_ID,
        "end_snapshot_date": SNAPSHOT_DATE.isoformat(),
        "top_changes": [],
        "recent_events": [],
        "recent_evidence_card_ids": [],
        "featured_member_changes": [_member_change_summary_payload().model_dump(mode="json")],
    }
    pf = PlannedFile.from_bytes(
        snapshot_preset_compare_path("latest"),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([pf], tmp_path)

    result = load_snapshot_preset_compare(tmp_path, "latest")
    assert result.start_snapshot_id == "2026-04-06"
    assert result.end_snapshot_id == SNAPSHOT_ID
    assert result.featured_member_changes[0].slug == "charles-schumer"


def test_load_snapshot_preset_compare_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_snapshot_preset_compare(tmp_path, "cycle")


def test_load_snapshot_preset_compare_rejects_invalid_preset_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown snapshot preset key"):
        load_snapshot_preset_compare(tmp_path, "../latest")


def test_load_movement_window_roundtrip(tmp_path: Path) -> None:
    payload = _movement_window_payload()
    pf = PlannedFile.from_bytes(movement_window_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_movement_window(tmp_path)
    assert result.latest_snapshot_id == SNAPSHOT_ID
    assert result.previous_snapshot_id == "2026-04-06"
    assert result.top_changes[0].slug == "charles-schumer"


def test_load_named_movement_window_roundtrip(tmp_path: Path) -> None:
    payload = _movement_window_payload().model_copy(
        update={"window_key": "4w", "has_full_window": False}
    )
    pf = PlannedFile.from_bytes(movement_window_path("4w"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_movement_window(tmp_path, "4w")
    assert result.window_key == "4w"
    assert result.has_full_window is False
    assert result.latest_snapshot_id == SNAPSHOT_ID


def test_load_dimension_movement_window_roundtrip(tmp_path: Path) -> None:
    payload = _movement_window_payload().model_copy(update={"dimension": "transparency_risk"})
    pf = PlannedFile.from_bytes(
        movement_window_path(dimension="transparency_risk"),
        _serialise(payload),
    )
    write_planned_files([pf], tmp_path)

    result = load_movement_window(tmp_path, dimension="transparency_risk")
    assert result.dimension == "transparency_risk"
    assert result.latest_snapshot_id == SNAPSHOT_ID


def test_load_history_preset_range_roundtrip(tmp_path: Path) -> None:
    payload = HistoryPresetRangePayload(
        preset=SnapshotComparePresetPayload(
            preset_key="latest",
            label="Latest change",
            start_snapshot_id="2026-04-06",
            start_snapshot_date="2026-04-06",
            end_snapshot_id=SNAPSHOT_ID,
            end_snapshot_date=SNAPSHOT_DATE,
            has_full_window=True,
        ),
        movement_window=_movement_window_payload(),
        snapshot_compare=SnapshotComparePayload(
            start_snapshot_id="2026-04-06",
            start_snapshot_date="2026-04-06",
            end_snapshot_id=SNAPSHOT_ID,
            end_snapshot_date=SNAPSHOT_DATE,
            top_changes=[],
            recent_events=[],
            recent_evidence_card_ids=[],
            featured_member_changes=[],
        ),
    )
    pf = PlannedFile.from_bytes(history_preset_range_path("latest"), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_history_preset_range(tmp_path, "latest")
    assert result.preset.preset_key == "latest"
    assert result.movement_window.latest_snapshot_id == SNAPSHOT_ID
    assert result.snapshot_compare.end_snapshot_id == SNAPSHOT_ID


def test_load_movement_window_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_movement_window(tmp_path)


def test_load_snapshot_index_roundtrip(tmp_path: Path) -> None:
    payload = {
        "latest_snapshot_id": "2026-04-13",
        "snapshots": [
            {
                "snapshot_id": "2026-04-13",
                "snapshot_date": "2026-04-13",
                "published_at": "2026-04-13T00:00:00Z",
                "root_sha256": "a" * 64,
                "total_files": 4,
                "total_bytes": 1200,
            }
        ],
    }
    file = PlannedFile.from_bytes(
        snapshot_index_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)

    result = load_snapshot_index(tmp_path)
    assert result.latest_snapshot_id == "2026-04-13"
    assert result.snapshots[0].root_sha256 == "a" * 64


def test_load_history_bootstrap_roundtrip(tmp_path: Path) -> None:
    payload = HistoryBootstrapPayload(
        snapshot_index={
            "latest_snapshot_id": "2026-04-13",
            "snapshots": [
                {
                    "snapshot_id": "2026-04-13",
                    "snapshot_date": "2026-04-13",
                    "published_at": "2026-04-13T00:00:00Z",
                    "root_sha256": "a" * 64,
                    "total_files": 4,
                    "total_bytes": 1200,
                }
            ],
        },
        movement_window=_movement_window_payload(),
        default_compare_preset_key="latest",
        compare_presets=[
            SnapshotComparePresetPayload(
                preset_key="latest",
                label="Latest change",
                start_snapshot_id="2026-04-06",
                start_snapshot_date="2026-04-06",
                end_snapshot_id="2026-04-13",
                end_snapshot_date="2026-04-13",
                has_full_window=True,
            )
        ],
        featured_member_changes=[_member_change_summary_payload()],
    )
    file = PlannedFile.from_bytes(
        history_bootstrap_path(),
        _serialise(payload),
    )
    write_planned_files([file], tmp_path)

    result = load_history_bootstrap(tmp_path)
    assert result.snapshot_index.latest_snapshot_id == "2026-04-13"
    assert result.movement_window.latest_snapshot_id == SNAPSHOT_ID
    assert result.default_compare_preset_key == "latest"
    assert result.compare_presets[0].preset_key == "latest"
    assert result.featured_member_changes[0].slug == "charles-schumer"


# ── load_zip_feed ──────────────────────────────────────────────────


def test_load_zip_feed_roundtrip(tmp_path: Path) -> None:
    payload = _zip_payload()
    pf = PlannedFile.from_bytes(zip_path(payload.zip_code), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_zip_feed(tmp_path, "10001")
    assert result.zip_code == "10001"
    assert result.congressional_district == "NY-12"
    assert result.ambiguity_note is None
    assert result.members == []


def test_load_zip_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_zip_feed(tmp_path, "99999")


def test_load_zip_feed_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, zip_path("00000"), {"bad": "data"})
    with pytest.raises(Exception):
        load_zip_feed(tmp_path, "00000")


def test_load_zip_entry_roundtrip(tmp_path: Path) -> None:
    from src.api.contracts import ZipEntryPayload

    payload = ZipEntryPayload(
        zip_feed=_zip_payload(),
        member_lookup_entries=_current_member_lookup_payload().members,
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-13T00:00:00Z",
            "root_sha256": "c" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 1,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
    )
    pf = PlannedFile.from_bytes(zip_entry_path(payload.zip_feed.zip_code), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_zip_entry(tmp_path, "10001")
    assert result.zip_feed.zip_code == "10001"
    assert result.member_lookup_entries[0].slug == "charles-schumer"
    assert result.snapshot.snapshot_id == SNAPSHOT_ID


def test_load_zip_entry_alias_serialized_roundtrip(tmp_path: Path) -> None:
    from src.api.contracts import ZipEntryPayload

    payload = ZipEntryPayload(
        zip_feed=_zip_payload(),
        member_lookup_entries=_current_member_lookup_payload().members,
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-13T00:00:00Z",
            "root_sha256": "c" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 1,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
    )
    pf = PlannedFile.from_bytes(
        zip_entry_path(payload.zip_feed.zip_code), serialize_payload(payload)
    )
    write_planned_files([pf], tmp_path)

    result = load_zip_entry(tmp_path, "10001")
    assert result.member_lookup_entries[0].slug == "charles-schumer"


def test_load_zip_entry_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_zip_entry(tmp_path, "99999")


# ── load_homepage_feed ────────────────────────────────────────────


def _homepage_feed_payload() -> HomepageFeedPayload:
    return HomepageFeedPayload(
        snapshot_date=SNAPSHOT_DATE,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                dimension="conflict_of_interest_risk",
                score_delta=-5.0,
                abs_delta=5.0,
                event_count=1,
                top_evidence_card_ids=["ec-001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-001",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                member_slug="charles-schumer",
                dimension="conflict_of_interest_risk",
                score_delta=-5.0,
                short_explanation="Example homepage feed event.",
                evidence_card_id="ec-001",
                occurred_at=SNAPSHOT_DATE,
            )
        ],
        recent_evidence_card_ids=["ec-001"],
    )


def test_load_homepage_feed_roundtrip(tmp_path: Path) -> None:
    payload = _homepage_feed_payload()
    pf = PlannedFile.from_bytes(HOMEPAGE_FEED_PATH, _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_homepage_feed(tmp_path)
    assert result.snapshot_date == SNAPSHOT_DATE
    assert result.top_changes[0].slug == "charles-schumer"
    assert result.recent_events[0].feed_event_id == "event-001"
    assert result.recent_evidence_card_ids == ["ec-001"]


def test_load_homepage_bootstrap_roundtrip(tmp_path: Path) -> None:
    payload = HomepageBootstrapPayload(
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-13T00:00:00Z",
            "root_sha256": "c" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 0,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
        movement={
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "top_changes": _homepage_feed_payload().top_changes,
            "recent_events": _homepage_feed_payload().recent_events,
            "recent_evidence_card_ids": ["ec-001"],
        },
        featured_lookup_entries=_current_member_lookup_payload().members,
    )
    pf = PlannedFile.from_bytes(homepage_bootstrap_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_homepage_bootstrap(tmp_path)
    assert result.snapshot.snapshot_id == SNAPSHOT_ID
    assert result.movement.top_changes[0].slug == "charles-schumer"
    assert result.featured_lookup_entries[0].slug == "charles-schumer"


def test_load_homepage_bootstrap_alias_serialized_roundtrip(tmp_path: Path) -> None:
    payload = HomepageBootstrapPayload(
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-13T00:00:00Z",
            "root_sha256": "c" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 0,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
        movement={
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "top_changes": _homepage_feed_payload().top_changes,
            "recent_events": _homepage_feed_payload().recent_events,
            "recent_evidence_card_ids": ["ec-001"],
        },
        featured_lookup_entries=_current_member_lookup_payload().members,
    )
    pf = PlannedFile.from_bytes(homepage_bootstrap_path(), serialize_payload(payload))
    write_planned_files([pf], tmp_path)

    result = load_homepage_bootstrap(tmp_path)
    assert result.featured_lookup_entries[0].slug == "charles-schumer"


def test_load_homepage_bootstrap_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_homepage_bootstrap(tmp_path)


def test_load_homepage_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_homepage_feed(tmp_path)


def test_load_homepage_feed_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"{{not json}}")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_homepage_feed(tmp_path)


def test_load_homepage_feed_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, HOMEPAGE_FEED_PATH, {"wrong": "fields"})
    with pytest.raises(Exception):
        load_homepage_feed(tmp_path)


def test_load_homepage_feed_empty_featured_slugs(tmp_path: Path) -> None:
    payload = HomepageFeedPayload(
        snapshot_date=SNAPSHOT_DATE,
        top_changes=[],
        recent_events=[],
        recent_evidence_card_ids=[],
    )
    pf = PlannedFile.from_bytes(HOMEPAGE_FEED_PATH, _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_homepage_feed(tmp_path)
    assert result.top_changes == []
    assert result.recent_events == []
    assert result.recent_evidence_card_ids == []


# ── load_current_member_lookup ─────────────────────────────────────


def test_load_current_member_lookup_roundtrip(tmp_path: Path) -> None:
    payload = _current_member_lookup_payload()
    pf = PlannedFile.from_bytes(current_member_lookup_path(), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_current_member_lookup(tmp_path)
    assert result.snapshot_date == SNAPSHOT_DATE
    assert result.members[0].bioguide_id == "S000148"
    assert result.members[0].search_name == "charles schumer"


def test_load_current_member_lookup_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_current_member_lookup(tmp_path)


def test_load_current_member_lookup_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, current_member_lookup_path(), {"bad": "data"})
    with pytest.raises(Exception):
        load_current_member_lookup(tmp_path)


def test_load_current_member_lookup_rejects_stale_search_name(tmp_path: Path) -> None:
    payload = _current_member_lookup_payload().model_dump(mode="json", by_alias=True)
    payload["m"][0]["q"] = "stale lookup name"
    _write_json(tmp_path, current_member_lookup_path(), payload)

    with pytest.raises(ValueError, match="stale search_name"):
        load_current_member_lookup(tmp_path)


def test_load_current_member_lookup_rejects_duplicate_slug(tmp_path: Path) -> None:
    payload = _current_member_lookup_payload().model_dump(mode="json", by_alias=True)
    payload["m"].append({**payload["m"][0], "b": "S000149"})
    _write_json(tmp_path, current_member_lookup_path(), payload)

    with pytest.raises(ValueError, match="duplicate slug"):
        load_current_member_lookup(tmp_path)


# ── load_manifest ──────────────────────────────────────────────────


def test_load_manifest_roundtrip(tmp_path: Path) -> None:
    data_file = PlannedFile.from_bytes("members/test.json", b'{"key":"value"}')
    manifest = _manifest([data_file])
    pf = PlannedFile.from_bytes(manifest_path(SNAPSHOT_ID), _serialise(manifest))
    write_planned_files([pf], tmp_path)

    result = load_manifest(tmp_path, SNAPSHOT_ID)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.total_files == 1
    assert result.entries[0].path == "members/test.json"
    assert result.verify_counts() is True
    assert result.root_sha256 == manifest.root_sha256


def test_load_manifest_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path, "1970-01-01")


def test_load_manifest_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / manifest_path("2000-01-01")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_manifest(tmp_path, "2000-01-01")


# ── list_artifact_paths ────────────────────────────────────────────


def test_list_artifact_paths_order(tmp_path: Path) -> None:
    files = [
        PlannedFile.from_bytes("members/alice.json", b"{}"),
        PlannedFile.from_bytes("zip/10001.json", b"{}"),
        PlannedFile.from_bytes("evidence/ec-1.json", b"{}"),
    ]
    manifest = _manifest(files)
    paths = list_artifact_paths(manifest)
    assert paths == ["members/alice.json", "zip/10001.json", "evidence/ec-1.json"]


def test_list_artifact_paths_empty() -> None:
    manifest = SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 13),
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    assert list_artifact_paths(manifest) == []


# ── manifest-backed metadata helpers ───────────────────────────────


def test_manifest_snapshot_date_uses_snapshot_id_when_isoformatted() -> None:
    manifest = SnapshotManifest(
        snapshot_id="2026-04-13",
        created_at=datetime(2026, 4, 14, 9, 30, 0),
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    assert manifest_snapshot_date(manifest) == date(2026, 4, 13)


def test_manifest_snapshot_date_falls_back_to_created_at_for_non_date_ids() -> None:
    manifest = SnapshotManifest(
        snapshot_id="snapshot-prod-001",
        created_at=datetime(2026, 4, 14, 9, 30, 0),
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    assert manifest_snapshot_date(manifest) == date(2026, 4, 14)


def test_manifest_published_at_returns_manifest_created_at() -> None:
    created_at = datetime(2026, 4, 13, 12, 34, 56)
    manifest = SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=created_at,
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    assert manifest_published_at(manifest) == created_at


# ── latest_snapshot_id ────────────────────────────────────────────


def _write_manifest_for_snapshot(root: Path, snapshot_id: str) -> None:
    """Write a minimal manifest into the expected snapshot directory."""
    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    pf = PlannedFile.from_bytes(manifest_path(snapshot_id), _serialise(manifest))
    write_planned_files([pf], root)


def test_latest_snapshot_id_single(tmp_path: Path) -> None:
    _write_manifest_for_snapshot(tmp_path, "2026-04-13")
    assert latest_snapshot_id(tmp_path) == "2026-04-13"


def test_latest_snapshot_id_picks_latest(tmp_path: Path) -> None:
    for sid in ("2026-01-01", "2026-04-13", "2026-03-10"):
        _write_manifest_for_snapshot(tmp_path, sid)
    assert latest_snapshot_id(tmp_path) == "2026-04-13"


def test_list_snapshot_ids_returns_all_sorted_ids(tmp_path: Path) -> None:
    for sid in ("2026-01-01", "2026-04-13", "2026-03-10"):
        _write_manifest_for_snapshot(tmp_path, sid)
    assert list_snapshot_ids(tmp_path) == ["2026-01-01", "2026-03-10", "2026-04-13"]


def test_latest_snapshot_id_no_snapshots_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No snapshots directory"):
        latest_snapshot_id(tmp_path)


def test_latest_snapshot_id_empty_snapshots_dir(tmp_path: Path) -> None:
    (tmp_path / "snapshots").mkdir()
    with pytest.raises(FileNotFoundError, match="No snapshot directories"):
        latest_snapshot_id(tmp_path)


def test_latest_snapshot_id_ignores_files_in_snapshots_dir(tmp_path: Path) -> None:
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "README.txt").write_text("notes")
    _write_manifest_for_snapshot(tmp_path, "2026-02-01")
    assert latest_snapshot_id(tmp_path) == "2026-02-01"


# ── load_latest_manifest ───────────────────────────────────────────


def test_load_latest_manifest_returns_newest(tmp_path: Path) -> None:
    for sid in ("2026-01-15", "2026-04-13", "2026-02-28"):
        _write_manifest_for_snapshot(tmp_path, sid)
    result = load_latest_manifest(tmp_path)
    assert result.snapshot_id == "2026-04-13"


def test_load_latest_manifest_no_snapshots(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_latest_manifest(tmp_path)


def test_load_latest_manifest_counts_verify(tmp_path: Path) -> None:
    data_file = PlannedFile.from_bytes("members/test.json", b'{"key":"value"}')
    entries = [
        ManifestEntry(path=data_file.path, sha256=data_file.sha256, size_bytes=data_file.size_bytes)
    ]
    manifest = SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 13, 0, 0, 0),
        entries=entries,
        total_files=1,
        total_bytes=data_file.size_bytes,
        root_sha256=manifest_root_sha256(entries),
    )
    pf = PlannedFile.from_bytes(manifest_path(SNAPSHOT_ID), _serialise(manifest))
    write_planned_files([pf], tmp_path)

    result = load_latest_manifest(tmp_path)
    assert result.verify_counts() is True
    assert result.total_files == 1
    assert result.root_sha256 == manifest.root_sha256


def test_load_latest_snapshot_metadata_returns_manifest_backed_values(tmp_path: Path) -> None:
    _write_manifest_for_snapshot(tmp_path, "2026-04-13")
    _write_manifest_for_snapshot(tmp_path, "2026-04-14")

    snapshot_date, published_at = load_latest_snapshot_metadata(tmp_path)
    assert snapshot_date == date(2026, 4, 14)
    assert published_at == datetime(2026, 1, 1, 0, 0, 0)


def test_load_latest_snapshot_metadata_uses_created_at_when_snapshot_id_not_a_date(
    tmp_path: Path,
) -> None:
    manifest = SnapshotManifest(
        snapshot_id="release-42",
        created_at=datetime(2026, 4, 15, 7, 45, 0),
        entries=[],
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256([]),
    )
    pf = PlannedFile.from_bytes(manifest_path("release-42"), _serialise(manifest))
    write_planned_files([pf], tmp_path)

    snapshot_date, published_at = load_latest_snapshot_metadata(tmp_path)
    assert snapshot_date == date(2026, 4, 15)
    assert published_at == datetime(2026, 4, 15, 7, 45, 0)


# ── Integration: write then read back all artifact types ───────────


def test_full_roundtrip_via_write_and_read(tmp_path: Path) -> None:
    """Write member, evidence, zip, and manifest; read each back and validate."""
    member = _member_payload()
    evidence = _evidence_payload()
    feed = _zip_payload()

    files = [
        PlannedFile.from_bytes(member_path(member.slug), _serialise(member)),
        PlannedFile.from_bytes(evidence_path(evidence.evidence_card_id), _serialise(evidence)),
        PlannedFile.from_bytes(zip_path(feed.zip_code), _serialise(feed)),
    ]
    manifest = _manifest(files)
    files.append(PlannedFile.from_bytes(manifest_path(SNAPSHOT_ID), _serialise(manifest)))
    write_planned_files(files, tmp_path)

    assert load_member_profile(tmp_path, "charles-schumer").bioguide_id == "S000148"
    assert load_evidence_card(tmp_path, "ec-001").evidence_card_id == "ec-001"
    assert load_zip_feed(tmp_path, "10001").zip_code == "10001"
    loaded_manifest = load_manifest(tmp_path, SNAPSHOT_ID)
    assert loaded_manifest.verify_counts() is True
    assert loaded_manifest.root_sha256 == manifest.root_sha256
    paths = list_artifact_paths(loaded_manifest)
    assert len(paths) == 3  # manifest itself not in the entry list


# ── Path traversal rejection ─────────────────────────────────────


class TestSafeSubpath:
    """_safe_subpath must reject any relative path that escapes root."""

    def test_normal_path_accepted(self, tmp_path: Path) -> None:
        result = _safe_subpath(tmp_path, "members/alice.json")
        assert result == tmp_path / "members/alice.json"

    def test_dotdot_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            _safe_subpath(tmp_path, "../../../etc/passwd")

    def test_dotdot_inside_segment_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            _safe_subpath(tmp_path, "members/../../secret.json")

    def test_intra_root_dotdot_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="confined"):
            _safe_subpath(tmp_path, "members/../identity/current-member-lookup.json")

    def test_null_byte_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="null byte"):
            _safe_subpath(tmp_path, "members/evil\x00.json")

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            _safe_subpath(tmp_path, "/etc/passwd")


class TestLoaderPathTraversal:
    """Loaders must reject traversal slugs before touching the filesystem."""

    def test_member_profile_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_member_profile(tmp_path, "../../etc/passwd")

    def test_evidence_card_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_evidence_card(tmp_path, "../../../etc/shadow")

    def test_zip_feed_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_zip_feed(tmp_path, "../../../../tmp/x")

    def test_manifest_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_manifest(tmp_path, "../../../etc/passwd")
