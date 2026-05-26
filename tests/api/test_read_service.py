from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import (
    get_current_member_lookup,
    get_evidence,
    get_ontology_graph,
    get_ontology_index,
    get_ontology_member_features,
    get_ontology_member_graph,
    get_homepage,
    get_last_updated,
    get_member,
    get_member_page,
    get_prediction_bootstrap,
    get_prediction_committee_context,
    get_prediction_committee_readiness,
    get_prediction_member_context,
    get_prediction_member_readiness,
    get_prediction_readiness,
    get_prediction_readiness_index,
    get_prediction_sector_context,
    get_prediction_sector_readiness,
    get_prediction_source_context,
    get_prediction_source_index,
    get_prediction_topology,
    get_search_session,
    get_zip,
    search_current_member_lookup,
)
from src.export.contracts import SourceAnchor
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.writer import (
    current_member_lookup_path,
    evidence_path,
    ontology_edges_path,
    ontology_index_path,
    ontology_member_edges_path,
    ontology_member_features_path,
    prediction_bootstrap_path,
    prediction_committee_readiness_path,
    prediction_readiness_index_path,
    prediction_readiness_path,
    prediction_sector_readiness_path,
    prediction_source_index_path,
    prediction_topology_path,
    serialize_payload,
)
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.contracts import (
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessPayload,
)
from src.runtime.inspect import load_latest_local_snapshot_metadata
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_snapshot,
    make_zip_feed,
)


def _write_homepage_feed(root: Path) -> None:
    payload = HomepageFeedPayload(
        snapshot_date=make_zip_feed().snapshot_date,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=5.0,
                abs_delta=5.0,
                event_count=1,
                top_evidence_card_ids=["ec-0001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-0001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=5.0,
                short_explanation="Test explanation.",
                evidence_card_id="ec-0001",
                occurred_at=make_zip_feed().snapshot_date,
            )
        ],
        recent_evidence_card_ids=["ec-0001"],
    )
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload.model_dump(mode="json")), encoding="utf-8")


def _ontology_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-api-001",
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
                source_id="cm-api-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _committee_sector_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-api-002",
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
                source_id="cm-api-2",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee jurisdiction",
            )
        ],
    )


def _prediction_readiness() -> PredictionReadinessPayload:
    return PredictionReadinessPayload(
        snapshot_id="2026-01-01",
        snapshot_date=make_zip_feed().snapshot_date,
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


def _prediction_readiness_with_member() -> PredictionReadinessPayload:
    return PredictionReadinessPayload(
        snapshot_id="2026-01-01",
        snapshot_date=make_zip_feed().snapshot_date,
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
                latest_vote_date=make_zip_feed().snapshot_date,
                ontology_readiness_status="ready",
                ontology_edge_count=2,
                readiness_status="ready",
            )
        ],
    )


def test_get_member_returns_wrapped_payload_with_batch_meta(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed()])

    result = get_member("nancy-pelosi", snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_member_returns_not_found_for_missing_slug(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_member("ghost-member", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member"
    assert result.identifier == "ghost-member"


def test_get_evidence_returns_not_found_for_missing_card(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_evidence("ec-missing", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "evidence"
    assert result.identifier == "ec-missing"


def test_get_evidence_returns_display_ready_source_metadata(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_evidence("ec-0001", snapshot_root=tmp_path)

    assert result.ok is True
    data = result.data.model_dump(mode="json")
    assert data["source_count"] == 1
    assert data["official_source_count"] == 1
    assert data["primary_source_url"] == (
        "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf"
    )
    assert data["primary_source_label"] == "2025 PTR filing"


def test_get_evidence_rejects_nonzero_claim_bearing_anchor_without_https(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    invalid_card = make_evidence_card().model_copy(
        update={
            "source_anchors": [
                SourceAnchor(
                    source_type="financial_disclosure",
                    source_id="fd-001",
                    url=None,
                    label="2025 PTR filing",
                )
            ]
        }
    )
    dest = tmp_path / evidence_path(invalid_card.evidence_card_id)
    dest.write_bytes(serialize_payload(invalid_card))

    with pytest.raises(ValueError, match="financial_disclosure.*fd-001"):
        get_evidence(invalid_card.evidence_card_id, snapshot_root=tmp_path)


def test_get_zip_returns_not_found_for_missing_zip(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_zip("99999", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "zip"
    assert result.identifier == "99999"


def test_get_homepage_returns_wrapped_payload_with_batch_meta(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_homepage(snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.recent_evidence_card_ids == ["ec-0001"]
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_homepage_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_homepage(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "homepage"
    assert result.identifier == "current"


def test_get_homepage_returns_not_found_for_malformed_artifact(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    (tmp_path / HOMEPAGE_FEED_PATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / HOMEPAGE_FEED_PATH).write_text("{not valid json", encoding="utf-8")

    result = get_homepage(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "homepage"
    assert result.identifier == "current"


def test_get_current_member_lookup_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_current_member_lookup(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.members[0].slug == "nancy-pelosi"
    assert result.data.members[0].search_name == "nancy pelosi"


def test_get_current_member_lookup_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    result = get_current_member_lookup(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "current_member_lookup"
    assert result.identifier == "current"


def test_get_current_member_lookup_returns_not_found_for_malformed_artifact(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    (tmp_path / current_member_lookup_path()).write_text("{not valid json", encoding="utf-8")

    result = get_current_member_lookup(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "current_member_lookup"
    assert result.identifier == "current"


def test_search_current_member_lookup_returns_not_found_for_malformed_artifact(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    (tmp_path / current_member_lookup_path()).write_text("{not valid json", encoding="utf-8")

    result = search_current_member_lookup("nancy", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "current_member_lookup"
    assert result.identifier == "current"


def test_get_search_session_returns_not_found_for_malformed_lookup_artifact(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    (tmp_path / current_member_lookup_path()).write_text("{not valid json", encoding="utf-8")

    result = get_search_session("nancy", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "search_session"
    assert result.identifier == "current"


def test_get_ontology_graph_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_ontology_graph(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.edge_count == 1
    assert result.data.edges[0].edge_id == "ont-edge-api-001"


def test_get_ontology_graph_returns_stable_empty_payload_when_explicitly_published(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, ontology_edges=[])

    result = get_ontology_graph(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.edge_count == 0
    assert result.data.edges == []


def test_get_ontology_graph_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_ontology_graph(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "ontology_graph"
    assert result.identifier == "current"


def test_get_ontology_index_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_ontology_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.edge_count == 1
    assert result.data.member_count == 1
    assert result.data.available_member_graphs == ["P000197"]


def test_get_ontology_index_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_ontology_index(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "ontology_index"
    assert result.identifier == "current"


@pytest.mark.parametrize(
    ("artifact_path", "getter", "resource_type"),
    [
        (ontology_edges_path(), get_ontology_graph, "ontology_graph"),
        (ontology_index_path(), get_ontology_index, "ontology_index"),
    ],
)
def test_ontology_collection_endpoints_return_not_found_for_malformed_artifact(
    tmp_path: Path,
    artifact_path: str,
    getter,
    resource_type: str,
) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])
    (tmp_path / artifact_path).write_text("{not valid json", encoding="utf-8")

    result = getter(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == resource_type
    assert result.identifier == "current"


def test_get_ontology_member_graph_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_ontology_member_graph("P000197", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_bioguide_id == "P000197"
    assert result.data.edge_count == 1
    assert result.data.edges[0].edge_id == "ont-edge-api-001"


def test_get_member_page_attaches_ontology_features_when_available(tmp_path: Path) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_member_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.ontology_features is not None
    assert result.data.ontology_features.member_bioguide_id == "P000197"


def test_get_member_page_attaches_prediction_readiness_when_available(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    path = tmp_path / prediction_readiness_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_payload(_prediction_readiness_with_member()))

    result = get_member_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.prediction_readiness is not None
    assert result.data.prediction_readiness.bioguide_id == "P000197"
    assert result.data.prediction_readiness.yea_rate == 2 / 3


def test_get_ontology_member_graph_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_ontology_member_graph("P000197", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "ontology_member_graph"
    assert result.identifier == "P000197"


def test_get_ontology_member_features_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_ontology_member_features("P000197", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_bioguide_id == "P000197"
    assert result.data.edge_count == 1
    assert result.data.readiness_status == "partial"


def test_get_ontology_member_features_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_ontology_member_features("P000197", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "ontology_member_features"
    assert result.identifier == "P000197"


@pytest.mark.parametrize(
    ("artifact_path", "getter", "resource_type"),
    [
        (
            ontology_member_edges_path("P000197"),
            lambda root: get_ontology_member_graph("P000197", snapshot_root=root),
            "ontology_member_graph",
        ),
        (
            ontology_member_features_path("P000197"),
            lambda root: get_ontology_member_features("P000197", snapshot_root=root),
            "ontology_member_features",
        ),
    ],
)
def test_ontology_member_endpoints_return_not_found_for_malformed_artifact(
    tmp_path: Path,
    artifact_path: str,
    getter,
    resource_type: str,
) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])
    (tmp_path / artifact_path).write_text("{not valid json", encoding="utf-8")

    result = getter(tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == resource_type
    assert result.identifier == "P000197"


def test_get_prediction_readiness_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, prediction_readiness=_prediction_readiness())

    result = get_prediction_readiness(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshot_id == "2026-01-01"
    assert result.data.member_count == 0


def test_get_prediction_bootstrap_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, prediction_readiness=_prediction_readiness_with_member())

    result = get_prediction_bootstrap(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_count == 1
    assert result.data.index_path == "prediction/index.json"
    assert result.data.topology_path == "prediction/topology.json"


def test_get_prediction_topology_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_topology(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_count == 1
    assert result.data.source_count == 2
    assert result.data.sector_count == 1
    assert result.data.committee_count == 1
    assert result.data.source_context_path_template == "prediction/source-context/{source_key}.json"


def test_get_prediction_readiness_index_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, prediction_readiness=_prediction_readiness_with_member())

    result = get_prediction_readiness_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug_to_bioguide == {"nancy-pelosi": "P000197"}
    assert result.data.members_by_bioguide["P000197"].yea_rate == 2 / 3


def test_get_prediction_sector_readiness_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_sector_readiness(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.sector_count == 1
    assert result.data.sectors[0].member_count == 1


def test_get_prediction_sector_context_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_sector_context("energy", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.sector.sector_id == "energy"
    assert result.data.members[0].member_bioguide_id == "P000197"


def test_get_prediction_source_index_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_source_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.source_count == 2
    assert result.data.sources[0].member_bioguide_ids == ["P000197"]
    assert result.data.sources[0].committee_ids == ["HSEC"]
    assert result.data.sources[0].sector_ids == ["energy"]


def test_get_prediction_source_context_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )
    source_key = get_prediction_source_index(snapshot_root=tmp_path).data.sources[0].source_key

    result = get_prediction_source_context(source_key, snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.source.source_key == source_key
    assert result.data.members[0].member_bioguide_id == "P000197"


def test_get_prediction_committee_readiness_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_committee_readiness(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.committee_count == 1
    assert result.data.committees[0].committee_id == "HSEC"
    assert result.data.committees[0].sector_ids == ["energy"]


def test_get_prediction_committee_context_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_committee_context("HSEC", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.committee.committee_id == "HSEC"
    assert result.data.members[0].member_bioguide_id == "P000197"


def test_get_prediction_member_readiness_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path, prediction_readiness=_prediction_readiness_with_member())

    result = get_prediction_member_readiness("P000197", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.bioguide_id == "P000197"
    assert result.data.yea_rate == 2 / 3


def test_get_prediction_member_context_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )

    result = get_prediction_member_context("P000197", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_bioguide_id == "P000197"
    assert result.data.member_readiness.yea_rate == 2 / 3
    assert result.data.ontology_features is not None
    assert result.data.context_status in {"ready", "partial"}


@pytest.mark.parametrize(
    ("artifact_path", "getter", "resource_type"),
    [
        (prediction_readiness_path(), get_prediction_readiness, "prediction_readiness"),
        (prediction_bootstrap_path(), get_prediction_bootstrap, "prediction_bootstrap"),
        (prediction_topology_path(), get_prediction_topology, "prediction_topology"),
        (
            prediction_readiness_index_path(),
            get_prediction_readiness_index,
            "prediction_readiness_index",
        ),
        (
            prediction_sector_readiness_path(),
            get_prediction_sector_readiness,
            "prediction_sector_readiness",
        ),
        (prediction_source_index_path(), get_prediction_source_index, "prediction_source_index"),
        (
            prediction_committee_readiness_path(),
            get_prediction_committee_readiness,
            "prediction_committee_readiness",
        ),
    ],
)
def test_prediction_collection_endpoints_return_not_found_for_malformed_artifact(
    tmp_path: Path,
    artifact_path: str,
    getter,
    resource_type: str,
) -> None:
    make_snapshot(
        tmp_path,
        ontology_edges=[_ontology_edge(), _committee_sector_edge()],
        prediction_readiness=_prediction_readiness_with_member(),
    )
    (tmp_path / artifact_path).write_text("{not valid json", encoding="utf-8")

    result = getter(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == resource_type
    assert result.identifier == "current"


def test_get_prediction_readiness_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_readiness(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_readiness"
    assert result.identifier == "current"


def test_get_prediction_bootstrap_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_bootstrap(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_bootstrap"
    assert result.identifier == "current"


def test_get_prediction_topology_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_topology(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_topology"
    assert result.identifier == "current"


def test_get_prediction_readiness_index_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_readiness_index(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_readiness_index"
    assert result.identifier == "current"


def test_get_prediction_sector_readiness_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_sector_readiness(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_sector_readiness"
    assert result.identifier == "current"


def test_get_prediction_sector_context_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_sector_context("energy", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_sector_context"
    assert result.identifier == "energy"


def test_get_prediction_source_index_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_source_index(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_source_index"
    assert result.identifier == "current"


def test_get_prediction_source_context_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_source_context("abc123", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_source_context"
    assert result.identifier == "abc123"


def test_get_prediction_committee_readiness_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_committee_readiness(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_committee_readiness"
    assert result.identifier == "current"


def test_get_prediction_committee_context_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_committee_context("HSEC", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_committee_context"
    assert result.identifier == "HSEC"


def test_get_prediction_member_readiness_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_member_readiness("P000197", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_member_readiness"
    assert result.identifier == "P000197"


def test_get_prediction_member_context_returns_not_found_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_prediction_member_context("P000197", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "prediction_member_context"
    assert result.identifier == "P000197"


def test_get_ontology_member_graph_returns_not_found_for_unsafe_identifier(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, ontology_edges=[_ontology_edge()])

    result = get_ontology_member_graph("../P000197", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "ontology_member_graph"
    assert result.identifier == "../P000197"


def test_search_current_member_lookup_returns_filtered_results(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = search_current_member_lookup("nancy", snapshot_root=tmp_path)

    assert result.ok is True
    assert [member.slug for member in result.data.members] == ["nancy-pelosi"]


def test_search_current_member_lookup_keeps_ok_true_with_no_matches(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = search_current_member_lookup("ghost", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.members == []


def test_get_last_updated_returns_snapshot_metadata(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_last_updated(snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshot_date == snapshot_date
    assert result.data.published_at == published_at
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_last_updated_returns_not_found_without_snapshot(tmp_path: Path) -> None:
    result = get_last_updated(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot"
    assert result.identifier == "latest"


def test_src_api_exports_read_service_helpers() -> None:
    assert api.get_homepage is get_homepage
    assert api.get_zip is get_zip
    assert api.get_member is get_member
    assert api.get_evidence is get_evidence
    assert api.get_ontology_graph is get_ontology_graph
    assert api.get_ontology_member_graph is get_ontology_member_graph
    assert api.get_current_member_lookup is get_current_member_lookup
    assert api.search_current_member_lookup is search_current_member_lookup
    assert api.get_last_updated is get_last_updated
