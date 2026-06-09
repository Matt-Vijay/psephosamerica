"""Tests for 10-seed Bayesian defection uncertainty + content-addressed checkpoints."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.prediction.defection import build_party_profiles, defection_features
from src.prediction.defection_bayesian import (
    bootstrap_seed_ensemble,
    checkpoint_path,
    content_address,
    ensemble_auc,
    ensemble_payload,
    ensemble_predict,
    pin_checkpoint,
    verify_checkpoint,
)
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


def test_ten_seed_ensemble_gives_uncertainty() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    heads = bootstrap_seed_ensemble(corpus, profiles, n_seeds=10)
    assert len(heads) == 10
    pred = ensemble_predict(heads, defection_features(corpus[0], profiles))
    assert 0.0 <= pred.mean <= 1.0
    assert pred.std >= 0.0
    auc = ensemble_auc(heads, corpus, profiles)
    assert auc.n_seeds == 10 and len(auc.per_seed_auc) == 10
    assert 0.0 <= auc.mean_auc <= 1.0 and auc.std_auc >= 0.0


def test_content_address_is_deterministic_and_order_invariant() -> None:
    a = {"model_name": "x", "heads": [{"coefficients": {"b": 1.0, "a": 2.0}, "intercept": 0.0}]}
    b = {"heads": [{"intercept": 0.0, "coefficients": {"a": 2.0, "b": 1.0}}], "model_name": "x"}
    assert content_address(a) == content_address(b)


def test_pin_and_verify_checkpoint(tmp_path: Path) -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    heads = bootstrap_seed_ensemble(corpus, profiles, n_seeds=3)
    payload = ensemble_payload(heads, model_name="defection_head_bayes")
    digest, path = pin_checkpoint(tmp_path, payload)
    assert path == checkpoint_path(tmp_path, digest)
    assert path.exists()
    assert verify_checkpoint(path, digest) is True
    assert verify_checkpoint(path, "0" * 64) is False
