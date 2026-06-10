"""Tests for non-bill defection signals + ΔAUC harness."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles
from src.prediction.defection_signals import (
    evaluate_signal,
    hierarchical_defection_prior,
)
from src.prediction.vote_record import VoteRecord


def _record(member: str, *, is_yea: bool, party_lean_yea: bool, sectors, day: int) -> VoteRecord:
    return VoteRecord(
        member=member,
        party="R",
        state="TX",
        vote_date=date(2023, 1, day),
        is_yea=is_yea,
        party_alignment=1.0 if party_lean_yea else -1.0,
        sectors=sectors,
        is_cross_pressured=is_yea != party_lean_yea,
    )


def _corpus() -> list[VoteRecord]:
    out: list[VoteRecord] = []
    for day in range(1, 21):
        out.append(_record("D", is_yea=True, party_lean_yea=False, sectors=("energy_utilities",), day=day))
        out.append(_record("L1", is_yea=False, party_lean_yea=False, sectors=("energy_utilities",), day=day))
        out.append(_record("L2", is_yea=False, party_lean_yea=False, sectors=("health",), day=day))
    return out


def test_hierarchical_prior_shrinks_thin_records() -> None:
    train = _corpus()
    provider = hierarchical_defection_prior(train, member_strength=20.0)
    # frequent defector D -> high prior; loyalists -> low prior
    d = provider(_record("D", is_yea=True, party_lean_yea=False, sectors=(), day=1))["defection_prior"]
    l1 = provider(_record("L1", is_yea=False, party_lean_yea=False, sectors=(), day=1))["defection_prior"]
    assert d > l1
    # an unknown member falls back to the party/global rate (between 0 and 1)
    unknown = provider(_record("ZZ", is_yea=True, party_lean_yea=False, sectors=(), day=1))["defection_prior"]
    assert 0.0 <= unknown <= 1.0


def test_evaluate_signal_reports_delta() -> None:
    train = _corpus()
    profiles = build_party_profiles(train)
    provider = hierarchical_defection_prior(train)
    result = evaluate_signal(
        train,
        train,
        profiles,
        signal_name="hierarchical_prior",
        providers=[provider],
        extra_feature_names=("defection_prior",),
    )
    assert result.signal_name == "hierarchical_prior"
    assert result.delta_auc == result.augmented_auc - result.base_auc
    assert "defection_prior" in result.coefficients
    assert 0.0 <= result.augmented_auc <= 1.0
