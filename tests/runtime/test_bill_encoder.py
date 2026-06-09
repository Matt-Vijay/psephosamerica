"""Tests for the bill-embedding index + four-stream bill-stream exercise."""

from __future__ import annotations

import numpy as np

from src.runtime.contract_corpus import ContractEntity, bill_embedding_index
from src.runtime.four_stream_real import init_embedding_projections
from src.prediction.nn.token_projection import project


def _bill(cid: str) -> ContractEntity:
    return ContractEntity(
        canonical_id=cid,
        entity_type="bill",
        external_ids=(),
        dossier_embedding=np.linspace(0.0, 1.0, 256),
        structural_embedding=np.linspace(0.0, 1.0, 64),
    )


def test_bill_index_selects_only_bills() -> None:
    person = ContractEntity("p", "person", (), np.zeros(256), np.zeros(64))
    index = bill_embedding_index([_bill("cb-1"), person, _bill("cb-2")])
    assert set(index) == {"cb-1", "cb-2"}


def test_bill_embedding_projects_to_token() -> None:
    rng = np.random.default_rng(0)
    dproj, _sproj = init_embedding_projections(
        dossier_dim=256, structural_dim=64, d_model=8, rng=rng
    )
    token, _ = project(_bill("cb-1").dossier_embedding, dproj)
    assert token.shape == (8,)
