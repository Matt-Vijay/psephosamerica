"""Tests for the LLM-forecaster defection stacking eval (stub path)."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles
from src.prediction.defection_head import train_defection_head
from src.runtime.cross_pressured_experiment import VoteRecord
from src.runtime.llm_defection_eval import (
    anthropic_key_present,
    evaluate_llm_stack,
    stub_llm_forecaster,
)


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
        out.append(
            _record("D", is_yea=True, party_lean_yea=False, sectors=("energy_utilities",), day=day)
        )
        out.append(
            _record(
                "L1", is_yea=False, party_lean_yea=False, sectors=("energy_utilities",), day=day
            )
        )
        out.append(_record("L2", is_yea=False, party_lean_yea=False, sectors=("health",), day=day))
    return out


def test_stub_forecaster_in_unit_range() -> None:
    p = stub_llm_forecaster({"sector_divergence": 0.9, "loyalty_gap": 0.5})
    assert 0.0 <= p <= 1.0


def test_stack_reports_aucs_and_weights() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    result = evaluate_llm_stack(head, corpus, corpus, profiles)
    assert result.used_real_llm is False
    for auc in (result.head_auc, result.llm_auc, result.stacked_auc):
        assert 0.0 <= auc <= 1.0
    # stacked should not be worse than the better single member by much (learned blend)
    assert result.stacked_auc >= max(result.head_auc, result.llm_auc) - 0.1
    assert result.eval_pairs == len(corpus)


def test_real_llm_flag_tracks_forecaster_arg() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    result = evaluate_llm_stack(head, corpus, corpus, profiles, forecaster=lambda f: 0.5)
    assert result.used_real_llm is True
    assert isinstance(anthropic_key_present(), bool)
