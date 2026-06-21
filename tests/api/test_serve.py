"""Smoke test for the one-command explorer launcher."""

from __future__ import annotations

from collections.abc import Iterable

from src.api.serve import build_app


def _call(app, path: str) -> tuple[str, bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}
    chunks: Iterable[bytes] = app(environ, start_response)  # type: ignore[arg-type]
    return str(captured["status"]), b"".join(chunks)


def test_build_app_serves_healthz() -> None:
    status, body = _call(build_app(), "/healthz")
    assert status.startswith("200")
    assert b"ok" in body


def test_build_app_prediction_routes_dont_error() -> None:
    # Empty snapshot -> 400 (missing params) rather than a crash.
    status, _ = _call(build_app(), "/v1/prediction")
    assert status.startswith("400")
