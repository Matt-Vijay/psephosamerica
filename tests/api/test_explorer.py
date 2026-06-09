"""Tests for the HTML explorer/dashboard renderers and their routes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import quote

from src.api.explorer import render_dashboard_html, render_explorer_html
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

_SHA = "a" * 64


def _served(person: str, bill: str, *, explanation: str = "Votes with party.") -> ServedPrediction:
    return ServedPrediction(
        canonical_person_id=person,
        canonical_bill_id=bill,
        model_name="per_member_signal_model",
        known_at=date(2025, 2, 1),
        probability_yea=0.83,
        uncertainty=PredictionUncertainty(
            confidence_level=0.9,
            interval_lower=0.7,
            interval_upper=0.9,
            conformal_label_set=["yea"],
        ),
        evidence_anchors=[
            PredictionEvidenceAnchor(
                label="Prior energy vote",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256=_SHA,
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                contribution=0.5,
            )
        ],
        llm_explanation=explanation,
        counterfactual="A leadership whip against would flip it.",
    )


def test_render_explorer_includes_probability_evidence_and_counterfactual() -> None:
    html = render_explorer_html([_served("person:a", "bill:1")])
    assert "83% yea" in html
    assert "https://clerk.house.gov/Votes/1" in html
    assert _SHA[:12] in html  # cited sha256 prefix
    assert "What would change this" in html
    assert "person:a" in html and "bill:1" in html


def test_render_explorer_escapes_injected_markup() -> None:
    html = render_explorer_html([_served("person:a", "bill:1", explanation="<script>x</script>")])
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_dashboard_has_tiles() -> None:
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.14, log_loss=0.4, sample_count=4000
            ),
            BenchmarkSliceMetrics(
                slice_name="cross_pressured", brier_score=0.24, log_loss=0.55, sample_count=371
            ),
            BenchmarkSliceMetrics(
                slice_name="party:D", brier_score=0.12, log_loss=0.36, sample_count=2200
            ),
        ],
    )
    html = render_dashboard_html(dashboard)
    assert "per_member_signal_model" in html
    assert "cross-pressured" in html
    assert "0.240" in html  # cross-pressured Brier tile
    assert "party" in html


def _app() -> PredictionWsgiApp:
    snapshot = build_prediction_snapshot(
        [_served("person:a", "bill:1")],
        generated_at=datetime(2025, 2, 2, tzinfo=timezone.utc),
    )
    service = PredictionReadService(
        snapshot=snapshot,
        rate_limiter=TokenBucketRateLimiter(capacity=10, refill_per_second=1.0),
    )
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.14, log_loss=0.4, sample_count=1
            )
        ],
    )
    return PredictionWsgiApp(read_service=service, dashboard=dashboard, clock=lambda: 0.0)


def _call(app: PredictionWsgiApp, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "REMOTE_ADDR": "1.2.3.4",
    }
    body = b"".join(app(environ, start_response))  # type: ignore[arg-type]
    return str(captured["status"]), captured["headers"], body  # type: ignore[arg-type]


def test_explorer_route_serves_html() -> None:
    status, headers, body = _call(_app(), "/v1/explorer")
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("text/html")
    assert b"Prediction Explorer" in body
    assert b"person:a" in body


def test_dashboard_html_route_serves_html() -> None:
    status, headers, _body = _call(_app(), "/v1/dashboard.html")
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("text/html")


def test_path_style_prediction_route_serves_json() -> None:
    path = f"/v1/prediction/{quote('person:a', safe='')}/{quote('bill:1', safe='')}"
    status, headers, body = _call(_app(), path)
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("application/json")
    assert b"person:a" in body


def test_path_style_prediction_unknown_is_404() -> None:
    path = f"/v1/prediction/{quote('person:x', safe='')}/{quote('bill:1', safe='')}"
    status, _headers, _body = _call(_app(), path)
    assert status.startswith("404")
