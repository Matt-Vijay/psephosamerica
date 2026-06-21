"""Tests that the WSGI app routes the graph / explorer endpoints correctly."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timezone

from src.api.graph_http import GraphService
from src.api.wsgi_app import PredictionWsgiApp
from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.calibration_dashboard import build_calibration_dashboard
from src.prediction.read_api import (
    PredictionReadService,
    TokenBucketRateLimiter,
    build_prediction_snapshot,
)
from src.prediction.served_prediction import (
    PredictionEvidenceAnchor,
    PredictionUncertainty,
    ServedPrediction,
)
from src.query.graph_store import Edge, GraphStore, Node, Provenance


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _graph_store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-a",
            "person",
            "Alice Adams",
            ("legistar:oakland:1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p"),),
            "oakland",
        )
    )
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Rent Ordinance",
            ("legistar:oakland:9",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/b"),),
            "oakland",
        )
    )
    store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov("https://x/v")))
    return store


def _served() -> ServedPrediction:
    return ServedPrediction(
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        model_name="per_member_signal_model",
        known_at=date(2025, 2, 1),
        probability_yea=0.8,
        uncertainty=PredictionUncertainty(
            confidence_level=0.9,
            interval_lower=0.7,
            interval_upper=0.9,
            conformal_label_set=["yea"],
        ),
        evidence_anchors=[
            PredictionEvidenceAnchor(
                label="Prior vote",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256="f" * 64,
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                contribution=0.5,
            )
        ],
        llm_explanation="x",
        counterfactual="y",
    )


def _app() -> PredictionWsgiApp:
    snapshot = build_prediction_snapshot(
        [_served()], generated_at=datetime(2025, 2, 2, tzinfo=timezone.utc)
    )
    service = PredictionReadService(
        snapshot=snapshot,
        rate_limiter=TokenBucketRateLimiter(capacity=100, refill_per_second=1.0),
    )
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.14, log_loss=0.4, sample_count=1
            )
        ],
    )
    return PredictionWsgiApp(
        read_service=service,
        dashboard=dashboard,
        graph_service=GraphService(store_factory=_graph_store),
    )


def _call(app: PredictionWsgiApp, path: str, query: str = "") -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query}
    chunks: Iterable[bytes] = app(environ, start_response)  # type: ignore[arg-type]
    return str(captured["status"]), captured["headers"], b"".join(chunks)  # type: ignore[arg-type]


def test_graph_entities_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/entities", "type=person")
    assert status.startswith("200")
    assert json.loads(body)["results"][0]["display_name"] == "Alice Adams"


def test_graph_votes_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/votes", "person_id=ce-a")
    assert status.startswith("200")
    assert json.loads(body)["results"][0]["bill_name"] == "Rent Ordinance"


def test_graph_path_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/path", "from=ce-a")
    assert status.startswith("200")
    assert json.loads(body)["count"] >= 1


def test_graph_jurisdictions_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/jurisdictions")
    assert status.startswith("200")
    assert {"slug": "oakland", "officials": 1} in json.loads(body)["jurisdictions"]


def test_graph_ask_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/ask", "q=rent+control")
    assert status.startswith("200")
    assert json.loads(body)["used_llm"] is False


def test_explorer_official_route() -> None:
    status, headers, body = _call(_app(), "/v1/explorer/official/ce-a")
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("text/html")
    assert b"Alice Adams" in body


def test_explorer_jurisdiction_route() -> None:
    status, _, body = _call(_app(), "/v1/explorer/jurisdiction/oakland")
    assert status.startswith("200")
    assert b"Rent Ordinance" in body


def test_redundant_policy_areas_route() -> None:
    # base graph has no policy_area edges; endpoint still returns 200 with empty set
    status, _, body = _call(_app(), "/v1/graph/redundant_policy_areas", "min_bills=1")
    assert status.startswith("200")
    assert "policy_areas" in json.loads(body)


def test_reauthorizations_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/reauthorizations")
    assert status.startswith("200")
    assert "clusters" in json.loads(body)


def test_donor_paths_route_requires_term() -> None:
    status, _, _ = _call(_app(), "/v1/graph/donor_paths")
    assert status.startswith("400")


def test_unknown_graph_route_404() -> None:
    status, _, _ = _call(_app(), "/v1/graph/nope")
    assert status.startswith("404")


def test_existing_prediction_route_still_works() -> None:
    status, _, body = _call(_app(), "/v1/prediction", "person_id=person:a&bill_id=bill:1")
    assert status.startswith("200")
    assert json.loads(body)["canonical_person_id"] == "person:a"
