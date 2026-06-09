"""Tests for multi-task dense-auxiliary transfer to defection AUC."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles
from src.prediction.defection_multitask import multitask_transfer, no_auxiliaries
from src.runtime.cross_pressured_experiment import VoteRecord


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


def test_no_auxiliaries_gives_zero_coverage_and_delta() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    report = multitask_transfer(corpus, corpus, profiles, aux=no_auxiliaries)
    assert report.aux_coverage == 0.0
    # with no auxiliary signal the augmented head matches the base head
    assert abs(report.delta_auc) < 1e-6
    assert report.eval_pairs == len(corpus)


def test_informative_auxiliary_lifts_auc() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)

    # An oracle-ish auxiliary that flags the known defector raises defection AUC.
    def aux(record: VoteRecord) -> dict[str, float]:
        return {"cosponsor": 1.0 if record.member == "D" else 0.0}

    report = multitask_transfer(corpus, corpus, profiles, aux=aux)
    assert report.aux_coverage > 0.0
    assert report.multitask_auc >= report.base_auc - 1e-9
