"""HTTP endpoint for the public prediction read API.

OVERALL_GOAL.md: the read API serves rate-limited, cited predictions. This
wires :class:`PredictionReadService` to the repo's hand-rolled HTTP layer,
returning a :class:`JsonHttpResponse`:

* ``200`` with the full ``ServedPrediction`` (calibrated probability,
  uncertainty interval, top-5 cited evidence anchors, explanation,
  counterfactual) -- which the contract guarantees is cited.
* ``404`` for a (person, bill) pair not present in the snapshot.
* ``429`` with a ``Retry-After`` header when the client exceeds its rate.

It builds the response directly rather than routing through the large success
union in ``http.py``, keeping the prediction surface self-contained.
"""

from __future__ import annotations

import json
import math

from pydantic import BaseModel

from src.api.http import JsonHttpResponse
from src.prediction.calibration_dashboard import CalibrationDashboard
from src.prediction.read_api import PredictionReadService

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class PredictionApiError(BaseModel):
    """Error body for non-200 prediction responses."""

    error: str
    detail: str


def _json_body(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )


def serve_prediction(
    service: PredictionReadService,
    *,
    client_id: str,
    canonical_person_id: str,
    canonical_bill_id: str,
    now: float,
) -> JsonHttpResponse:
    """Serve one rate-limited, cited prediction for a (person, bill) pair."""
    result = service.get_prediction(
        client_id=client_id,
        canonical_person_id=canonical_person_id,
        canonical_bill_id=canonical_bill_id,
        now=now,
    )
    if result.status == "rate_limited":
        retry_after = math.ceil(result.retry_after_seconds or 0.0)
        return JsonHttpResponse(
            status_code=429,
            headers={
                "Content-Type": _JSON_CONTENT_TYPE,
                "Cache-Control": "no-store",
                "Retry-After": str(retry_after),
            },
            body=_json_body(
                PredictionApiError(error="rate_limited", detail="request rate exceeded")
            ),
        )
    if result.status == "not_found" or result.prediction is None:
        return JsonHttpResponse(
            status_code=404,
            headers={"Content-Type": _JSON_CONTENT_TYPE, "Cache-Control": "no-store"},
            body=_json_body(
                PredictionApiError(error="not_found", detail="no prediction for this pair")
            ),
        )
    return JsonHttpResponse(
        status_code=200,
        headers={"Content-Type": _JSON_CONTENT_TYPE, "Cache-Control": "public, max-age=300"},
        body=_json_body(result.prediction),
    )


def serve_calibration_dashboard(dashboard: CalibrationDashboard) -> JsonHttpResponse:
    """Serve the per-jurisdiction/party/faction calibration dashboard payload.

    Mirrors the rest of the read API: a pure response handler returning the
    dashboard data the frontend renders (Brier + log-loss per slice, with the
    cross-pressured slice highlighted).
    """
    return JsonHttpResponse(
        status_code=200,
        headers={"Content-Type": _JSON_CONTENT_TYPE, "Cache-Control": "public, max-age=300"},
        body=_json_body(dashboard),
    )
