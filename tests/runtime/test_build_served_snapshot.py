"""Tests for building a real served-prediction snapshot (offline)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np

from src.prediction.per_member_ensemble import train_per_member_seed_ensemble
from src.prediction.per_member_model import MemberVoteExample, train_per_member_model
from src.runtime.build_served_snapshot import build_served_snapshot
from src.runtime.contract_corpus import ContractEntity, EvidenceAnchorData


def _entity(bioguide: str) -> ContractEntity:
    return ContractEntity(
        canonical_id=f"ce-{bioguide}",
        entity_type="person",
        external_ids=(f"bioguide:{bioguide.lower()}",),
        dossier_embedding=np.zeros(256),
        structural_embedding=np.zeros(64),
        evidence=(
            EvidenceAnchorData(
                label="house_clerk:roll021",
                source_url="https://clerk.house.gov/evs/2023/roll021.xml",
                content_sha256="a" * 64,
                known_at=datetime(2023, 1, 1, tzinfo=UTC),
            ),
        ),
    )


def _models() -> list:
    examples = [
        MemberVoteExample(member_id="A000055", signals={"party_alignment": a}, is_yea=a > 0)
        for a in ([1.0, -1.0] * 30)
    ]
    return train_per_member_seed_ensemble(examples, seeds=[1, 2, 3])


def test_snapshot_has_real_cited_predictions() -> None:
    snapshot = build_served_snapshot(
        _models(),
        {"A000055": _entity("A000055")},
        generated_at=datetime(2026, 6, 9, tzinfo=UTC),
        known_at=date(2026, 6, 9),
        top_n=10,
    )
    assert snapshot.meta.prediction_count == 1
    prediction = snapshot.predictions[0]
    assert prediction.canonical_person_id == "bioguide:A000055"
    assert prediction.evidence_anchors[0].source_url.startswith("https://clerk.house.gov")
    assert prediction.counterfactual
    assert prediction.uncertainty.interval_lower <= prediction.probability_yea


def test_member_without_dated_evidence_is_skipped() -> None:
    entity = _entity("B000001")
    late = ContractEntity(
        canonical_id=entity.canonical_id,
        entity_type=entity.entity_type,
        external_ids=entity.external_ids,
        dossier_embedding=entity.dossier_embedding,
        structural_embedding=entity.structural_embedding,
        evidence=(
            EvidenceAnchorData(
                label="future",
                source_url="https://clerk.house.gov/x",
                content_sha256="b" * 64,
                known_at=datetime(2030, 1, 1, tzinfo=UTC),  # after known_at
            ),
        ),
    )
    models = [
        train_per_member_model(
            [MemberVoteExample(member_id="B000001", signals={"party_alignment": 1.0}, is_yea=True)]
        )
    ]
    snapshot = build_served_snapshot(
        models,
        {"B000001": late},
        generated_at=datetime(2026, 6, 9, tzinfo=UTC),
        known_at=date(2026, 6, 9),
    )
    # All its evidence post-dates known_at, so it cannot be served (no citation).
    assert snapshot.meta.prediction_count == 0
