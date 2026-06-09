"""THE cross-pressured experiment: does a per-member sector signal predict defections?

A party-only per-member model is ~0% accurate on the cross-pressured slice (votes
where a member defects from their party's majority) -- by construction, since its
only signal points the wrong way there. The blueprint's fix is a per-member sector
response. On real data we build a no-leakage ``sector_lean`` signal: for the bill's
sector(s), the member's *deviation* between their pre-cutoff yea-rate on that sector
and their pre-cutoff overall yea-rate. A member who consistently votes against their
party on (say) energy will have a negative energy deviation, and ``sector_lean``
points toward the defection on a new energy bill.

``run_experiment`` reports cross-pressured (and overall / per-party) metrics BEFORE
(party_alignment only) vs AFTER (party_alignment + sector_lean, with the per-member
model's per-member slope). Strict cutoff: sector rates come only from train (pre-
cutoff) votes; eval uses those rates.
"""

from __future__ import annotations

import json
import math
from typing import Any
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.prediction.calibration import expected_calibration_error
from src.prediction.per_member_model import MemberVoteExample, train_per_member_model

_BINARY = {"yea", "nay"}


@dataclass(frozen=True)
class VoteRecord:
    member: str
    party: str
    state: str
    vote_date: date
    is_yea: bool
    party_alignment: float
    sectors: tuple[str, ...]
    is_cross_pressured: bool


@dataclass(frozen=True)
class SliceMetrics:
    brier_score: float
    log_loss: float
    accuracy: float
    ece: float
    sample_count: int


def load_rich_rollcalls(path: Path) -> list[dict[str, Any]]:
    """Load the rich roll-call JSONL (bill_id, date, congress, sectors, votes)."""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def build_vote_records(rollcalls: list[dict[str, Any]]) -> list[VoteRecord]:
    """Compute party-alignment + cross-pressure per roll-call and flatten to vote records."""
    records: list[VoteRecord] = []
    for rollcall in rollcalls:
        votes = [v for v in rollcall["votes"] if v[3] in _BINARY]
        if not votes:
            continue
        party_yea: dict[str, int] = defaultdict(int)
        party_total: dict[str, int] = defaultdict(int)
        for _bio, party, _state, choice in votes:
            party_total[party] += 1
            if choice == "yea":
                party_yea[party] += 1
        leans_yea = {p: party_yea[p] * 2 >= party_total[p] for p in party_total}
        sectors = tuple(rollcall.get("sectors", []))
        vote_date = date.fromisoformat(str(rollcall["date"]))
        for bio, party, state, choice in votes:
            is_yea = choice == "yea"
            lean = leans_yea.get(party, True)
            records.append(
                VoteRecord(
                    member=bio,
                    party=party,
                    state=state,
                    vote_date=vote_date,
                    is_yea=is_yea,
                    party_alignment=1.0 if lean else -1.0,
                    sectors=sectors,
                    is_cross_pressured=is_yea != lean,
                )
            )
    return records


def _sector_lean_index(
    train: list[VoteRecord],
) -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    """Per-(member, sector) and per-member overall yea-rates from pre-cutoff votes."""
    sector_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    member_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in train:
        member_counts[record.member][0] += int(record.is_yea)
        member_counts[record.member][1] += 1
        for sector in record.sectors:
            sector_counts[(record.member, sector)][0] += int(record.is_yea)
            sector_counts[(record.member, sector)][1] += 1
    sector_rate = {key: yea / total for key, (yea, total) in sector_counts.items() if total}
    member_rate = {m: yea / total for m, (yea, total) in member_counts.items() if total}
    return sector_rate, member_rate


def _sector_lean(
    record: VoteRecord,
    sector_rate: dict[tuple[str, str], float],
    member_rate: dict[str, float],
) -> float:
    deviations = [
        sector_rate[(record.member, sector)] - member_rate.get(record.member, 0.5)
        for sector in record.sectors
        if (record.member, sector) in sector_rate
    ]
    if not deviations:
        return 0.0
    return 2.0 * (sum(deviations) / len(deviations))  # scale [-1,1]-ish


def _to_example(record: VoteRecord, *, with_sector: float | None) -> MemberVoteExample:
    signals = {"party_alignment": record.party_alignment}
    if with_sector is not None:
        signals["sector_lean"] = with_sector
    return MemberVoteExample(member_id=record.member, signals=signals, is_yea=record.is_yea)


def _log_loss(probability: float, is_yea: bool) -> float:
    clamped = min(1.0 - 1e-15, max(1e-15, probability))
    return -(math.log(clamped) if is_yea else math.log(1.0 - clamped))


def _slice_metrics(scored: list[tuple[float, bool]]) -> SliceMetrics:
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in scored) / len(scored)
    log_loss = sum(_log_loss(p, y) for p, y in scored) / len(scored)
    accuracy = sum(1 for p, y in scored if (p >= 0.5) == y) / len(scored)
    ece = expected_calibration_error([p for p, _ in scored], [y for _, y in scored])
    return SliceMetrics(brier, log_loss, accuracy, ece, len(scored))


def _evaluate(
    model: object,
    eval_records: list[VoteRecord],
    sector_rate: dict[tuple[str, str], float],
    member_rate: dict[str, float],
    *,
    with_sector: bool,
) -> dict[str, SliceMetrics]:
    buckets: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for record in eval_records:
        lean = _sector_lean(record, sector_rate, member_rate) if with_sector else None
        signals = {"party_alignment": record.party_alignment}
        if lean is not None:
            signals["sector_lean"] = lean
        probability = model.predict_probability(record.member, signals)  # type: ignore[attr-defined]
        entry = (probability, record.is_yea)
        buckets["overall"].append(entry)
        if record.is_cross_pressured:
            buckets["cross_pressured"].append(entry)
        buckets[f"party:{record.party}"].append(entry)
    return {name: _slice_metrics(rows) for name, rows in buckets.items() if rows}


def run_experiment(
    rollcalls: list[dict[str, Any]],
    *,
    cutoff: date,
    eval_end: date,
    max_train: int | None = None,
) -> dict[str, dict[str, SliceMetrics]]:
    """Return ``{'before': slices, 'after': slices}`` for the cross-pressured experiment."""
    records = build_vote_records(rollcalls)
    train = [r for r in records if r.vote_date <= cutoff]
    eval_records = [r for r in records if cutoff < r.vote_date <= eval_end]
    if max_train is not None and len(train) > max_train:
        train = train[:max_train]

    sector_rate, member_rate = _sector_lean_index(train)

    before_model = train_per_member_model([_to_example(r, with_sector=None) for r in train])
    after_model = train_per_member_model(
        [_to_example(r, with_sector=_sector_lean(r, sector_rate, member_rate)) for r in train]
    )
    return {
        "before": _evaluate(
            before_model, eval_records, sector_rate, member_rate, with_sector=False
        ),
        "after": _evaluate(after_model, eval_records, sector_rate, member_rate, with_sector=True),
    }
