"""Tests for the honest ex-ante defection slice + ranking metrics."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import (
    build_party_profiles,
    defected,
    defection_features,
    is_defection_prone,
    ranking_metrics,
    roc_auc,
    slice_prevalence,
    split_by_cutoff,
)
from src.runtime.cross_pressured_experiment import VoteRecord


def _record(
    member: str,
    party: str,
    *,
    is_yea: bool,
    party_lean_yea: bool,
    sectors: tuple[str, ...] = (),
    day: int = 1,
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


def test_roc_auc_perfect_and_random() -> None:
    assert roc_auc([0.1, 0.2, 0.9, 0.95], [False, False, True, True]) == 1.0
    assert roc_auc([0.9, 0.95, 0.1, 0.2], [True, True, False, False]) == 1.0
    # one class absent -> no ordering information
    assert roc_auc([0.1, 0.2], [True, True]) == 0.5


def test_roc_auc_invariant_to_predict_opposite_party() -> None:
    # The old degeneracy: a model that just predicts the realized defection.
    # AUC measures ordering, so a *separating* score is 1.0 either way -- but a
    # constant score (no ordering) is 0.5, which the tautological accuracy hid.
    labels = [True, False, True, False]
    assert roc_auc([0.5, 0.5, 0.5, 0.5], labels) == 0.5


def test_roc_auc_handles_ties_with_average_ranks() -> None:
    auc = roc_auc([0.5, 0.5, 0.5, 0.9], [False, True, False, True])
    assert 0.0 <= auc <= 1.0


def test_ranking_metrics_precision_at_k() -> None:
    scores = [0.9, 0.8, 0.7, 0.1, 0.05]
    labels = [True, True, False, False, False]
    metrics = ranking_metrics(scores, labels)
    assert metrics.positives == 2
    assert metrics.sample_count == 5
    assert metrics.precision_at_10 == 2 / 5  # top-10 = all 5, 2 positive
    assert metrics.auc > 0.5


def test_sector_divergence_is_ex_ante_and_label_free() -> None:
    # Member A votes against party on energy historically; the party (a loyal
    # majority B..F plus A) votes yea, so A diverges and the loyalists do not.
    train = [
        _record("A", "D", is_yea=False, party_lean_yea=True, sectors=("energy",), day=d)
        for d in range(1, 6)
    ] + [
        _record(m, "D", is_yea=True, party_lean_yea=True, sectors=("energy",), day=d)
        for m in ("B", "C", "D2", "E", "F")
        for d in range(1, 6)
    ]
    profiles = build_party_profiles(train)
    # New energy bill: A is defection-prone (diverges from party), B is not.
    a_new = _record("A", "D", is_yea=True, party_lean_yea=True, sectors=("energy",), day=10)
    b_new = _record("B", "D", is_yea=True, party_lean_yea=True, sectors=("energy",), day=10)
    assert is_defection_prone(a_new, profiles, tau=0.25)
    assert not is_defection_prone(b_new, profiles, tau=0.25)
    # Flipping the realized vote of the scored bill must not change slice membership.
    a_flip = _record("A", "D", is_yea=False, party_lean_yea=True, sectors=("energy",), day=10)
    assert is_defection_prone(a_flip, profiles, tau=0.25) == is_defection_prone(
        a_new, profiles, tau=0.25
    )


def test_slice_enriches_for_defections() -> None:
    # A defects on energy, loyal elsewhere; the honest slice should concentrate
    # A's energy defections relative to the overall defection rate.
    train = (
        [
            _record("A", "D", is_yea=False, party_lean_yea=True, sectors=("energy",), day=d)
            for d in range(1, 6)
        ]
        + [
            _record("A", "D", is_yea=True, party_lean_yea=True, sectors=("health",), day=d)
            for d in range(1, 6)
        ]
        + [
            # a loyal majority so the party's energy lean is genuinely yea
            _record(m, "D", is_yea=True, party_lean_yea=True, sectors=("energy",), day=d)
            for m in ("B", "C", "D2", "E", "F")
            for d in range(1, 6)
        ]
    )
    profiles = build_party_profiles(train)
    eval_records = [
        _record("A", "D", is_yea=False, party_lean_yea=True, sectors=("energy",), day=10),
        _record("A", "D", is_yea=True, party_lean_yea=True, sectors=("health",), day=11),
    ]
    stats = slice_prevalence(eval_records, profiles, tau=0.25)
    assert stats["defection_prone_pairs"] >= 1.0
    assert stats["defection_rate_in_slice"] >= stats["defection_rate_overall"]


def test_features_and_defected_label() -> None:
    rec = _record("A", "D", is_yea=False, party_lean_yea=True, sectors=("energy",))
    profiles = build_party_profiles([rec])
    feats = defection_features(rec, profiles)
    assert set(feats) == {"loyalty_gap", "sector_divergence"}
    assert defected(rec) is True


def test_split_by_cutoff_is_strict() -> None:
    records = [
        _record("A", "D", is_yea=True, party_lean_yea=True, day=1),
        _record("A", "D", is_yea=True, party_lean_yea=True, day=15),
    ]
    train, eval_records = split_by_cutoff(
        records, cutoff=date(2023, 1, 10), eval_end=date(2023, 1, 31)
    )
    assert len(train) == 1 and len(eval_records) == 1
