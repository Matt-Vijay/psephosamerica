"""The bitemporal provenance envelope carried by every graph node and edge.

The blueprint (``OVERALL_GOAL.md``) requires that every node and edge in the
Person x Bill x Org knowledge graph carry
``(source_url, content_sha256, first_observed_at, valid_from, valid_to)`` and
that "every fact [be] replayable from immutable raw artifacts to any time-``t``
snapshot." This module is the single value object that enforces that contract.

Two distinct time axes are modelled (a bitemporal record):

* **Valid time** — ``[valid_from, valid_to)``: the half-open window during which
  the fact was true *in the world*. ``valid_to=None`` means "still true".
* **Known time** — ``known_at``: the earliest wall-clock instant at which the
  fact was publicly *knowable*. This is the bridge to the strict-cutoff leakage
  discipline in ``src/prediction/backtest.py``: a prediction made as of time
  ``t`` may only consume facts whose ``known_at <= t``. A fact can never be
  observed before it is knowable, so ``known_at <= first_observed_at`` always.

``content_sha256`` is the content-address of the raw artifact in the immutable
lake; :meth:`ProvenanceEnvelope.content_address` renders its sharded lake path.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HTTP_SCHEMES = ("http://", "https://")


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    """Reject naive datetimes; normalize aware ones to UTC for total ordering."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class ProvenanceEnvelope(BaseModel):
    """Immutable provenance + bitemporal validity for one graph fact."""

    model_config = ConfigDict(frozen=True)

    source_url: str = Field(
        description="Public http(s) URL of the record that backs this fact.",
    )
    content_sha256: str = Field(
        description="sha256 (64 lowercase hex) content-address of the raw artifact.",
    )
    first_observed_at: datetime = Field(
        description="When ingestion first saw this artifact (tz-aware, stored UTC).",
    )
    valid_from: date = Field(
        description="Start of the valid-time window (the fact became true).",
    )
    valid_to: date | None = Field(
        default=None,
        description="Exclusive end of the valid-time window; None means still valid.",
    )
    known_at: datetime = Field(
        description="Earliest instant the fact was publicly knowable (leakage gate).",
    )

    @field_validator("source_url", mode="before")
    @classmethod
    def _strip_source_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("content_sha256", mode="before")
    @classmethod
    def _normalize_sha(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("source_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value or not value.startswith(_HTTP_SCHEMES):
            raise ValueError("source_url must be a non-blank http:// or https:// URL")
        return value

    @field_validator("content_sha256")
    @classmethod
    def _require_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("content_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("first_observed_at")
    @classmethod
    def _aware_first_observed_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="first_observed_at")

    @field_validator("known_at")
    @classmethod
    def _aware_known_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="known_at")

    @model_validator(mode="after")
    def _check_intervals(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be strictly after valid_from")
        if self.known_at > self.first_observed_at:
            raise ValueError(
                "known_at must not be after first_observed_at "
                "(a fact cannot be observed before it is knowable)"
            )
        return self

    # ── Queries ────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """True when the fact has no recorded end (``valid_to is None``)."""
        return self.valid_to is None

    def covers(self, as_of: date) -> bool:
        """True when ``as_of`` falls in the half-open ``[valid_from, valid_to)``."""
        if as_of < self.valid_from:
            return False
        return self.valid_to is None or as_of < self.valid_to

    def known_as_of(self, cutoff: datetime) -> bool:
        """The strict-cutoff leakage gate: was this fact knowable by ``cutoff``?

        ``cutoff`` must be timezone-aware so the comparison is unambiguous.
        """
        aware = _require_aware_utc(cutoff, field_name="cutoff")
        return self.known_at <= aware

    def content_address(self) -> str:
        """The sharded content-addressed path of the raw artifact in the lake."""
        sha = self.content_sha256
        return f"sha256/{sha[:2]}/{sha[2:4]}/{sha}"
