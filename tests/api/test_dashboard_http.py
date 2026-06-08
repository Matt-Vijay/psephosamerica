"""Tests for the calibration dashboard read endpoint.

Mirrors the rest of the read API: a pure ``serve_*`` handler returning the
calibration dashboard payload (Brier + log-loss per jurisdiction/party/faction,
cross-pressured highlighted) for the frontend to render.
"""

from __future__ import annotations

import json

from src.api.prediction_http import serve_calibration_dashboard
from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.calibration_dashboard import build_calibration_dashboard


def _dashboard() -> object:
    slices = [
        BenchmarkSliceMetrics(
            slice_name="overall", brier_score=0.14, log_loss=0.40, sample_count=4000
        ),
        BenchmarkSliceMetrics(
            slice_name="cross_pressured", brier_score=0.24, log_loss=0.55, sample_count=371
        ),
        BenchmarkSliceMetrics(
            slice_name="party:D", brier_score=0.12, log_loss=0.36, sample_count=2200
        ),
        BenchmarkSliceMetrics(
            slice_name="jurisdiction:us_congress",
            brier_score=0.14,
            log_loss=0.40,
            sample_count=4000,
        ),
    ]
    return build_calibration_dashboard("per_member_signal_model", slices)


def test_serve_dashboard_returns_200_with_slices() -> None:
    response = serve_calibration_dashboard(_dashboard())  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    body = json.loads(response.body)
    assert body["model_name"] == "per_member_signal_model"
    assert body["overall"]["brier_score"] == 0.14
    assert body["cross_pressured"]["log_loss"] == 0.55
    assert {entry["key"] for entry in body["sections"]["party"]} == {"D"}


def test_serve_dashboard_is_cacheable() -> None:
    response = serve_calibration_dashboard(_dashboard())  # type: ignore[arg-type]
    assert "max-age" in response.headers["Cache-Control"]
