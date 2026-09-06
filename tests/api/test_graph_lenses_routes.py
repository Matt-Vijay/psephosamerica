"""Tests for the V8 #3 lens routes, landing page, search, and ask page."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from src.api.graph_http import GraphService
from src.api.wsgi_app import PredictionWsgiApp
from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.calibration_dashboard import build_calibration_dashboard
from src.prediction.read_api import (
    PredictionReadService,
    TokenBucketRateLimiter,
    build_prediction_snapshot,
)
from src.query.graph_rag import GraphRagAnswerer, stub_generate
from src.query.graph_store import Edge, GraphStore, Node, Provenance


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-a",
            "person",
            "Alice Adams",
            ("bioguide:A1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://p/a"),),
            "us-congress",
        )
    )
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Budget Act",
            ("congress:1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://b/1"),),
            "us-congress",
        )
    )
    store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov("https://v/1")))
    return store


def _app() -> PredictionWsgiApp:
    snapshot = build_prediction_snapshot([], generated_at=datetime(2026, 1, 1, tzinfo=UTC))
    service = PredictionReadService(
        snapshot=snapshot, rate_limiter=TokenBucketRateLimiter(capacity=100, refill_per_second=1.0)
    )
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.0, log_loss=0.0, sample_count=0
            )
        ],
    )
    store = _store()
    graph = GraphService(store_factory=lambda: store)
    # Pin a deterministic stub answerer so the ask path never touches the network.
    graph._answerer = GraphRagAnswerer(store=store, generate=stub_generate, used_llm=False)
    return PredictionWsgiApp(read_service=service, dashboard=dashboard, graph_service=graph)


def _call(app: PredictionWsgiApp, path: str, query: str = "") -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query}
    chunks: Iterable[bytes] = app(environ, start_response)  # type: ignore[arg-type]
    return str(captured["status"]), captured["headers"], b"".join(chunks)  # type: ignore[arg-type]


def test_home_page() -> None:
    status, headers, body = _call(_app(), "/")
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("text/html")
    assert b"Ask anything" in body
    assert b"explorer/search" in body
    assert b"us-congress" in body


def test_search_page() -> None:
    status, _, body = _call(_app(), "/v1/explorer/search", "q=Alice&type=person")
    assert status.startswith("200")
    assert b"Alice Adams" in body
    assert b"/v1/explorer/official/ce-a" in body


def test_ask_page_renders_cited_answer() -> None:
    status, _, body = _call(_app(), "/v1/explorer/ask", "q=budget")
    assert status.startswith("200")
    assert b"grounded stub" in body


def test_accountability_route() -> None:
    status, _, body = _call(_app(), "/v1/graph/accountability", "limit=5")
    import json

    data = json.loads(body)
    assert status.startswith("200")
    assert data["officials"][0]["display_name"] == "Alice Adams"
    assert data["officials"][0]["citation"]["source_url"] == "https://p/a"


def test_jurisdiction_accountability_route() -> None:
    import json

    status, _, body = _call(_app(), "/v1/graph/jurisdiction_accountability")
    assert status.startswith("200")
    assert any(r["jurisdiction"] == "us-congress" for r in json.loads(body)["jurisdictions"])


def test_said_vs_voted_route() -> None:
    import json

    status, _, body = _call(_app(), "/v1/graph/said_vs_voted", "person_id=ce-a")
    assert status.startswith("200")
    data = json.loads(body)
    assert len(data["votes"]) == 1
    assert "lights up" in data["note"]


def test_said_vs_voted_route_requires_person() -> None:
    status, _, _ = _call(_app(), "/v1/graph/said_vs_voted")
    assert status.startswith("400")


def test_copied_bills_route_empty_without_sidecar(monkeypatch) -> None:
    import json
    from pathlib import Path

    from src.query import lenses

    # Point at a non-existent sidecar so the route returns an empty, valid payload.
    monkeypatch.setattr(lenses, "BILL_CONTENT_PATHS", (Path("/no/such.jsonl"),))
    status, _, body = _call(_app(), "/v1/graph/copied_bills", "scan=100")
    assert status.startswith("200")
    assert json.loads(body)["count"] == 0
