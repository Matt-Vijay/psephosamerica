"""Tests for the prediction read HTTP endpoint.

Wires the rate-limited read service to the repo's HTTP layer so the public read
API serves cited predictions over HTTP: 200 with the ServedPrediction, 404 for
an unknown pair, 429 with a Retry-After header when the client is rate limited.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from src.api.prediction_http import serve_prediction
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

_SHA = "c" * 64


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
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                contribution=0.5,
            )
        ],
        llm_explanation="Votes with party on this topic.",
        counterfactual="A whipped leadership position against would flip it.",
    )


def _service(*, capacity: int = 10, refill: float = 1.0) -> PredictionReadService:
    snapshot = build_prediction_snapshot(
        [_served("person:a", "bill:1")],
        generated_at=datetime(2025, 2, 2, tzinfo=timezone.utc),
    )
    return PredictionReadService(
        snapshot=snapshot,
        rate_limiter=TokenBucketRateLimiter(capacity=capacity, refill_per_second=refill),
    )


def test_serve_prediction_returns_200_with_cited_payload() -> None:
    response = serve_prediction(
        _service(),
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    body = json.loads(response.body)
    assert body["canonical_person_id"] == "person:a"
    assert body["probability_yea"] == 0.8
    assert body["uncertainty"]["conformal_label_set"] == ["yea"]
    assert body["evidence_anchors"][0]["source_url"] == "https://clerk.house.gov/Votes/1"
    assert body["counterfactual"]


def test_serve_prediction_returns_404_for_unknown_pair() -> None:
    response = serve_prediction(
        _service(),
        client_id="c1",
        canonical_person_id="person:missing",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert response.status_code == 404
    assert json.loads(response.body)["error"] == "not_found"


def test_serve_prediction_returns_429_with_retry_after_when_rate_limited() -> None:
    service = _service(capacity=1, refill=0.5)
    first = serve_prediction(
        service,
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert first.status_code == 200

    blocked = serve_prediction(
        service,
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert blocked.status_code == 429
    # Refill 0.5/sec -> a full token in 2 seconds.
    assert blocked.headers["Retry-After"] == "2"
    assert json.loads(blocked.body)["error"] == "rate_limited"


def test_serve_prediction_rate_limit_is_per_client() -> None:
    service = _service(capacity=1, refill=1.0)
    serve_prediction(
        service, client_id="c1", canonical_person_id="person:a", canonical_bill_id="bill:1", now=0.0
    )
    other = serve_prediction(
        service, client_id="c2", canonical_person_id="person:a", canonical_bill_id="bill:1", now=0.0
    )
    assert other.status_code == 200
