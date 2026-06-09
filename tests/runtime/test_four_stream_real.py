"""Tests for building four-stream examples from real votes + embeddings."""

from __future__ import annotations

from datetime import date

import numpy as np

from src.runtime.contract_corpus import ContractEntity
from src.runtime.four_stream_real import (
    build_four_stream_real_examples,
    init_embedding_projections,
)
from src.prediction.real_data_eval import VoteRow


def _entity(bioguide: str) -> ContractEntity:
    return ContractEntity(
        canonical_id=f"ce-{bioguide}",
        entity_type="person",
        external_ids=(f"bioguide:{bioguide.lower()}",),
        dossier_embedding=np.linspace(0.0, 1.0, 256),
        structural_embedding=np.linspace(0.0, 1.0, 64),
    )


def _row(member: str, *, aligned: float, yea: bool) -> VoteRow:
    return VoteRow(
        member_bioguide_id=member,
        canonical_bill_id="bill",
        vote_option="yea" if yea else "nay",
        vote_date=date(2024, 6, 1),
        party="D",
        state="CA",
        jurisdiction_id="us_congress",
        signals={"party_alignment": aligned},
    )


def test_build_examples_uses_real_embedding_dims() -> None:
    rng = np.random.default_rng(0)
    dossier_proj, structural_proj = init_embedding_projections(
        dossier_dim=256, structural_dim=64, d_model=8, rng=rng
    )
    index = {"M000001": _entity("M000001")}
    rows = [_row("M000001", aligned=1.0, yea=True), _row("M000001", aligned=-1.0, yea=False)]
    examples = build_four_stream_real_examples(
        rows,
        index,
        dossier_projection=dossier_proj,
        structural_projection=structural_proj,
        d_model=8,
    )
    assert len(examples) == 2
    ex = examples[0]
    assert ex.politician_structural.shape == (8,)
    assert ex.politician_dossier.shape == (8,)
    assert ex.context_tokens.shape == (1, 8)
    assert ex.context_tokens[0, 0] == 1.0  # party_alignment in the context token


def test_votes_without_embeddings_are_skipped() -> None:
    rng = np.random.default_rng(1)
    dossier_proj, structural_proj = init_embedding_projections(
        dossier_dim=256, structural_dim=64, d_model=8, rng=rng
    )
    index = {"M000001": _entity("M000001")}
    rows = [_row("M000001", aligned=1.0, yea=True), _row("UNKNOWN", aligned=1.0, yea=True)]
    examples = build_four_stream_real_examples(
        rows,
        index,
        dossier_projection=dossier_proj,
        structural_projection=structural_proj,
        d_model=8,
    )
    assert len(examples) == 1  # the member without an embedding is dropped
