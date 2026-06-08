"""Tests for the prediction read service.

OVERALL_GOAL.md definition-of-done: "read API serves rate-limited cited
predictions". This is the serving core -- a content-addressed snapshot of
served predictions, a deterministic token-bucket rate limiter, and a read
service that returns a typed envelope (ok / not_found / rate_limited). Every
served object is a ``ServedPrediction``, which the contract guarantees carries
at least one cited evidence anchor, so the service can only ever serve cited
predictions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.prediction.read_api import (
    PredictionReadService,
    PredictionSnapshot,
    TokenBucketRateLimiter,
    build_prediction_snapshot,
)
from src.prediction.served_prediction import (
    PredictionEvidenceAnchor,
    PredictionUncertainty,
    ServedPrediction,
)

_SHA = "b" * 64


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


def _snapshot() -> PredictionSnapshot:
    return build_prediction_snapshot(
        [_served("person:a", "bill:1"), _served("person:b", "bill:2")],
        generated_at=datetime(2025, 2, 2, tzinfo=timezone.utc),
    )


def test_build_prediction_snapshot_is_content_addressed_and_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()
    assert first.meta.prediction_count == 2
    assert len(first.meta.content_sha256) == 64
    assert first.meta.content_sha256 == second.meta.content_sha256


def test_snapshot_rejects_duplicate_person_bill_keys() -> None:
    with pytest.raises(ValidationError):
        build_prediction_snapshot(
            [_served("person:a", "bill:1"), _served("person:a", "bill:1")],
            generated_at=datetime(2025, 2, 2, tzinfo=timezone.utc),
        )


def test_read_service_serves_known_prediction() -> None:
    service = PredictionReadService(
        snapshot=_snapshot(),
        rate_limiter=TokenBucketRateLimiter(capacity=10, refill_per_second=1.0),
    )
    result = service.get_prediction(
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=100.0,
    )
    assert result.status == "ok"
    assert result.prediction is not None
    assert result.prediction.canonical_person_id == "person:a"
    assert result.retry_after_seconds is None


def test_read_service_reports_not_found_for_unknown_pair() -> None:
    service = PredictionReadService(
        snapshot=_snapshot(),
        rate_limiter=TokenBucketRateLimiter(capacity=10, refill_per_second=1.0),
    )
    result = service.get_prediction(
        client_id="c1",
        canonical_person_id="person:missing",
        canonical_bill_id="bill:1",
        now=100.0,
    )
    assert result.status == "not_found"
    assert result.prediction is None


def test_rate_limiter_allows_up_to_capacity_then_blocks() -> None:
    limiter = TokenBucketRateLimiter(capacity=3, refill_per_second=1.0)
    assert [limiter.allow("c", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("c", now=0.0) is False
    # A second client has its own independent bucket.
    assert limiter.allow("other", now=0.0) is True


def test_rate_limiter_refills_over_time() -> None:
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1.0)
    assert limiter.allow("c", now=0.0) is True
    assert limiter.allow("c", now=0.0) is True
    assert limiter.allow("c", now=0.0) is False
    # One token refills after one second.
    assert limiter.allow("c", now=1.0) is True
    assert limiter.allow("c", now=1.0) is False


def test_read_service_rate_limits_with_retry_after() -> None:
    service = PredictionReadService(
        snapshot=_snapshot(),
        rate_limiter=TokenBucketRateLimiter(capacity=1, refill_per_second=0.5),
    )

    first = service.get_prediction(
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert first.status == "ok"

    blocked = service.get_prediction(
        client_id="c1",
        canonical_person_id="person:a",
        canonical_bill_id="bill:1",
        now=0.0,
    )
    assert blocked.status == "rate_limited"
    assert blocked.prediction is None
    # Refill is 0.5 tokens/sec, so a full token returns after 2 seconds.
    assert blocked.retry_after_seconds == pytest.approx(2.0)


def test_read_service_rate_limit_is_independent_per_client() -> None:
    service = PredictionReadService(
        snapshot=_snapshot(),
        rate_limiter=TokenBucketRateLimiter(capacity=1, refill_per_second=1.0),
    )
    service.get_prediction(
        client_id="c1", canonical_person_id="person:a", canonical_bill_id="bill:1", now=0.0
    )
    other = service.get_prediction(
        client_id="c2", canonical_person_id="person:a", canonical_bill_id="bill:1", now=0.0
    )
    assert other.status == "ok"
