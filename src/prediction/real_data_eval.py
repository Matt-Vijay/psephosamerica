"""Real-data evaluation runner over Track A's vote corpus.

Consumes real vote rows (Track A's federal House feed -- member, bill, vote,
date, party, state, plus the model's input signals) and reports the first real
Brier / log-loss / accuracy / ECE for the per-member model, sliced by all /
cross-pressured / per-party / per-state, over rolling strict-cutoff windows
(one per recent congress).

No-leakage is enforced by construction: for each window the model trains only
on votes dated on or before ``train_end`` and is evaluated only on votes inside
``[eval_start, eval_end]`` -- a member with no pre-cutoff votes is predicted
from the global fallback, never from its own future vote. The runner is pure
Python; it produces real numbers the moment the corpus is present and is tested
here against valid-format fixture rows.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.prediction.calibration import expected_calibration_error
from src.prediction.per_member_model import (
    MemberVoteExample,
    train_per_member_model,
)

_BINARY = {"yea", "nay"}


@dataclass(frozen=True)
class VoteRow:
    """One realized vote with its model input signals."""

    member_bioguide_id: str
    canonical_bill_id: str
    vote_option: str
    vote_date: date
    party: str | None
    state: str | None
    jurisdiction_id: str
    signals: dict[str, float] = field(default_factory=dict)
    is_cross_pressured: bool = False

    @property
    def is_binary(self) -> bool:
        return self.vote_option in _BINARY

    @property
    def is_yea(self) -> bool:
        return self.vote_option == "yea"


@dataclass(frozen=True)
class EvalWindow:
    """A rolling strict-cutoff window: train on/before ``train_end``, eval inside."""

    name: str
    train_end: date
    eval_start: date
    eval_end: date


@dataclass(frozen=True)
class SliceMetrics:
    """Real metrics for one evaluation slice."""

    slice_name: str
    brier_score: float
    log_loss: float
    accuracy: float
    ece: float
    sample_count: int


def load_vote_rows(path: Path) -> list[VoteRow]:
    """Load a JSONL vote corpus (one VoteRow object per line)."""
    rows: list[VoteRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        rows.append(
            VoteRow(
                member_bioguide_id=record["member_bioguide_id"],
                canonical_bill_id=record["canonical_bill_id"],
                vote_option=record["vote_option"],
                vote_date=date.fromisoformat(record["vote_date"]),
                party=record.get("party"),
                state=record.get("state"),
                jurisdiction_id=record.get("jurisdiction_id", "us_congress"),
                signals={key: float(value) for key, value in record.get("signals", {}).items()},
                is_cross_pressured=bool(record.get("is_cross_pressured", False)),
            )
        )
    return rows


def _log_loss(probability: float, is_yea: bool) -> float:
    clamped = min(1.0 - 1e-15, max(1e-15, probability))
    return -(math.log(clamped) if is_yea else math.log(1.0 - clamped))


def _slice_metrics(slice_name: str, scored: list[tuple[float, bool]]) -> SliceMetrics:
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in scored) / len(scored)
    log_loss = sum(_log_loss(p, y) for p, y in scored) / len(scored)
    accuracy = sum(1 for p, y in scored if (p >= 0.5) == y) / len(scored)
    ece = expected_calibration_error([p for p, _ in scored], [y for _, y in scored])
    return SliceMetrics(
        slice_name=slice_name,
        brier_score=brier,
        log_loss=log_loss,
        accuracy=accuracy,
        ece=ece,
        sample_count=len(scored),
    )


def _evaluate_one_window(vote_rows: list[VoteRow], window: EvalWindow) -> dict[str, SliceMetrics]:
    train_rows = [row for row in vote_rows if row.is_binary and row.vote_date <= window.train_end]
    eval_rows = [
        row
        for row in vote_rows
        if row.is_binary and window.eval_start <= row.vote_date <= window.eval_end
    ]
    if not eval_rows:
        return {}

    model = train_per_member_model(
        [
            MemberVoteExample(
                member_id=row.member_bioguide_id, signals=row.signals, is_yea=row.is_yea
            )
            for row in train_rows
        ]
    )

    # (slice_name -> list of (probability, is_yea)) over the evaluation votes.
    buckets: dict[str, list[tuple[float, bool]]] = {"all": []}
    for row in eval_rows:
        probability = model.predict_probability(row.member_bioguide_id, row.signals)
        scored = (probability, row.is_yea)
        buckets["all"].append(scored)
        if row.is_cross_pressured:
            buckets.setdefault("cross_pressured", []).append(scored)
        if row.party:
            buckets.setdefault(f"party:{row.party}", []).append(scored)
        if row.state:
            buckets.setdefault(f"state:{row.state}", []).append(scored)

    return {name: _slice_metrics(name, scored) for name, scored in buckets.items() if scored}


def evaluate_windows(
    vote_rows: Iterable[VoteRow],
    *,
    windows: list[EvalWindow],
) -> dict[str, dict[str, SliceMetrics]]:
    """Evaluate the per-member model per window, returning window -> slice -> metrics."""
    materialized = list(vote_rows)
    return {window.name: _evaluate_one_window(materialized, window) for window in windows}
