"""Tests for the defection ranking head."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles
from src.prediction.defection_head import (
    evaluate_defection_head,
    rank_defections,
    train_defection_head,
)
from src.runtime.cross_pressured_experiment import VoteRecord


def _record(
    member: str, party: str, *, is_yea: bool, party_lean_yea: bool, sectors: tuple[str, ...], day: int
) -> VoteRecord:
    return VoteRecord(
        member=member,
        party=party,
        state="CA",
        vote_date=date(2023, 1, day),
        is_yea=is_yea,
        party_alignment=1.0 if party_lean_yea else -1.0,
        sectors=sectors,
        is_cross_pressured=is_yea != party_lean_yea,
    )


def _corpus() -> list[VoteRecord]:
    # Defector D consistently breaks party on energy; loyalists L1/L2 never do.
    records: list[VoteRecord] = []
    for day in range(1, 21):
        records.append(
            _record("D", "R", is_yea=True, party_lean_yea=False, sectors=("energy",), day=day)
        )
        records.append(
            _record("L1", "R", is_yea=False, party_lean_yea=False, sectors=("energy",), day=day)
        )
        records.append(
            _record("L2", "R", is_yea=False, party_lean_yea=False, sectors=("energy",), day=day)
        )
    return records


def test_head_ranks_known_defector_first() -> None:
    records = _corpus()
    profiles = build_party_profiles(records)
    head = train_defection_head(records, profiles)
    ranked = rank_defections(head, records, profiles, top_n=3)
    assert ranked[0].member == "D"
    assert ranked[0].probability >= ranked[-1].probability


def test_head_auc_above_chance_on_holdout() -> None:
    records = _corpus()
    profiles = build_party_profiles(records)
    head = train_defection_head(records, profiles)
    metrics = evaluate_defection_head(head, records, profiles)
    assert metrics.auc > 0.7
    assert 0.0 <= metrics.precision_at_10 <= 1.0


def test_contributions_cover_features() -> None:
    records = _corpus()
    profiles = build_party_profiles(records)
    head = train_defection_head(records, profiles)
    contributions = head.contributions({"loyalty_gap": 0.5, "sector_divergence": 0.4})
    assert set(contributions) == {"loyalty_gap", "sector_divergence"}


def test_empty_train_returns_constant_half_head() -> None:
    head = train_defection_head([], build_party_profiles([]))
    assert head.intercept == 0.0
    assert all(v == 0.0 for v in head.coefficients.values())
    assert head.probability({"loyalty_gap": 1.0, "sector_divergence": 1.0}) == 0.5


def test_rank_with_truth_populates_actual_defected() -> None:
    records = _corpus()
    profiles = build_party_profiles(records)
    head = train_defection_head(records, profiles)
    with_truth = rank_defections(head, records, profiles, top_n=1, with_truth=True)
    without_truth = rank_defections(head, records, profiles, top_n=1)
    assert with_truth[0].actual_defected is True  # D voted against party lean
    assert without_truth[0].actual_defected is None
