"""Tests for split-conformal coverage of the defection head."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles, is_defection_prone
from src.prediction.defection_conformal import _quantile_threshold, conformal_coverage
from src.prediction.defection_head import train_defection_head
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
    for day in range(1, 41):
        out.append(
            _record(
                "D",
                is_yea=True,
                party_lean_yea=False,
                sectors=("energy_utilities",),
                day=(day % 28) + 1,
            )
        )
        out.append(
            _record(
                "L1",
                is_yea=False,
                party_lean_yea=False,
                sectors=("energy_utilities",),
                day=(day % 28) + 1,
            )
        )
        out.append(
            _record(
                "L2", is_yea=False, party_lean_yea=False, sectors=("health",), day=(day % 28) + 1
            )
        )
    return out


def test_quantile_threshold_monotone_and_bounded() -> None:
    scores = [0.1, 0.2, 0.3, 0.4, 0.9]
    q90 = _quantile_threshold(scores, 0.10)
    q50 = _quantile_threshold(scores, 0.50)
    assert q90 >= q50
    assert 0.0 <= q50 <= 1.0 and 0.0 <= q90 <= 1.0
    assert _quantile_threshold([], 0.1) == 1.0


def test_conformal_marginal_coverage_near_nominal() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    # disjoint calibration / test splits
    calibration = corpus[: len(corpus) // 2]
    test = corpus[len(corpus) // 2 :]
    coverage = conformal_coverage(
        head,
        calibration,
        test,
        profiles,
        alpha=0.10,
        slicers={"defection_prone": lambda r: is_defection_prone(r, profiles, tau=0.25)},
    )
    by_name = {c.slice_name: c for c in coverage}
    assert "overall" in by_name
    overall = by_name["overall"]
    assert overall.nominal == 0.90
    # split conformal guarantees marginal coverage >= nominal (finite-sample)
    assert overall.empirical_coverage >= 0.85
    assert 1.0 <= overall.average_set_size <= 2.0


def test_mondrian_recalibration_improves_minority_slice() -> None:
    # A minority slice the head is biased on: marginal conformal under-covers it,
    # Mondrian (per-slice threshold) restores coverage toward nominal.
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    calibration = corpus[: len(corpus) // 2]
    test = corpus[len(corpus) // 2 :]
    slicers = {"defection_prone": lambda r: is_defection_prone(r, profiles, tau=0.25)}
    marginal = {
        c.slice_name: c
        for c in conformal_coverage(head, calibration, test, profiles, slicers=slicers)
    }
    mondrian = {
        c.slice_name: c
        for c in conformal_coverage(
            head, calibration, test, profiles, slicers=slicers, mondrian=True
        )
    }
    if "defection_prone" in marginal and "defection_prone" in mondrian:
        # Mondrian coverage is at least as close to nominal as marginal on the slice.
        marg = abs(marginal["defection_prone"].empirical_coverage - 0.90)
        mond = abs(mondrian["defection_prone"].empirical_coverage - 0.90)
        assert mond <= marg + 1e-9


def test_empty_test_slice_is_skipped() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    coverage = conformal_coverage(
        head, corpus, corpus, profiles, slicers={"never": lambda _r: False}
    )
    assert all(c.slice_name != "never" for c in coverage)
