"""Prediction read service: snapshot store + rate limiting + cited serving.

OVERALL_GOAL.md definition-of-done: "read API serves rate-limited cited
predictions". This module is the serving core, independent of HTTP transport
(the repo's hand-rolled ``src/api/http.py`` layer wraps it):

* ``PredictionSnapshot`` -- an immutable, content-addressed batch of
  ``ServedPrediction`` objects keyed by (canonical_person_id,
  canonical_bill_id), matching the "FastAPI + Postgres serving precomputed
  snapshots" design. Because every entry is a ``ServedPrediction`` -- which the
  contract forbids from existing without at least one cited evidence anchor --
  the service structurally cannot serve an uncited prediction.
* ``TokenBucketRateLimiter`` -- a deterministic per-client token bucket. Time
  is passed in explicitly (never read from the clock) so behavior is fully
  reproducible and testable, consistent with this repo's snapshot discipline.
* ``PredictionReadService`` -- returns a typed envelope: ``ok`` with the
  prediction, ``not_found``, or ``rate_limited`` with a ``retry_after``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from src.prediction.served_prediction import ServedPrediction

PredictionReadStatus = Literal["ok", "not_found", "rate_limited"]


def _prediction_key(canonical_person_id: str, canonical_bill_id: str) -> str:
    return f"{canonical_person_id}\x1f{canonical_bill_id}"


class PredictionSnapshotMeta(BaseModel, frozen=True):
    """Provenance for one served-prediction snapshot."""

    generated_at: datetime
    content_sha256: str
    prediction_count: int = Field(ge=0)


class PredictionSnapshot(BaseModel):
    """An immutable, content-addressed batch of served predictions."""

    meta: PredictionSnapshotMeta
    predictions: list[ServedPrediction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_are_unique_and_counted(self) -> Self:
        keys = [
            _prediction_key(item.canonical_person_id, item.canonical_bill_id)
            for item in self.predictions
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("snapshot has duplicate (person, bill) predictions")
        if self.meta.prediction_count != len(self.predictions):
            raise ValueError("snapshot meta.prediction_count must match predictions")
        return self

    def get(self, canonical_person_id: str, canonical_bill_id: str) -> ServedPrediction | None:
        key = _prediction_key(canonical_person_id, canonical_bill_id)
        for prediction in self.predictions:
            if _prediction_key(prediction.canonical_person_id, prediction.canonical_bill_id) == key:
                return prediction
        return None


def _snapshot_content_sha256(predictions: list[ServedPrediction]) -> str:
    payload = [
        prediction.model_dump(mode="json")
        for prediction in sorted(
            predictions,
            key=lambda item: (item.canonical_person_id, item.canonical_bill_id),
        )
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_prediction_snapshot(
    predictions: list[ServedPrediction],
    *,
    generated_at: datetime,
) -> PredictionSnapshot:
    """Assemble a content-addressed snapshot; the sha256 covers all predictions."""
    return PredictionSnapshot(
        meta=PredictionSnapshotMeta(
            generated_at=generated_at,
            content_sha256=_snapshot_content_sha256(predictions),
            prediction_count=len(predictions),
        ),
        predictions=predictions,
    )


class TokenBucketRateLimiter:
    """A deterministic per-client token bucket. Time is supplied by the caller."""

    def __init__(self, *, capacity: int, refill_per_second: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0.0:
            raise ValueError("refill_per_second must be positive")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._state: dict[str, tuple[float, float]] = {}

    def _tokens_at(self, client_id: str, now: float) -> float:
        tokens, last = self._state.get(client_id, (float(self._capacity), now))
        replenished = tokens + max(0.0, now - last) * self._refill_per_second
        return min(float(self._capacity), replenished)

    def allow(self, client_id: str, *, now: float) -> bool:
        """Consume a token if one is available, returning whether the call is allowed."""
        tokens = self._tokens_at(client_id, now)
        if tokens >= 1.0:
            self._state[client_id] = (tokens - 1.0, now)
            return True
        self._state[client_id] = (tokens, now)
        return False

    def retry_after(self, client_id: str, *, now: float) -> float:
        """Seconds until one token is available for ``client_id``."""
        tokens = self._tokens_at(client_id, now)
        if tokens >= 1.0:
            return 0.0
        return (1.0 - tokens) / self._refill_per_second


class PredictionReadResult(BaseModel):
    """The typed envelope returned for one read."""

    status: PredictionReadStatus
    prediction: ServedPrediction | None = None
    retry_after_seconds: float | None = None


class PredictionReadService:
    """Serves rate-limited, cited predictions from a precomputed snapshot."""

    def __init__(
        self, *, snapshot: PredictionSnapshot, rate_limiter: TokenBucketRateLimiter
    ) -> None:
        self._snapshot = snapshot
        self._rate_limiter = rate_limiter

    def get_prediction(
        self,
        *,
        client_id: str,
        canonical_person_id: str,
        canonical_bill_id: str,
        now: float,
    ) -> PredictionReadResult:
        if not self._rate_limiter.allow(client_id, now=now):
            return PredictionReadResult(
                status="rate_limited",
                retry_after_seconds=self._rate_limiter.retry_after(client_id, now=now),
            )
        prediction = self._snapshot.get(canonical_person_id, canonical_bill_id)
        if prediction is None:
            return PredictionReadResult(status="not_found")
        return PredictionReadResult(status="ok", prediction=prediction)
