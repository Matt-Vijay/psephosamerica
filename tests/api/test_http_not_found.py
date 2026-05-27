"""Not-found behavior for snapshot/target-root read-API endpoints.

Calling each endpoint against an empty root exercises the NotFoundBody branch
that the success-path tests in test_http.py do not cover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.api.http as api_http

_SNAPSHOT_ROOT_ONLY = [
    "serve_homepage",
    "serve_homepage_bootstrap",
    "serve_history_bootstrap",
    "serve_ontology_graph",
    "serve_ontology_index",
]

_TARGET_ROOT = [
    "serve_history_backfill_report",
    "serve_history_backfill_bootstrap",
]


@pytest.mark.parametrize("fn_name", _SNAPSHOT_ROOT_ONLY)
def test_snapshot_root_endpoint_reports_not_found(fn_name: str, tmp_path: Path) -> None:
    response = getattr(api_http, fn_name)(snapshot_root=tmp_path)
    assert response.status_code in (404, 503)
    assert "json" in response.headers["Content-Type"]


@pytest.mark.parametrize("fn_name", _TARGET_ROOT)
def test_target_root_endpoint_reports_not_found(fn_name: str, tmp_path: Path) -> None:
    response = getattr(api_http, fn_name)(target_root=tmp_path)
    assert response.status_code in (404, 503)
    assert "json" in response.headers["Content-Type"]
