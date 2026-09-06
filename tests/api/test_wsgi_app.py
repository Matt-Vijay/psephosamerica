"""Tests for the framework-neutral WSGI serving app.

Exercises the running read API by invoking the WSGI callable directly (no
sockets): GET /v1/prediction serves a rate-limited cited prediction, GET
/v1/dashboard serves the calibration dashboard, /healthz is a liveness probe,
unknown paths 404, and non-GET 405. This is the glue that makes the read API
actually serve and the dashboards visible under any WSGI/ASGI server.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from urllib.parse import urlencode

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

_SHA = "f" * 64


def _served(person: str, bill: str) -> ServedPrediction:
    return ServedPrediction(
        canonical_person_id=person,
        canonical_bill_id=bill,
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
                content_sha256=_SHA,
                retrieved_at=datetime(2025, 1, 1, tzinfo=UTC),
                contribution=0.5,
            )
        ],
        llm_explanation="Votes with party.",
        counterfactual="A whip against would flip it.",
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _app(
    *, capacity: int = 10, refill: float = 1.0, clock: _Clock | None = None
) -> PredictionWsgiApp:
    snapshot = build_prediction_snapshot(
        [_served("person:a", "bill:1")],
        generated_at=datetime(2025, 2, 2, tzinfo=UTC),
    )
    service = PredictionReadService(
        snapshot=snapshot,
        rate_limiter=TokenBucketRateLimiter(capacity=capacity, refill_per_second=refill),
    )
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.14, log_loss=0.4, sample_count=1
            )
        ],
    )
    return PredictionWsgiApp(read_service=service, dashboard=dashboard, clock=clock or _Clock())


def _call(
    app: PredictionWsgiApp, path: str, *, method: str = "GET", query: str = ""
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REMOTE_ADDR": "1.2.3.4",
    }
    body_chunks: Iterable[bytes] = app(environ, start_response)  # type: ignore[arg-type]
    body = b"".join(body_chunks)
    return str(captured["status"]), captured["headers"], body  # type: ignore[arg-type]


def test_serves_cited_prediction() -> None:
    status, headers, body = _call(
        _app(), "/v1/prediction", query=urlencode({"person_id": "person:a", "bill_id": "bill:1"})
    )
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["canonical_person_id"] == "person:a"
    assert payload["evidence_anchors"][0]["content_sha256"] == _SHA


def test_unknown_pair_is_404() -> None:
    status, _headers, body = _call(
        _app(), "/v1/prediction", query=urlencode({"person_id": "person:x", "bill_id": "bill:1"})
    )
    assert status.startswith("404")
    assert json.loads(body)["error"] == "not_found"


def test_missing_query_params_is_400() -> None:
    status, _headers, _body = _call(_app(), "/v1/prediction")
    assert status.startswith("400")


def test_serves_dashboard() -> None:
    status, _headers, body = _call(_app(), "/v1/dashboard")
    assert status.startswith("200")
    assert json.loads(body)["model_name"] == "per_member_signal_model"


def test_healthz_is_ok() -> None:
    status, _headers, _body = _call(_app(), "/healthz")
    assert status.startswith("200")


def test_unknown_path_is_404() -> None:
    status, _headers, _body = _call(_app(), "/v1/nope")
    assert status.startswith("404")


def test_non_get_is_405() -> None:
    status, _headers, _body = _call(_app(), "/v1/dashboard", method="POST")
    assert status.startswith("405")


def test_rate_limit_returns_429_with_retry_after() -> None:
    clock = _Clock()
    app = _app(capacity=1, refill=0.5, clock=clock)
    query = urlencode({"person_id": "person:a", "bill_id": "bill:1"})
    first, _h, _b = _call(app, "/v1/prediction", query=query)
    assert first.startswith("200")
    blocked_status, blocked_headers, _b = _call(app, "/v1/prediction", query=query)
    assert blocked_status.startswith("429")
    assert blocked_headers["Retry-After"] == "2"
