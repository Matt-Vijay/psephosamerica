"""Not-found behavior for the prediction read-API endpoints.

Calling each endpoint against an empty snapshot root exercises the
NotFoundBody branch (a missing snapshot/artifact), which the success-path
tests in test_http.py do not cover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.api.http as api_http

_SNAPSHOT_ONLY = [
    "serve_prediction_readiness",
    "serve_prediction_bootstrap",
    "serve_prediction_topology",
    "serve_prediction_readiness_index",
    "serve_prediction_sector_readiness",
    "serve_prediction_source_index",
    "serve_prediction_committee_readiness",
]

_ID_ARG = [
    "serve_prediction_source_context",
    "serve_prediction_sector_context",
    "serve_prediction_committee_context",
    "serve_prediction_member_readiness",
]


@pytest.mark.parametrize("fn_name", _SNAPSHOT_ONLY)
def test_snapshot_only_prediction_endpoint_reports_not_found(fn_name: str, tmp_path: Path) -> None:
    response = getattr(api_http, fn_name)(snapshot_root=tmp_path)
    # 404 for a missing artifact, 503 for a missing "latest" snapshot.
    assert response.status_code in (404, 503)
    assert "json" in response.headers["Content-Type"]


@pytest.mark.parametrize("fn_name", _ID_ARG)
def test_id_prediction_endpoint_reports_not_found(fn_name: str, tmp_path: Path) -> None:
    response = getattr(api_http, fn_name)("does-not-exist", snapshot_root=tmp_path)
    assert response.status_code in (404, 503)
    assert "json" in response.headers["Content-Type"]
