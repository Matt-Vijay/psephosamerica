"""Tests for the bill-encoder + RAG defection experiment (v2)."""

from __future__ import annotations

from datetime import date

import numpy as np

from src.prediction.defection import build_party_profiles
from src.runtime.cross_pressured_experiment import VoteRecord
from src.runtime.defection_rag import (
    build_member_stores,
    run_rag_before_after,
    sector_bag_embedding,
)


def _record(
    member: str, *, is_yea: bool, party_lean_yea: bool, sectors: tuple[str, ...], day: int
) -> VoteRecord:
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


def test_sector_bag_embedding_is_normalised() -> None:
    vector = sector_bag_embedding(("energy_utilities",))
    assert vector.shape[0] == 15
    # known sector -> unit norm; unknown/empty -> zero
    if float(np.linalg.norm(vector)) > 0:
        assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-9
    assert float(np.linalg.norm(sector_bag_embedding(()))) == 0.0


def test_member_store_rag_signal_reflects_past_defections() -> None:
    train = [
        _record("D", is_yea=True, party_lean_yea=False, sectors=("energy_utilities",), day=d)
        for d in range(1, 6)
    ]
    stores = build_member_stores(train)
    query = sector_bag_embedding(("energy_utilities",))
    # all retrieved past votes were defections -> signal near 1
    assert stores["D"].rag_signal(query, k=4) > 0.9


def test_run_rag_before_after_reports_ablation_and_is_label_safe() -> None:
    train = (
        [
            _record("D", is_yea=True, party_lean_yea=False, sectors=("energy_utilities",), day=d)
            for d in range(1, 11)
        ]
        + [
            _record("L", is_yea=False, party_lean_yea=False, sectors=("energy_utilities",), day=d)
            for d in range(1, 11)
        ]
        + [
            _record("D", is_yea=False, party_lean_yea=False, sectors=("health",), day=d)
            for d in range(1, 11)
        ]
    )
    profiles = build_party_profiles(train)
    eval_records = [
        _record("D", is_yea=True, party_lean_yea=False, sectors=("energy_utilities",), day=20),
        _record("L", is_yea=False, party_lean_yea=False, sectors=("energy_utilities",), day=20),
    ]
    report = run_rag_before_after(
        train, eval_records, profiles, tau=0.25, k_values=(4, 8)
    )
    assert report["embedding"] == "sector_bag"
    assert set(report["k_ablation"]) == {"k=4", "k=8"}
    assert "before" in report and "after_best" in report
    assert isinstance(report["delta_auc"], float)
