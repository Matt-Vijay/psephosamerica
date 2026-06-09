"""Framework-neutral WSGI app for the public read API.

OVERALL_GOAL.md wants the read API to actually *serve* rate-limited cited
predictions and the dashboards to be *visible*. The repo's endpoints are pure
``serve_*`` response handlers; this is the thin running layer that routes HTTP
requests to them. It is a standard WSGI application with **no new dependency**
-- it runs unchanged under gunicorn, uvicorn (via the ASGI-WSGI bridge), or a
Ray Serve WSGI deployment -- so it makes the API live without committing to a
particular framework.

Routes:
* ``GET /healthz`` -- liveness probe.
* ``GET /v1/prediction?person_id&bill_id`` -- a rate-limited cited prediction
  (per-client by ``REMOTE_ADDR``). 200 / 404 / 429.
* ``GET /v1/dashboard`` -- the calibration dashboard payload.

The wall clock used by the rate limiter is injected (default
``time.monotonic``) so the app is deterministic under test.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from src.api.explorer import render_dashboard_html, render_explorer_html
from src.api.http import JsonHttpResponse
from src.api.prediction_http import (
    PredictionApiError,
    serve_calibration_dashboard,
    serve_prediction,
)
from src.prediction.calibration_dashboard import CalibrationDashboard
from src.prediction.read_api import PredictionReadService

StartResponse = Callable[[str, list[tuple[str, str]]], object]
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_HTML_CONTENT_TYPE = "text/html; charset=utf-8"


def _html_response(html: str) -> JsonHttpResponse:
    return JsonHttpResponse(
        status_code=200,
        headers={"Content-Type": _HTML_CONTENT_TYPE, "Cache-Control": "public, max-age=300"},
        body=html.encode("utf-8"),
    )


def _error_response(status_code: int, error: str, detail: str) -> JsonHttpResponse:
    body = json.dumps({"error": error, "detail": detail}).encode("utf-8")
    return JsonHttpResponse(
        status_code=status_code,
        headers={"Content-Type": _JSON_CONTENT_TYPE, "Cache-Control": "no-store"},
        body=body,
    )


class PredictionWsgiApp:
    """WSGI application exposing the prediction read API and dashboard."""

    def __init__(
        self,
        *,
        read_service: PredictionReadService,
        dashboard: CalibrationDashboard,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read_service = read_service
        self._dashboard = dashboard
        self._clock = clock

    def __call__(
        self,
        environ: dict[str, object],
        start_response: StartResponse,
    ) -> list[bytes]:
        response = self._route(environ)
        phrase = HTTPStatus(response.status_code).phrase
        start_response(f"{response.status_code} {phrase}", list(response.headers.items()))
        return [response.body]

    def _route(self, environ: dict[str, object]) -> JsonHttpResponse:
        method = str(environ.get("REQUEST_METHOD", "GET"))
        path = str(environ.get("PATH_INFO", ""))

        if method != "GET":
            return _error_response(405, "method_not_allowed", "use GET")

        if path == "/healthz":
            return JsonHttpResponse(
                status_code=200,
                headers={"Content-Type": _JSON_CONTENT_TYPE, "Cache-Control": "no-store"},
                body=b'{"status":"ok"}',
            )
        if path == "/v1/dashboard":
            return serve_calibration_dashboard(self._dashboard)
        if path == "/v1/dashboard.html":
            return _html_response(render_dashboard_html(self._dashboard))
        if path == "/v1/explorer":
            return _html_response(render_explorer_html(self._read_service.all_predictions()))
        if path == "/v1/prediction":
            return self._serve_prediction_query(environ)
        if path.startswith("/v1/prediction/"):
            return self._serve_prediction_path(path, environ)

        return _error_response(404, "not_found", f"no route for {path}")

    def _serve_prediction_path(self, path: str, environ: dict[str, object]) -> JsonHttpResponse:
        # /v1/prediction/<person>/<bill> with url-encoded segments (ids contain ':' and '/').
        remainder = path[len("/v1/prediction/") :]
        segments = remainder.split("/")
        if len(segments) != 2 or not segments[0] or not segments[1]:
            return _error_response(
                400, "bad_request", "path must be /v1/prediction/<person_id>/<bill_id>"
            )
        person = unquote(segments[0])
        bill = unquote(segments[1])
        return self._dispatch_prediction(person, bill, environ)

    def _serve_prediction_query(self, environ: dict[str, object]) -> JsonHttpResponse:
        query = parse_qs(str(environ.get("QUERY_STRING", "")))
        person = query.get("person_id", [""])[0]
        bill = query.get("bill_id", [""])[0]
        if not person or not bill:
            return _error_response(
                400, "bad_request", "person_id and bill_id query parameters are required"
            )
        return self._dispatch_prediction(person, bill, environ)

    def _dispatch_prediction(
        self, person: str, bill: str, environ: dict[str, object]
    ) -> JsonHttpResponse:
        client_id = str(environ.get("REMOTE_ADDR", "anonymous"))
        return serve_prediction(
            self._read_service,
            client_id=client_id,
            canonical_person_id=person,
            canonical_bill_id=bill,
            now=self._clock(),
        )


# PredictionApiError is re-exported so callers can construct error bodies consistently.
__all__ = ["PredictionWsgiApp", "PredictionApiError"]
