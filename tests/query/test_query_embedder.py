"""Query embedder: determinism + byte-parity with the contract's embedder."""

from __future__ import annotations

import numpy as np

from src.query.query_embedder import DEFAULT_DIM, QueryEmbedder


def test_deterministic_and_normalized() -> None:
    embedder = QueryEmbedder()
    a = embedder.embed("redundant housing ordinance")
    b = embedder.embed("redundant housing ordinance")
    assert a == b
    assert len(a) == DEFAULT_DIM
    assert abs(np.linalg.norm(a) - 1.0) < 1e-9


def test_empty_text_is_zero_vector() -> None:
    assert QueryEmbedder().embed("") == [0.0] * DEFAULT_DIM


def test_callable_alias_matches_embed() -> None:
    embedder = QueryEmbedder()
    assert embedder("tax policy") == embedder.embed("tax policy")


def test_parity_with_contract_embedder() -> None:
    """Must produce byte-identical vectors to the embedder Track A used.

    If this drifts, query vectors stop being cosine-comparable to the stored
    ``dossier_embedding`` vectors and retrieval silently degrades.
    """
    from src.graph.enrichment.local_embedder import LocalTextEmbedder

    contract = LocalTextEmbedder()
    mine = QueryEmbedder()
    for text in ["Affordable Housing Act", "donor influence on appropriations", "AB1566"]:
        assert mine.embed(text) == contract(text)

    # Pinned from the pre-consolidation implementation, not from the shared
    # class: protects stored-vector compatibility even when both names alias it.
    assert QueryEmbedder(dim=8).embed("Tax reform—energy & HEALTH 2024.") == [
        0.0,
        -0.4082482904638631,
        -0.20412414523193154,
        0.20412414523193154,
        0.20412414523193154,
        -0.20412414523193154,
        0.0,
        -0.8164965809277261,
    ]
